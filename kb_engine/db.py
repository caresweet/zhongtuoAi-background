"""db.py — 双类型知识库数据库管理

两个物理独立的 SQLite 文件：
  stability_kb.db  — 社会稳定风险评估报告库
  bidding_kb.db    — 招标投标文件库

每库统一 schema：
  templates          — 模板/用例文件 + 学习到的大纲
  regulations        — 规范/法规文件
  fixed_assets       — 固定资料（营业执照/人员证件/资质证书/财务/社保...）
  learned_chapters   — 从模板学习到的逐章写作指引
  generation_runs    — 生成会话 + 逐章内容 + 审核结果 + 对比结果
"""

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KB_DOMAIN_STABILITY = "stability"
KB_DOMAIN_BIDDING = "bidding"

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_DB_FILES = {
    KB_DOMAIN_STABILITY: "stability_kb.db",
    KB_DOMAIN_BIDDING: "bidding_kb.db",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    doc_role TEXT NOT NULL DEFAULT 'template',   -- template | example
    category TEXT DEFAULT '',
    file_path TEXT NOT NULL,
    outline_json TEXT,          -- 学习到的章节大纲 [{chapter,title,level,subsections:[...]}]
    style_notes TEXT,           -- 学习到的写作风格备注
    learned_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS regulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    doc_no TEXT DEFAULT '',
    source_path TEXT DEFAULT '',
    content_text TEXT DEFAULT '',
    key_clauses_json TEXT,      -- [{clause_no, summary}]
    domain_tag TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fixed_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type TEXT NOT NULL,       -- 营业执照|人员证件|资质证书|财务报告|社保纳税|法人证明|授权委托|承诺函|设备|业绩|其他
    title TEXT NOT NULL DEFAULT '',
    company TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    extracted_text TEXT DEFAULT '',
    structured_json TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_fa_type ON fixed_assets(asset_type);

CREATE TABLE IF NOT EXISTS learned_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    chapter_no INTEGER NOT NULL,
    chapter_title TEXT NOT NULL,
    level INTEGER DEFAULT 1,
    subsections_json TEXT,      -- [{no,title}]
    writing_guide TEXT,         -- AI 生成的逐章写作指引（写什么/要点/必备数据/句式风格）
    required_data_json TEXT,    -- 该章所需数据字段列表
    tables_json TEXT,           -- 该章包含的模板表格结构 [{idx,rows,cols,headers:[...],chapter_loc}]
    images_json TEXT,           -- 该章包含的模板图片位置 [{idx,para_text,chapter_loc}]
    sort_order INTEGER DEFAULT 0,
    learned_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (template_id) REFERENCES templates(id)
);

CREATE TABLE IF NOT EXISTS generation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,           -- uuid
    domain TEXT NOT NULL,
    project_name TEXT DEFAULT '',
    requirement TEXT DEFAULT '',
    outline_json TEXT,              -- 本次生成使用的大纲
    chapters_json TEXT,             -- [{chapter_no,title,markdown,status,review_result,attempts}]
    final_review_json TEXT,         -- 终审结果
    comparison_json TEXT,           -- 与模板对比结果
    output_path TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',  -- pending|generating|assembled|reviewed|completed|failed
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


class DualKB:
    """管理两个独立知识库的读写。"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conns: Dict[str, sqlite3.Connection] = {}

    # ── connection ──────────────────────────────────────────────
    def _path(self, domain: str) -> Path:
        fname = _DB_FILES.get(domain)
        if not fname:
            raise ValueError(f"未知知识库类型: {domain}（仅 stability / bidding）")
        return self.data_dir / fname

    def conn(self, domain: str) -> sqlite3.Connection:
        if domain not in self._conns:
            p = self._path(domain)
            c = sqlite3.connect(str(p))
            c.row_factory = sqlite3.Row
            c.executescript(_SCHEMA)
            self._conns[domain] = c
        return self._conns[domain]

    def close(self):
        for c in self._conns.values():
            try:
                c.close()
            except Exception:
                pass
        self._conns.clear()

    # ── templates ───────────────────────────────────────────────
    def upsert_template(
        self, domain: str, name: str, file_path: str,
        doc_role: str = "template", category: str = "",
        outline: Optional[List[dict]] = None,
        style_notes: str = "",
    ) -> int:
        c = self.conn(domain)
        outline_json = json.dumps(outline, ensure_ascii=False) if outline else None
        existing = c.execute(
            "SELECT id FROM templates WHERE name=? AND file_path=?", (name, file_path)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE templates SET outline_json=?, style_notes=?, learned_at=?, doc_role=?, category=? WHERE id=?",
                (outline_json, style_notes, datetime.now().isoformat(), doc_role, category, existing["id"]),
            )
            return existing["id"]
        cur = c.execute(
            "INSERT INTO templates(name,doc_role,category,file_path,outline_json,style_notes,learned_at) VALUES(?,?,?,?,?,?,?)",
            (name, doc_role, category, file_path, outline_json, style_notes, datetime.now().isoformat()),
        )
        c.commit()
        return cur.lastrowid

    def get_templates(self, domain: str, doc_role: Optional[str] = None) -> List[dict]:
        c = self.conn(domain)
        if doc_role:
            rows = c.execute(
                "SELECT * FROM templates WHERE is_active=1 AND doc_role=? ORDER BY id", (doc_role,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM templates WHERE is_active=1 ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_primary_template(self, domain: str) -> Optional[dict]:
        """取该库的第一个 template 角色模板。"""
        rows = self.get_templates(domain, doc_role="template")
        return rows[0] if rows else None

    # ── learned chapters ────────────────────────────────────────
    def save_learned_chapters(self, domain: str, template_id: int, chapters: List[dict]):
        c = self.conn(domain)
        c.execute("DELETE FROM learned_chapters WHERE template_id=?", (template_id,))
        for idx, ch in enumerate(chapters):
            c.execute(
                "INSERT INTO learned_chapters(template_id,chapter_no,chapter_title,level,subsections_json,writing_guide,required_data_json,tables_json,images_json,sort_order) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    template_id, ch.get("chapter_no", idx + 1), ch.get("title", ""),
                    ch.get("level", 1),
                    json.dumps(ch.get("subsections", []), ensure_ascii=False),
                    ch.get("writing_guide", ""),
                    json.dumps(ch.get("required_data", []), ensure_ascii=False),
                    json.dumps(ch.get("tables", []), ensure_ascii=False),
                    json.dumps(ch.get("images", []), ensure_ascii=False),
                    idx,
                ),
            )
        c.commit()

    def get_learned_chapters(self, domain: str, template_id: Optional[int] = None) -> List[dict]:
        c = self.conn(domain)
        if template_id:
            rows = c.execute(
                "SELECT * FROM learned_chapters WHERE template_id=? ORDER BY sort_order", (template_id,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM learned_chapters ORDER BY template_id, sort_order"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["subsections"] = json.loads(r["subsections_json"] or "[]")
            d["required_data"] = json.loads(r["required_data_json"] or "[]")
            d["tables"] = json.loads(r["tables_json"] or "[]")
            d["images"] = json.loads(r["images_json"] or "[]")
            # 映射 chapter_title → title（去掉开头数字前缀）
            raw_title = d.get("chapter_title", "")
            clean_title = re.sub(r"^\d{1,2}\s*", "", raw_title) if raw_title else ""
            d["title"] = clean_title or raw_title
            result.append(d)
        return result

    # ── regulations ─────────────────────────────────────────────
    def add_regulation(self, domain: str, title: str, content_text: str,
                       doc_no: str = "", source_path: str = "",
                       key_clauses: Optional[List[dict]] = None) -> int:
        c = self.conn(domain)
        cur = c.execute(
            "INSERT INTO regulations(title,doc_no,source_path,content_text,key_clauses_json) VALUES(?,?,?,?,?)",
            (title, doc_no, source_path, content_text,
             json.dumps(key_clauses or [], ensure_ascii=False)),
        )
        c.commit()
        return cur.lastrowid

    def get_regulations(self, domain: str) -> List[dict]:
        c = self.conn(domain)
        return [dict(r) for r in c.execute(
            "SELECT * FROM regulations WHERE is_active=1 ORDER BY id"
        ).fetchall()]

    # ── fixed assets ────────────────────────────────────────────
    def add_fixed_asset(self, domain: str, asset_type: str, title: str,
                        company: str = "", file_path: str = "",
                        extracted_text: str = "",
                        structured: Optional[dict] = None) -> int:
        c = self.conn(domain)
        cur = c.execute(
            "INSERT INTO fixed_assets(asset_type,title,company,file_path,extracted_text,structured_json) VALUES(?,?,?,?,?,?)",
            (asset_type, title, company, file_path, extracted_text,
             json.dumps(structured, ensure_ascii=False) if structured else None),
        )
        c.commit()
        return cur.lastrowid

    def get_fixed_assets(self, domain: str,
                         asset_types: Optional[List[str]] = None) -> List[dict]:
        c = self.conn(domain)
        if asset_types:
            ph = ",".join("?" * len(asset_types))
            rows = c.execute(
                f"SELECT * FROM fixed_assets WHERE is_active=1 AND asset_type IN ({ph}) ORDER BY id",
                asset_types,
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM fixed_assets WHERE is_active=1 ORDER BY id"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if r["structured_json"]:
                d["structured"] = json.loads(r["structured_json"])
            result.append(d)
        return result

    def search_fixed_assets(self, domain: str, keywords: str) -> List[dict]:
        """按关键词模糊搜索固定资料。"""
        c = self.conn(domain)
        kw = f"%{keywords}%"
        rows = c.execute(
            "SELECT * FROM fixed_assets WHERE is_active=1 AND (title LIKE ? OR asset_type LIKE ? OR extracted_text LIKE ?) ORDER BY id",
            (kw, kw, kw),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── generation runs ─────────────────────────────────────────
    def create_run(self, domain: str, run_id: str, project_name: str,
                   requirement: str, outline: List[dict]) -> int:
        c = self.conn(domain)
        cur = c.execute(
            "INSERT INTO generation_runs(run_id,domain,project_name,requirement,outline_json,status) VALUES(?,?,?,?,?,?)",
            (run_id, domain, project_name, requirement,
             json.dumps(outline, ensure_ascii=False), "generating"),
        )
        c.commit()
        return cur.lastrowid

    def update_run_chapters(self, domain: str, run_id: str, chapters: List[dict]):
        c = self.conn(domain)
        c.execute(
            "UPDATE generation_runs SET chapters_json=?, updated_at=datetime('now','localtime') WHERE run_id=?",
            (json.dumps(chapters, ensure_ascii=False), run_id),
        )
        c.commit()

    def update_run_status(self, domain: str, run_id: str, status: str,
                          output_path: str = ""):
        c = self.conn(domain)
        c.execute(
            "UPDATE generation_runs SET status=?, output_path=?, updated_at=datetime('now','localtime') WHERE run_id=?",
            (status, output_path, run_id),
        )
        c.commit()

    def save_final_review(self, domain: str, run_id: str, review: dict):
        c = self.conn(domain)
        c.execute(
            "UPDATE generation_runs SET final_review_json=?, status='reviewed', updated_at=datetime('now','localtime') WHERE run_id=?",
            (json.dumps(review, ensure_ascii=False), run_id),
        )
        c.commit()

    def save_comparison(self, domain: str, run_id: str, comparison: dict):
        c = self.conn(domain)
        c.execute(
            "UPDATE generation_runs SET comparison_json=?, updated_at=datetime('now','localtime') WHERE run_id=?",
            (json.dumps(comparison, ensure_ascii=False), run_id),
        )
        c.commit()

    def get_run(self, domain: str, run_id: str) -> Optional[dict]:
        c = self.conn(domain)
        r = c.execute("SELECT * FROM generation_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(r) if r else None
