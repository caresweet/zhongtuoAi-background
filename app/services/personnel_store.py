"""人员库 —— 「人名 → 人员信息 + 证书图片」的**独立存储**，供报告查找与填充。

业主口径：
    「把图片和人员信息存入到单独的一个数据库上，做查找和填充功能，
      用户选择哪些人员就可以提取出哪些人员的信息。」

两张表（建在 knowledge_base.db）：

    company_personnel          人员信息（姓名/性别/年龄/学历/专业/职称/拟任角色/证书）
    company_personnel_images   人员图片（培训证书/职称证书/执业资格证书/证件照 …）

数据从哪来：
    · 人员信息  ← company_assets「公司专业技术人员配备」
    · 图片      ← storage/extracted_imgs 的公司素材 + asset_images，
                  由 personnel_certs 一次性视觉识别确定「这张图是谁的」（姓名是手写体）

对外两个查询：
    personnel_for(names)  → [{姓名, 性别, 年龄, 学历, 专业, 职称/职务, 拟任角色, 执业资格证书, ...}]
    images_for(names)     → [(图片路径, 图题)]
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 证书类型 → 展示用类别
_CERT_CATEGORIES = ("培训证书", "职称证书", "执业资格证书", "学历证书", "证件照", "其他")


def _db_path() -> Path:
    from app.config import settings
    return settings.DATA_DIR / "knowledge_base.db"


def _conn() -> Optional[sqlite3.Connection]:
    p = _db_path()
    if not p.exists():
        return None
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


# 内部公司 id 的兜底 —— 现存人员全部属于我们自己（江苏众拓）。
# 迁移时把老数据划到它名下；新库由 auth 迁移先种出 company id=1。
_INTERNAL_COMPANY_ID = 1


def ensure_tables() -> None:
    """建表（幂等）—— 新库走这里；老库的加列/重建约束走 `_migrate_personnel_company`。"""
    c = _conn()
    if not c:
        return
    try:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS company_personnel (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT NOT NULL,
            gender            TEXT DEFAULT '',
            age               TEXT DEFAULT '',
            education         TEXT DEFAULT '',
            major             TEXT DEFAULT '',
            title             TEXT DEFAULT '',
            role              TEXT DEFAULT '项目组成员',
            certs             TEXT DEFAULT '[]',
            has_wenshen_cert  INTEGER DEFAULT 0,
            sort_order        INTEGER DEFAULT 0,
            company_id        INTEGER NOT NULL DEFAULT 1,
            updated_at        TEXT DEFAULT '',
            UNIQUE(name, company_id)
        );
        CREATE TABLE IF NOT EXISTS company_personnel_images (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            personnel_id  INTEGER NOT NULL,
            image_name    TEXT DEFAULT '',
            category      TEXT DEFAULT '其他',
            image_path    TEXT DEFAULT '',
            image_data    BLOB,
            mime_type     TEXT DEFAULT '',
            source        TEXT DEFAULT '',
            company_id    INTEGER NOT NULL DEFAULT 1,
            updated_at    TEXT DEFAULT '',
            UNIQUE(personnel_id, image_name)
        );
        CREATE INDEX IF NOT EXISTS idx_cpi_pid ON company_personnel_images(personnel_id);
        """)
        c.commit()
    except Exception as e:
        logger.warning(f"人员库建表失败: {e}")
    finally:
        c.close()

    # 🔴 建表只保证**新库**结构对（CREATE TABLE IF NOT EXISTS 不会 ALTER 老表）。
    #    老库的加列 / 重建唯一约束 / 建索引统统交给它，且**新库也要跑**
    #    （索引在那里创建，不然新库永远没有索引）。
    _migrate_personnel_company()


def _migrate_personnel_company() -> None:
    """幂等迁移：人员归属公司 + 唯一约束改成 `(name, company_id)`。

    🔴 为什么要**重建表**
       老库的 `name TEXT NOT NULL UNIQUE` 是**全局唯一** —— 两家公司各有一个
       「张伟」就会撞。SQLite 不支持 ALTER 掉列级 UNIQUE，只能
       新建表 → 拷数据 → drop → rename。整段在一个事务里，失败整体回滚。

    🔴 现存人员默认划给内部公司（id=1）
       他们本来就是从我们自己的名册/证书扫描件收敛来的。

    幂等判据：看 `sqlite_master.sql` 里的唯一约束是否**已经**包含 company_id。
    """
    c = _conn()
    if not c:
        return
    try:
        def _table_sql(name: str) -> str:
            r = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            return (r["sql"] or "") if r else ""

        def _cols(name: str) -> set:
            return {r["name"] for r in c.execute(f"PRAGMA table_info({name})").fetchall()}

        # ── company_personnel_images：只加列，约束不用动 ──
        if _table_sql("company_personnel_images") and "company_id" not in _cols("company_personnel_images"):
            c.execute("ALTER TABLE company_personnel_images ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1")
            logger.warning("人员库迁移：company_personnel_images 加 company_id 列")

        # 索引**无论新旧库都要建**（新库的建表脚本里已不再包含它们）
        c.execute("CREATE INDEX IF NOT EXISTS idx_cpi_company ON company_personnel_images(company_id)")

        def _ensure_cp_index():
            """`company_personnel` 的公司索引。**重建路径必须在 rename 之后**再建，
            否则它会被 `DROP TABLE` 一起带走。"""
            c.execute("CREATE INDEX IF NOT EXISTS idx_cp_company ON company_personnel(company_id)")

        sql = _table_sql("company_personnel")
        if not sql:
            c.commit()
            return  # 表不存在 → 由 ensure_tables 直接建成新结构

        # 加列必须在重建/建索引之前（后面两条都引用这个列）
        if "company_id" not in _cols("company_personnel"):
            c.execute("ALTER TABLE company_personnel ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1")

        # 唯一约束已经带上 company_id → 已迁过，收工
        # 🔴 返回前必须 commit：上面的加列还没落盘
        if "unique(name,company_id)" in re.sub(r"\s+", "", sql).lower():
            _ensure_cp_index()
            c.commit()
            return

        before = c.execute("SELECT COUNT(*) AS n FROM company_personnel").fetchone()["n"]

        c.execute("""
            CREATE TABLE company_personnel_mig (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                gender            TEXT DEFAULT '',
                age               TEXT DEFAULT '',
                education         TEXT DEFAULT '',
                major             TEXT DEFAULT '',
                title             TEXT DEFAULT '',
                role              TEXT DEFAULT '项目组成员',
                certs             TEXT DEFAULT '[]',
                has_wenshen_cert  INTEGER DEFAULT 0,
                sort_order        INTEGER DEFAULT 0,
                company_id        INTEGER NOT NULL DEFAULT 1,
                updated_at        TEXT DEFAULT '',
                UNIQUE(name, company_id)
            )
        """)
        c.execute("""
            INSERT INTO company_personnel_mig
                (id, name, gender, age, education, major, title, role, certs,
                 has_wenshen_cert, sort_order, company_id, updated_at)
            SELECT id, name, gender, age, education, major, title, role, certs,
                   has_wenshen_cert, sort_order, company_id, updated_at
              FROM company_personnel
        """)
        c.execute("DROP TABLE company_personnel")
        c.execute("ALTER TABLE company_personnel_mig RENAME TO company_personnel")
        _ensure_cp_index()

        after = c.execute("SELECT COUNT(*) AS n FROM company_personnel").fetchone()["n"]
        if after != before:
            raise RuntimeError(f"人员库重建后行数不一致：{before} → {after}")
        c.commit()
        logger.warning(
            f"人员库迁移：company_personnel 已重建，唯一约束改为 (name, company_id)，"
            f"{after} 行全部归入内部公司 id={_INTERNAL_COMPANY_ID}"
        )
    except Exception as e:
        c.rollback()
        logger.error(f"人员库迁移失败（已回滚，数据未变）: {e}")
    finally:
        c.close()


def _norm_cert_category(cert_type: str, image_name: str = "") -> str:
    t = f"{cert_type or ''}{image_name or ''}"
    for k in ("培训证书", "职称证书", "执业资格证书", "学历证书", "证件照"):
        if k in t:
            return k
    if "培训" in t and "证书" in t:
        return "培训证书"
    if "证书" in t:
        return "其他"
    return "其他"


# ═══════════════════════════════════════════════════════════════
# 同步：把散落的 roster / 素材图收敛进人员库
# ═══════════════════════════════════════════════════════════════

def _load_roster(company_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """company_assets「公司专业技术人员配备」（按公司过滤）。

    名册是**我们自己的**投标资料；外部用户没有名册，靠 `POST /company/personnel/import`
    上传。传 None 时不过滤（仅供内部迁移脚本使用）。
    """
    c = _conn()
    if not c:
        return []
    try:
        _sql = ("SELECT content FROM company_assets "
                "WHERE (asset_type='人员' OR title LIKE '%人员%')")
        _params: list = []
        if company_id:
            _sql += " AND company_id = ?"
            _params.append(int(company_id))
        row = c.execute(_sql + " ORDER BY id LIMIT 1", _params).fetchone()
        if not row or not row["content"]:
            return []
        data = json.loads(row["content"])
        return data if isinstance(data, list) else []
    except Exception:
        return []
    finally:
        c.close()


def roster_names(company_id: Optional[int] = None) -> set:
    """公司名册（company_assets「公司专业技术人员配备」）上的人名集合。

    🔴 为什么需要这个：`sync_personnel_store` 第 ①b 步会把「只有证书扫描件、
       不在名册上的人」也补进 `company_personnel`（否则证书图会成为取不到的孤儿图）。
       于是人员库里混着两类人：**在职名册人员** 与 **仅有历史证书的人**。
       「选择报告人员」只该列前者 —— 实测库里 22 人里 14 人不在名册上，
       而参照件的稳评工作组只有 6～7 人。
    """
    try:
        return {str(p.get("姓名") or "").strip()
                for p in _load_roster(company_id) if isinstance(p, dict)}
    except Exception:
        return set()


def _image_candidates() -> List[Dict[str, str]]:
    """候选人员图片 → [{path, name, source}]（公司素材目录 + asset_images）。"""
    out: List[Dict[str, str]] = []
    from app.config import settings
    exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    for base in (settings.STORAGE_DIR / "extracted_imgs", settings.STORAGE_DIR / "images"):
        if not base.exists():
            continue
        for f in sorted(base.iterdir()):
            if f.suffix.lower() not in exts:
                continue
            nm = f.name
            if not any(h in nm for h in ("company", "stability_cert", "person_cert")):
                continue
            out.append({"path": str(f), "name": nm, "source": "extracted_imgs"})
    return out


def sync_personnel_store(rebuild_images: bool = True,
                         company_id: Optional[int] = None) -> Dict[str, int]:
    """从 roster + 素材图重建人员库。返回 {personnel, images} 计数。

    `company_id` 决定重建**哪家公司**：名册只对内部公司存在，外部用户走导入接口。
    传 None 时按内部公司（`_INTERNAL_COMPANY_ID`）处理，保持旧调用行为。
    """
    _cid = int(company_id or _INTERNAL_COMPANY_ID)
    ensure_tables()
    c = _conn()
    if not c:
        return {"personnel": 0, "images": 0}
    now = datetime.now().isoformat(timespec="seconds")
    try:
        # ── ① 人员信息 ──
        roster = _load_roster(_cid)
        for i, p in enumerate(roster):
            if not isinstance(p, dict):
                continue
            name = str(p.get("姓名") or "").strip()
            if not name:
                continue
            certs = [str(x) for x in (p.get("执业资格证书") or [])]
            has_ws = 1 if any(("社会稳定风险评估" in x) or ("稳评" in x) for x in certs) else 0
            c.execute("""
                INSERT INTO company_personnel
                    (name, gender, age, education, major, title, role, certs,
                     has_wenshen_cert, sort_order, company_id, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                -- 🔴 冲突键必须与**新**唯一约束一致 `(name, company_id)`：
                --    唯一约束已从「全局唯一」改成「公司内唯一」，
                --    这里再写 `ON CONFLICT(name)` 会直接报
                --    「does not match any PRIMARY KEY or UNIQUE constraint」。
                ON CONFLICT(name, company_id) DO UPDATE SET
                    gender=excluded.gender, age=excluded.age, education=excluded.education,
                    major=excluded.major, title=excluded.title, role=excluded.role,
                    certs=excluded.certs, has_wenshen_cert=excluded.has_wenshen_cert,
                    sort_order=excluded.sort_order, updated_at=excluded.updated_at
            """, (name, str(p.get("性别") or ""), str(p.get("年龄") or ""),
                  str(p.get("学历") or ""), str(p.get("专业") or ""),
                  str(p.get("职称/职务") or ""), str(p.get("拟任角色") or "项目组成员"),
                  json.dumps(certs, ensure_ascii=False), has_ws, i, _cid, now))
        c.commit()

        ids = {r["name"]: r["id"] for r in
               c.execute("SELECT id, name FROM company_personnel WHERE company_id = ?", (_cid,))}

        # ── ①b 只有证书扫描件、不在名册上的人（老员工/离职在职边界）也补进库，
        #         否则扫描件会成为取不到的孤儿图。
        try:
            from app.services.personnel_certs import load_index
            idx = load_index()
        except Exception:
            idx = {}
        extra = 0
        for e in idx.values():
            if not e.get("is_person_cert"):
                continue
            pn = str(e.get("person_name") or "").strip()
            if not pn or pn in ids:
                continue
            c.execute("""
                INSERT OR IGNORE INTO company_personnel
                    (name, gender, age, education, major, title, role, certs,
                     has_wenshen_cert, sort_order, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (pn, "", "", "", "", "", "项目组成员",
                  json.dumps([str(e.get("cert_type") or "证书")], ensure_ascii=False),
                  1, 1000 + extra, now))
            extra += 1
        c.commit()
        ids = {r["name"]: r["id"] for r in
               c.execute("SELECT id, name FROM company_personnel WHERE company_id = ?", (_cid,))}
        n_person = len(ids)

        # ── ② 图片：由视觉识别索引确定「这张证书是谁的」 ──
        n_img = 0
        if rebuild_images:
            try:
                c.execute("DELETE FROM company_personnel_images")
            except Exception:
                pass
            for e in idx.values():
                if not e.get("is_person_cert"):
                    continue
                pn = str(e.get("person_name") or "").strip()
                p = str(e.get("path") or "")
                if pn not in ids or not p or not Path(p).exists():
                    continue
                cat = _norm_cert_category(e.get("cert_type", ""), Path(p).name)
                try:
                    c.execute("""
                        INSERT OR REPLACE INTO company_personnel_images
                            (personnel_id, image_name, category, image_path, mime_type, source, updated_at)
                        VALUES (?,?,?,?,?,?,?)
                    """, (ids[pn], f"{pn} {e.get('cert_type') or cat}"[:60], cat, p,
                          "image/png" if p.lower().endswith(".png") else "image/jpeg",
                          "vision_index", now))
                    n_img += 1
                except Exception:
                    continue
            c.commit()
        else:
            n_img = c.execute("SELECT COUNT(*) FROM company_personnel_images").fetchone()[0]

        logger.info(f"[人员库] 同步完成：{n_person} 人 / {n_img} 张图片")
        return {"personnel": n_person, "images": n_img}
    except Exception as e:
        logger.warning(f"人员库同步失败: {e}")
        return {"personnel": 0, "images": 0}
    finally:
        c.close()


# ═══════════════════════════════════════════════════════════════
# 查询：按人名取信息 / 取图片
# ═══════════════════════════════════════════════════════════════

def personnel_for(names: Optional[List[str]], company_id: Optional[int]) -> List[Dict[str, Any]]:
    """按人名取**本公司**的人员信息；names 为空则返回本公司全部。

    🔴 `company_id` 是**必填**参数（故意不给默认值）：漏传会直接 `TypeError`，
       而不是像原来的 `personnel_for(None)` 那样**静默返回全库所有人** ——
       多租户下那等于把 A 公司的名册给 B 公司看。
       没有公司（外部用户还没填）时传 None/0 → 返回空列表。
    """
    if not company_id:
        return []
    ensure_tables()
    c = _conn()
    if not c:
        return []
    try:
        rows = c.execute(
            "SELECT * FROM company_personnel WHERE company_id = ? "
            "ORDER BY sort_order, id", (int(company_id),)).fetchall()
        if not rows:
            # 本公司还一个人都没有 → 尝试从名册同步一次（只对内部公司有意义）
            c.close()
            sync_personnel_store()
            c = _conn()
            if not c:
                return []
            rows = c.execute(
                "SELECT * FROM company_personnel WHERE company_id = ? "
                "ORDER BY sort_order, id", (int(company_id),)).fetchall()

        want = [str(n).strip() for n in (names or []) if str(n).strip()]
        out = []
        for r in rows:
            if want and r["name"] not in want:
                continue
            out.append({
                "姓名": r["name"], "性别": r["gender"], "年龄": r["age"],
                "学历": r["education"], "专业": r["major"], "职称/职务": r["title"],
                "拟任角色": r["role"],
                "执业资格证书": json.loads(r["certs"] or "[]"),
                "has_wenshen_cert": bool(r["has_wenshen_cert"]),
                "_id": r["id"],
            })
        # 按用户勾选顺序返回
        if want:
            out.sort(key=lambda p: want.index(p["姓名"]) if p["姓名"] in want else 999)
        return out
    except Exception as e:
        logger.warning(f"读取人员库失败: {e}")
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass


def images_for(names: Optional[List[str]],
               company_id: Optional[int],
               categories: Optional[Tuple[str, ...]] = None) -> List[Tuple[str, str]]:
    """按人名取**本公司**人员的图片 → [(路径, 图题)]，保持 names 顺序。

    🔴 同 `personnel_for`：`company_id` 必填，避免把别家公司的证书图带出来。
    """
    if not company_id:
        return []
    ensure_tables()
    c = _conn()
    if not c:
        return []
    try:
        want = [str(n).strip() for n in (names or []) if str(n).strip()]
        sql = ("SELECT p.name AS pname, i.image_name, i.category, i.image_path "
               "FROM company_personnel_images i JOIN company_personnel p ON p.id = i.personnel_id "
               "WHERE p.company_id = ?")
        params: list = [int(company_id)]
        if want:
            sql += " AND p.name IN (%s)" % ",".join("?" * len(want))
            params += want
        if categories:
            sql += " AND i.category IN (%s)" % ",".join("?" * len(categories))
            params += list(categories)
        sql += " ORDER BY i.id"
        rows = c.execute(sql, params).fetchall()

        out, seen, seen_hash = [], set(), set()
        for r in rows:
            p = str(r["image_path"] or "")
            if not p or not Path(p).exists() or p in seen:
                continue
            # 🔴 company_P30_rId8 与 stability_cert_02 是同一张证书的副本，按内容去重
            try:
                import hashlib
                h = hashlib.md5(open(p, "rb").read()).hexdigest()
            except Exception:
                continue
            if h in seen_hash:
                continue
            seen.add(p); seen_hash.add(h)
            cap = str(r["image_name"] or f'{r["pname"]} {r["category"]}')
            out.append((p, cap[:60]))
        if want:
            out.sort(key=lambda t: next((i for i, n in enumerate(want) if n in t[1]), 999))
        return out
    except Exception as e:
        logger.warning(f"读取人员图片失败: {e}")
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass


def all_personnel_names() -> List[str]:
    ensure_tables()
    c = _conn()
    if not c:
        return []
    try:
        return [r["name"] for r in c.execute(
            "SELECT name FROM company_personnel ORDER BY sort_order, id")]
    except Exception:
        return []
    finally:
        c.close()
