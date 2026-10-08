"""「跳过」链路的请求载荷 —— 曾经的 422 就出在这里。

🔴 曾经的现象
    用户点「跳过（使用默认值）」→ 报错。根因不是业务逻辑，而是
    `WorkflowResumeRequest.data: Dict[str, str]` 与前端实际发送的
    `{"__skip__": true}`（**布尔**）不兼容：pydantic v2 不做 bool→str 强转，
    FastAPI 在**进入函数体之前**就返回 422 —— 所以后端日志里连一条痕迹都没有，
    排查时极难定位。

这个用例把「前端会发的几种载荷」逐一钉住，防止 schema 再被改窄。
"""

import pytest
from pydantic import ValidationError


def test_accepts_string_values():
    from app.schemas.report import WorkflowResumeRequest
    r = WorkflowResumeRequest(data={"contact_phone": "13900000000"})
    assert r.data["contact_phone"] == "13900000000"


def test_accepts_skip_sentinel_as_string():
    """前端现在发的是这个（字符串哨兵）。"""
    from app.schemas.report import WorkflowResumeRequest
    r = WorkflowResumeRequest(data={"__skip__": "1"})
    assert r.data["__skip__"] == "1"


def test_accepts_skip_sentinel_as_bool():
    """🔴 **回归点**：布尔哨兵也必须能过。

    前端历史上发的是 `{"__skip__": true}`；只要 schema 再被改回 `Dict[str, str]`，
    这条会立刻失败 —— 免得同一个坑踩第二次。
    """
    from app.schemas.report import WorkflowResumeRequest
    r = WorkflowResumeRequest(data={"__skip__": True})
    assert r.data["__skip__"] is True


def test_accepts_empty_and_default_payload():
    """空对象/缺省也要能过 —— 后端的空值哨兵（`{}` → `{'__skip__': True}`）依赖它。"""
    from app.schemas.report import WorkflowResumeRequest
    assert WorkflowResumeRequest(data={}).data == {}
    assert WorkflowResumeRequest().data == {}


def test_accepts_mixed_types():
    from app.schemas.report import WorkflowResumeRequest
    r = WorkflowResumeRequest(data={"a": "x", "n": 3, "flag": True})
    assert r.data["n"] == 3 and r.data["flag"] is True
