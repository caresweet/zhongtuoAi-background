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


def test_get_domain_does_not_raise_when_registry_empty(monkeypatch):
    """契约：注册表为空（所有域 builder 都建造失败）时，get_domain 必须回落默认域，
    而不是把 _REGISTRY[...] 的 KeyError 泄漏给调用方 —— 那会把一次静默降级
    变成稳评主链路上的 500。

    这条测试锁死 _FALLBACK_CONFIG 的存在价值：谁删了它（或改回直接下标取值），这里即变红。
    """
    import app.domains.registry as reg
    from app.domains import DomainConfig

    def _boom():
        raise RuntimeError("模拟域建造失败")

    # 让所有 builder 失败并清空注册表，复现「全部建造失败 → 注册表为空」的真实状态。
    monkeypatch.setattr("app.domains.stability.build_stability_config", _boom)
    monkeypatch.setattr("app.domains.bidding.build_bidding_config", _boom)
    monkeypatch.setattr("app.domains.reclamation.build_reclamation_config", _boom)
    monkeypatch.setattr(reg, "_REGISTRY", {})

    cfg = reg.get_domain("stability")          # 不抛异常即为通过
    assert isinstance(cfg, DomainConfig)
    assert cfg.domain_id == "stability"
