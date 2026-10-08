"""reclamation 域注册成功，且不影响既有两域。"""
from app.domains import get_domain, list_domains
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
