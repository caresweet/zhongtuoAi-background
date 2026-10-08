"""资料门槛 + 座谈会 PDF 定位 —— 两条「用户没做什么就不该发生什么」的线。

🔴 为什么有这组用例

    ① **生成按钮零门槛**：`/workflow/start` 原先只判断「是否已在运行」，
       一个材料都没有也能一路跑完出 Word。正文（尤其第 11/13 章的风险清单与
       化解措施）全靠模型自由发挥。聊天那条路有 `has_real_data` 门槛，按钮这条路没有。

    ② **座谈会 PDF 永远找不到**：`find_symposium_pdf` 有两个各自都能让它
       恒返回空的错 ——
         · 关键词比的是 `basename(路径)`，而落盘名是 `uuid4().hex + ext`，
           「座谈会/问卷」只在 `original_name` 里；
         · 会话里存的是相对 storage 的路径，`os.path.exists` 在 cwd=backend/ 时恒假。
       两者合起来，就是界面上「问卷数据：未解析」永远不动的来源。
"""

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parent.parent


# ════════════════════════════════════════════════════════════
# ① 资料门槛
# ════════════════════════════════════════════════════════════

def _empty_state():
    return {"session_id": "s_empty"}


def _state_with_upload():
    return {"session_id": "s_full", "_uploaded_files": [{"path": "attachments/x.pdf",
                                                         "original_name": "预公告.pdf"}]}


def test_has_real_data_false_for_empty_session():
    from app.routers.report import _has_real_data
    assert _has_real_data(_empty_state()) is False
    # 只有下划线开头的内部键（用户什么都没填）
    assert _has_real_data({"filled_data": {"_locked_area": "100"}}) is False


def test_has_real_data_true_once_materials_arrive():
    from app.routers.report import _has_real_data
    assert _has_real_data(_state_with_upload()) is True
    assert _has_real_data({"filled_data": {"project_name": "某土地征收决策"}}) is True
    assert _has_real_data({}, attachments=["a.pdf"]) is True


def _run_start(state, monkeypatch):
    from app.routers import report as r

    class _Sess:
        pass

    sess = _Sess()
    sess.state = state

    monkeypatch.setattr(r.report_service, "get_session_for_user", lambda sid, user: sess)
    return asyncio.run(r.start_workflow("s1", r.WorkflowStartRequest(), object()))


def test_start_workflow_refuses_when_no_materials(monkeypatch):
    """**核心**：没有任何材料时，生成接口必须直接拒绝（400），而不是跑完给出报告。"""
    with pytest.raises(HTTPException) as e:
        _run_start(_empty_state(), monkeypatch)
    assert e.value.status_code == 400
    assert "上传" in str(e.value.detail), "提示要告诉用户去上传资料"


def test_start_workflow_gate_is_shared_with_chat_path():
    """两条路必须共用同一个判据 —— 内联第二份就会各自漂移。"""
    src = (BACKEND / "app/routers/report.py").read_text(encoding="utf-8")
    assert "_has_real_data(" in src
    # 旧的内联写法（`len(real_filled) >= 1 or ...`）不该再留一份
    assert "or len(uploaded) > 0" not in src, "聊天路径的内联判据没换掉，判据又分家了"


# ════════════════════════════════════════════════════════════
# ② 座谈会 PDF 定位
# ════════════════════════════════════════════════════════════

@pytest.fixture
def _fake_pdf():
    """在真实 STORAGE_DIR 下造一个 uuid 命名的 pdf（用完即删）。

    文件名必须是 uuid 形态 —— 这正是原实现找不到它的原因。
    """
    from app.config import settings
    d = Path(settings.STORAGE_DIR) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{uuid.uuid4().hex}.pdf"
    p.write_bytes(b"%PDF-1.4\n% fake\n")
    yield f"attachments/{p.name}"
    p.unlink(missing_ok=True)
    try:
        d.rmdir()          # 只在空的时候删得掉，不碰别人的文件
    except OSError:
        pass


def test_finds_symposium_pdf_by_original_name(_fake_pdf):
    """落盘名是 uuid、关键词只在 original_name 里 —— 也必须找得到。"""
    from app.services.survey_data import find_symposium_pdf
    state = {"_uploaded_files": [{"path": _fake_pdf, "original_name": "群众座谈会记录.pdf"}]}
    found = find_symposium_pdf(state)
    assert found, "座谈会 PDF 没找到（关键词比对或相对路径解析又退化了）"
    assert os.path.isabs(found), "返回的必须是绝对路径，否则调用方的 os.path.exists 又为假"
    assert os.path.exists(found)


def test_ignores_unrelated_pdf(_fake_pdf):
    """文件名不带座谈会/问卷关键词的 PDF 不能被误认成问卷来源。"""
    from app.services.survey_data import find_symposium_pdf
    state = {"_uploaded_files": [{"path": _fake_pdf, "original_name": "勘测定界报告.pdf"}]}
    assert find_symposium_pdf(state) == ""


def test_upload_triggers_auto_parse():
    """上传座谈会/问卷扫描件时自动排后台解析 —— 而不是等用户点按钮。"""
    src = (BACKEND / "app/routers/report.py").read_text(encoding="utf-8")
    i = src.find("def upload_attachment")
    body = src[i:i + 6000]
    assert "_bg_questionnaire" in body, "上传接口没挂自动解析"
    assert "looks_like_symposium_pdf" in body


def test_manual_questionnaire_endpoints_are_gone():
    """界面上的「📊 解析座谈会问卷」已删 —— 那两个接口不该再留成孤儿。"""
    src = (BACKEND / "app/routers/knowledge.py").read_text(encoding="utf-8")
    for dead in ("_QUESTIONNAIRE_JOBS", "_resolve_questionnaire_pdf",
                 "/questionnaire/parse", "/questionnaire/result"):
        assert dead not in src, f"`{dead}` 是删除后残留的死代码"
