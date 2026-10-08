"""reclamation 域注册成功，且不影响既有两域。"""
from app.domains import detect_domain, get_domain, list_domains
from app.domains.registry import DEFAULT_DOMAIN


def test_reclamation_registered():
    cfg = get_domain("reclamation")
    assert cfg.domain_id == "reclamation"
    assert cfg.display_name
    assert cfg.chapter_count == 10


def test_reclamation_has_own_rag_domain_and_collection():
    cfg = get_domain("reclamation")
    assert cfg.rag_domain == "reclamation"
    assert cfg.default_collection.startswith("kb_reclamation")


def test_existing_domains_untouched():
    ids = {d.domain_id for d in list_domains()}
    assert {"stability", "bidding", "reclamation"} <= ids
    assert get_domain(None).domain_id == DEFAULT_DOMAIN == "stability"


def test_unknown_domain_falls_back_to_default():
    assert get_domain("no_such_domain").domain_id == "stability"


def test_stability_messages_not_misclassified_as_reclamation():
    """护栏：detect_domain 里非默认域的关键词会压过稳评（默认域）。

    所以 reclamation.classify_keywords 只能收「复垦专有」表述；宽泛词
    （如「临时用地」「复垦」）会把稳评对话误判成复垦。以下稳评味儿句子必须仍归 stability。
    """
    stability_messages = [
        "本项目为土地征收社会稳定风险评估，涉及临时用地补偿",
        "稳评报告：临时用地与复垦保障措施",
        "拟征收土地社会稳定风险评估公示",
    ]
    for msg in stability_messages:
        assert detect_domain(msg) == "stability", msg


def test_reclamation_own_expressions_detected():
    """复垦专有表述仍应被判为 reclamation。"""
    reclamation_messages = [
        "编制土地复垦方案",
        "复垦报告编制",
        "复垦方向分析",
    ]
    for msg in reclamation_messages:
        assert detect_domain(msg) == "reclamation", msg
