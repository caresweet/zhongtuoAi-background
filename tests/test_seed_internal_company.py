"""`_seed_internal_company` 幂等 —— 第二家我司机构只种一次，且不改变既有查询结果。

背景：`_seed_internal_company` 补录了「江苏众拓测绘有限公司」。它**按 name 判重**
（不是照抄那条 `WHERE is_internal = 1 LIMIT 1` —— 照抄只会查到第一家去补空字段）。
本测试把两条不变量钉住：
  1. 连跑两次，「江苏众拓测绘有限公司」恰好一行；
  2. `SELECT ... WHERE is_internal = 1 LIMIT 1` 仍选中**第一家**（江苏众拓项目代理咨询）。
"""
from sqlalchemy import create_engine, text

from app.database.auth_db import _seed_internal_company
from app.models.user import Base
from app.services.internal_company import INTERNAL_COMPANY_NAME

_SECOND_COMPANY = "江苏众拓测绘有限公司"


def _new_db_with_seed(db_file):
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _seed_internal_company(conn)
    return engine


def test_seed_is_idempotent_and_internal_lookup_unchanged(tmp_path):
    db_file = tmp_path / "auth.db"
    engine = _new_db_with_seed(db_file)
    # 第二次 —— 服务每次启动都会跑一遍
    with engine.begin() as conn:
        _seed_internal_company(conn)

    with engine.connect() as conn:
        # 1) 第二家公司恰好一行
        rows = conn.execute(text(
            "SELECT name, is_internal FROM companies WHERE name = :n"
        ), {"n": _SECOND_COMPANY}).fetchall()
        assert len(rows) == 1, "第二家公司应恰好一行（幂等）"
        assert rows[0][1] in (1, True), "第二家公司也应是内部公司"

        # 2) 「取第一家内部公司」的查询结果不变（仍是先建的那家）
        first = conn.execute(text(
            "SELECT name FROM companies WHERE is_internal = 1 LIMIT 1")).scalar()
        assert first == INTERNAL_COMPANY_NAME

    engine.dispose()
