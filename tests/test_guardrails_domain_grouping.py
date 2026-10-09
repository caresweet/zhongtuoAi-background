"""质检规则按域分组：稳评独有规则不作用于复垦；不传 domain 行为不变。"""
import inspect

from app.validation.content_guardrails import (
    STABILITY_ONLY_CHECKS,
    full_guardrail_check,
)


def test_signature_has_domain_with_stability_default():
    sig = inspect.signature(full_guardrail_check)
    assert "domain" in sig.parameters
    assert sig.parameters["domain"].default == "stability"


def test_stability_only_checks_are_declared():
    """稳评独有的检查必须显式登记，否则门控无从下手。"""
    assert "numeric_ranges" in STABILITY_ONLY_CHECKS
    assert "data_validity" in STABILITY_ONLY_CHECKS


def test_stability_specific_rule_does_not_fire_for_reclamation():
    """含「支持率」的文本：稳评域要报，复垦域不许报。

    ⚠️ `full_guardrail_check` 返回的是 **dict**（键：passed / total_issues /
    blocking / data_validity / fabricated / numeric_ranges / regulations /
    materials），不是列表 —— 断言要按键取。
    """
    text = "本项目问卷调查支持率为98%，反对率2%。"
    as_stability = full_guardrail_check(text, domain="stability")
    as_reclamation = full_guardrail_check(text, domain="reclamation")
    assert as_stability["numeric_ranges"], as_stability
    assert not as_reclamation["numeric_ranges"], as_reclamation


def test_default_domain_behaves_like_before():
    """不传 domain 时与改动前一致 —— 稳评的护栏。"""
    text = "本项目问卷调查支持率为98%，反对率2%。"
    assert full_guardrail_check(text) == full_guardrail_check(text, domain="stability")


def test_stability_total_issues_immune_to_domain_guardrails(monkeypatch):
    """稳评调用**读不到** `DomainConfig.guardrails` —— 逐字节等于改动前。

    `DomainConfig.guardrails` 在新分支出现前从未被稳评读过。这里往稳评域塞一条
    **逐字出现在正文里**的护栏：若门控失效（稳评也跑该块），`total_issues` 会 +1；
    门控有效则整份结果与基线逐字节相同。反证给复垦域同一护栏 —— 它**会**被计入。
    """
    import app.domains as domains_pkg

    text = "社会稳定风险评估流程图"
    stab_baseline = full_guardrail_check(text, domain="stability")
    rec_baseline = full_guardrail_check(text, domain="reclamation")

    class _Cfg:
        guardrails = [text]                 # 故意逐字命中正文

    monkeypatch.setattr(domains_pkg, "get_domain", lambda *a, **k: _Cfg())

    stab_after = full_guardrail_check(text, domain="stability")
    rec_after = full_guardrail_check(text, domain="reclamation")

    # 稳评：注入的护栏命中正文，但整份结果与 total_issues 均逐字节不变。
    assert stab_after == stab_baseline
    assert stab_after["total_issues"] == stab_baseline["total_issues"]
    # 反证：同一护栏在复垦域**会**被计入 —— 证明门控存在且只跳过稳评。
    assert rec_after["total_issues"] == rec_baseline["total_issues"] + 1
