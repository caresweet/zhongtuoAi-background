"""会话状态通道 —— 界面与脚本**共用同一份事实源**。

🔴 为什么要有这个模块
────────────────────────────────────────────────────────────────────────
`storage/tmp/review_state.pkl` 曾经是脚本自己的「影子状态」，与界面会话
（`ReportService` 内存 + `storage/sessions.json`）**互不同步**。后果实测过一次：

    用户在界面弹窗里填了 15 个日期（现场公示 9月3日、实地勘察 9月5日…），
    脚本出报告时全丢 —— 报告里印的还是材料识别出来的旧日期（4月13日、6月4日）。
    用户在成品里看到的是错日期，而且**看不出来是错的**。

根因不是某一处代码写错，是「两份状态」这个结构本身。所以这里把访问收敛成
一条通道：**服务器是唯一写者**，脚本一律按 `session_id` 走 HTTP 读写。

    ┌──────────────────────────────┐
    │  ReportService 会话           │  ←── 界面（生成/弹窗填写）
    │  （唯一写者：uvicorn 进程）     │  ←── 脚本（--session <id>）
    └──────────────────────────────┘
                 ↓ 持久化
          storage/sessions.json
                 ↓ 填即归档 / 生成完成归档
          data/history_reports.db

🔴 字段优先级（本次事故就是这条规则没被任何地方强制）
        `_locked_<key>`（用户填/确认）  >  材料识别  >  模板默认
   服务端 `/state` 的写入会**拒绝覆盖** `_locked_` 的键，见 `routers/report.py`。

没有 `--session` 时的降级：读离线 pkl（`storage/tmp/review_state.pkl`），
但会**大声警告**并打印该 pkl 的来源会话 —— 不会再出现「不知道这份状态是谁的」。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_API = os.environ.get("REPORT_API_BASE", "http://localhost:8000")

# 🔴 **JSON 往返会把整数键变成字符串** —— 状态里有几个 map 是按**章号（int）**索引的：
#       `{1: {...}}` 经 HTTP 一圈回来就成了 `{"1": {...}}`，
#       而消费方写的是 `chapters.get(6)` / `sorted(chapters.keys())` ——
#       前者取不到、后者直接 `TypeError: '<' not supported between int and str`。
#    实测就是这么炸的（全本重跑 --session 时崩在 master_orchestrator._summarize_previous）。
#    所以**载入与写回两端都要归一化**，别指望运输层保型别。
_INT_KEYED_MAPS = (
    "chapters",            # 章号 → {markdown, status}
    "_extracted_tables",   # 章号 → PDF 提取表
    "_chapter_image_map",  # 章号 → 图片列表
    "_chapter_gaps",       # 章号 → 缺口清单（遁词检测 + 缺数据）
)


def normalize_int_keys(state: dict) -> dict:
    """把 `_INT_KEYED_MAPS` 里的整数键从字符串还原成 int（幂等）。"""
    if not isinstance(state, dict):
        return state
    for k in _INT_KEYED_MAPS:
        v = state.get(k)
        if isinstance(v, dict) and v:
            state[k] = {(int(x) if str(x).lstrip("-").isdigit() else x): y
                        for x, y in v.items()}
    return state


def get_state(session_id: str, api: str = "", timeout: int = 120) -> Optional[dict]:
    """按会话 id 从后端读状态。失败返回 None（调用方决定是报错还是降级）。"""
    import urllib.request
    url = f"{(api or DEFAULT_API).rstrip('/')}/api/v1/reports/generate/{session_id}/state"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        st = (d.get("data") or {}).get("state") or {}
        return normalize_int_keys(st) or None
    except Exception as e:
        logger.warning(f"读取会话 {session_id} 状态失败: {e}")
        return None


def put_state(session_id: str, state: dict, api: str = "", timeout: int = 120) -> bool:
    """把生成结果写回**同一个会话**。

    只回写脚本产出的那几类键；`filled_data` 由服务端做带 `_locked_` 保护的浅合并
    （用户填的值不会被脚本覆盖）。
    """
    import urllib.request
    keys = ("filled_data", "chapters", "generated_sections", "output_path",
            "outline_list", "selected_personnel", "selected_cert_personnel",
            "_questionnaire_result", "_chapter_gaps")
    payload = {"state": {k: state.get(k) for k in keys if state.get(k) is not None}}
    url = f"{(api or DEFAULT_API).rstrip('/')}/api/v1/reports/generate/{session_id}/state"
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception as e:
        logger.warning(f"写回会话 {session_id} 失败: {e}")
        return False


def load_state(session_id: str = "", api: str = "",
               cache_path: Optional[Path] = None) -> dict:
    """**脚本统一的入口**：有 session_id 走会话，没有才回退离线 pkl。

    回退时会明确警告 —— 离线缓存与界面会话不是同一份状态，
    用户在界面上填的值不会进报告。
    """
    import pickle
    if session_id and session_id.strip():
        st = get_state(session_id.strip(), api)
        if st:
            print(f"🔗 从界面会话载入状态：{session_id.strip()}")
            return st
        raise RuntimeError(
            f"读取会话 {session_id.strip()} 失败（后端没起来？会话已过期？）\n"
            f"   接口：GET {(api or DEFAULT_API).rstrip('/')}"
            f"/api/v1/reports/generate/<sid>/state")

    p = Path(cache_path) if cache_path else Path("storage/tmp/review_state.pkl")
    state = normalize_int_keys(pickle.loads(p.read_bytes()))
    _from = str(state.get("_source_session") or "").strip()
    _at = str(state.get("_cache_written_at") or "").strip()
    print(f"⚠️  未指定 --session，改用脚本离线缓存 {p}")
    print("    ⚠️ 这份缓存与界面会话**不是同一份状态** —— 界面上填的值不会进报告！")
    print("    ⚠️ 要带上界面填的值，请加： --session <会话id>")
    if _from:
        print(f"    （该缓存上次是从会话 {_from} 导出的" + (f"，{_at}）" if _at else "）"))
    return state


def save_cache(state: dict, session_id: str = "", cache_path: Optional[Path] = None) -> None:
    """写离线缓存，并打上来源标记（下次谁用它，日志会说清它是谁的）。"""
    import pickle
    import time
    p = Path(cache_path) if cache_path else Path("storage/tmp/review_state.pkl")
    state["_source_session"] = (session_id or "").strip() or state.get("_source_session", "")
    state["_cache_written_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pickle.dumps(state))
