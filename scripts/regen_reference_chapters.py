#!/usr/bin/env python3
"""按「三档分级」重跑参照件轨道的章节（第2/5/7章）。

用法：
    python3 scripts/regen_reference_chapters.py 2 5 7
    python3 scripts/regen_reference_chapters.py 2 5 7 --session <会话id>   # 🔴 以界面会话为准
"""
import asyncio, pickle, re, sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
import os; os.chdir(str(BACKEND))

from app.services.llm_service import LLMService
from app.agent.agents.chapters import get_chapter_agent
from app.services.survey_data import merge_survey_into_state
from app.services.reference_corpus import render_reference_markdown
from app.services.reference_llm import _copy_ratio, _protected

PHONE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
IMG = re.compile(r'!\[')

def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    _sid = ""
    _api = ""
    if "--session" in sys.argv:
        _i = sys.argv.index("--session")
        if _i + 1 < len(sys.argv):
            _sid = sys.argv[_i + 1]
    if "--api" in sys.argv:
        _i = sys.argv.index("--api")
        if _i + 1 < len(sys.argv):
            _api = sys.argv[_i + 1]
    nums = [int(x) for x in args if x.strip().isdigit()] or [2, 5, 7]
    # 🔴 会话状态统一入口（见 `app/services/session_io.py`）
    from app.services.session_io import load_state, save_cache, put_state
    ST = BACKEND / "storage/tmp/review_state.pkl"
    st = load_state(_sid, _api, ST)
    merge_survey_into_state(st)
    filled = st.get("filled_data") or {}
    llm = LLMService()
    for n in nums:
        agent = get_chapter_agent(n, llm_service=llm)
        s = {"filled_data": dict(filled), "chapters": {}, "session_id": st.get("session_id", "")}
        asyncio.run(agent.run(s, asyncio.Queue()))
        cd = s["chapters"][n]
        md = cd["markdown"]
        st.setdefault("chapters", {})[n] = cd
        (BACKEND / f"storage/output/ch{n}.md").write_text(md, encoding="utf-8")
        sk = render_reference_markdown(n, filled).markdown
        h2 = sum(1 for l in md.split("\n") if l.strip().startswith("### "))
        h3 = sum(1 for l in md.split("\n") if l.strip().startswith("#### "))
        print(f"第{n}章 status={cd.get('status')} {len(md)}字（骨架 {len(sk)}）"
              f" 二级{h2} 三级{h3} 图{len(IMG.findall(md))}"
              f" 条文段{len(_protected(sk))} 照抄率{_copy_ratio(md, sk):.2f}"
              f" 手机号{len(PHONE.findall(md))} 污点{cd.get('status')}")
        for it in (cd.get('_issues') or []):
            print("     ⚠️", it)
    save_cache(st, _sid, ST)
    if _sid.strip():
        put_state(_sid.strip(), st, _api)
    print("state 已更新")

if __name__ == "__main__":
    main()
