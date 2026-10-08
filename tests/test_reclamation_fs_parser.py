"""可研解析：抽结构化字段；JSON 不合法时容错；文本过短不调用模型。

注：**本仓未装 pytest-asyncio**，异步用例一律用 `asyncio.run`，不用
`@pytest.mark.asyncio`（仓库既有约定）。
"""
import asyncio
import json

from app.services.reclamation_fs_parser import ReclamationFsParser

# 真实可研正文远长于门限；用一段够长的样例文本，避免撞上「文本过短直接返回」
_LONG_TEXT = (
    "张兴矿区采输卤扩建项目主要建设内容包括新增7组采卤接替井组、管网升级改造，"
    "计划从2026年1月底开始，到2030年4月底竣工。临时用地作为施工便道使用，"
    "面积为0.2354公顷，施工时先进行表土剥离，分别铺设8cm垫层和10cm混凝土层。"
)


class _FakeLLM:
    """假 LLM —— 签名必须与真实 `LLMService.chat` 一致（收 messages 列表）。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.is_available = True

    async def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.reply


def test_parse_extracts_fields():
    llm = _FakeLLM(json.dumps({
        "project_name": "某扩建项目", "build_unit": "某有限公司",
        "location": "淮安市淮安区石塘镇", "build_content": "新增7组井组",
        "build_period": "2026年1月至2030年4月",
        "process": "先表土剥离，铺8cm垫层和10cm混凝土层",
    }, ensure_ascii=False))
    out = asyncio.run(ReclamationFsParser(llm).parse(_LONG_TEXT))
    assert out["project_name"] == "某扩建项目"
    assert out["process"].startswith("先表土剥离")
    # 调用形式必须是 messages 列表（与真实接口一致），不是裸字符串
    assert isinstance(llm.calls[0], list)
    assert llm.calls[0][0]["role"] == "user"


def test_malformed_json_recovers_from_code_fence():
    llm = _FakeLLM('好的，结果如下：\n```json\n{"project_name": "X"}\n```')
    out = asyncio.run(ReclamationFsParser(llm).parse(_LONG_TEXT))
    assert out.get("project_name") == "X"


def test_garbage_reply_returns_empty_not_raises():
    llm = _FakeLLM("我无法从这段文字中提取信息。")
    assert asyncio.run(ReclamationFsParser(llm).parse(_LONG_TEXT)) == {}


def test_short_text_short_circuits_without_llm_call():
    """文本过短不值得调用模型（与 tender_spec_analyzer 的门限一致）。"""
    llm = _FakeLLM("{}")
    assert asyncio.run(ReclamationFsParser(llm).parse("   ")) == {}
    assert llm.calls == []


def test_unavailable_llm_returns_empty_without_call():
    llm = _FakeLLM("{}")
    llm.is_available = False
    assert asyncio.run(ReclamationFsParser(llm).parse(_LONG_TEXT)) == {}
    assert llm.calls == []
