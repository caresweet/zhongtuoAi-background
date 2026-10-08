"""SQLAlchemy session for auth (users) database — uses shared engine pool.

Uses a shared PostgreSQL server in production (via AUTH_DB_URL) or falls back
to a local SQLite file for development.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import auth_async_session as async_session
from app.database.engine import _auth_engine as engine


async def get_auth_db() -> AsyncSession:
    """Dependency: yield an async session for the auth database."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_auth_db():
    """Create all tables in the auth database, then run idempotent migrations."""
    from app.models.user import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 🔴 create_all 只建**新表**，从不 ALTER 已存在的表 —— 老库（开发机上的
        #    auth.db）不会自动获得新列。下面三步照抄 knowledge_db.py 的幂等迁移范式。
        await conn.run_sync(_migrate_user_columns)
        await conn.run_sync(_seed_internal_company)
        await conn.run_sync(_promote_bootstrap_admin)


def _migrate_user_columns(conn):
    """幂等迁移：给已存在的 `users` 表补多租户/审核相关列。"""
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    if not inspector.has_table("users"):
        return
    cols = {c["name"] for c in inspector.get_columns("users")}
    additions = {
        "company_id": "ALTER TABLE users ADD COLUMN company_id INTEGER",
        "phone": "ALTER TABLE users ADD COLUMN phone VARCHAR(40)",
        "review_note": "ALTER TABLE users ADD COLUMN review_note VARCHAR(255)",
        "approved_at": "ALTER TABLE users ADD COLUMN approved_at DATETIME",
        "approved_by": "ALTER TABLE users ADD COLUMN approved_by INTEGER",
    }
    for column, ddl in additions.items():
        if column not in cols:
            conn.execute(text(ddl))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_company ON users(company_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active)"))


def _seed_internal_company(conn):
    """种入我们自己（江苏众拓）的公司行 —— 文案来自 `services/internal_company.py`。

    幂等：按 `is_internal=1` 查，已存在就只补齐为空的字段（不覆盖人工改过的值）。

    另补入第二家我司机构（江苏众拓测绘有限公司）—— **单独按 `name` 判重**，
    与上面那条 `is_internal` 逻辑并列、互不干扰（见下方说明）。
    """
    from sqlalchemy import text as _text
    from app.services.internal_company import internal_company_defaults

    d = internal_company_defaults()
    row = conn.execute(_text(
        "SELECT id, intro_text, implement_unit FROM companies WHERE is_internal = 1 LIMIT 1"
    )).fetchone()

    if row is None:
        conn.execute(_text(
            "INSERT INTO companies (name, is_internal, short_name, intro_text, "
            "implement_unit, status, created_at, updated_at) "
            "VALUES (:name, 1, :short_name, :intro_text, :implement_unit, 'active', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), d)
    else:
        # 已存在：只给空字段补默认值，**不覆盖**人工编辑过的内容
        sets, params = [], {}
        if not (row[1] or "").strip():
            sets.append("intro_text = :intro_text"); params["intro_text"] = d["intro_text"]
        if not (row[2] or "").strip():
            sets.append("implement_unit = :implement_unit"); params["implement_unit"] = d["implement_unit"]
        if sets:
            params["id"] = row[0]
            conn.execute(_text(f"UPDATE companies SET {', '.join(sets)} WHERE id = :id"), params)

    # 我司第二家机构 —— 复垦方案的出具单位常见为测绘公司。
    # ⚠️ 这里**按 name 判重**，不是按 is_internal：本函数开头那条
    #    `SELECT ... WHERE is_internal = 1 LIMIT 1` 是「只有一家内部公司」时代的写法，
    #    照它加第二家只会查到第一家去补空字段，第二家永远进不来。
    #    ⚠️ 必须放在 if/else **之后**：若放在开头那条查询之前，新插入的行 id 更小，
    #       `LIMIT 1` 就会选中它，从而改变既有 `is_internal` 查询的选择结果。
    conn.execute(_text(
        "INSERT INTO companies (name, is_internal, short_name, status, "
        "created_at, updated_at) "
        "SELECT :name, 1, :short_name, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM companies WHERE name = :name)"
    ), {"name": "江苏众拓测绘有限公司", "short_name": "众拓测绘"})


def _promote_bootstrap_admin(conn):
    """把引导管理员提权，并给它挂上内部公司。

    🔴 不做提权 = **上线当天自锁**：实测开发库里的 `admin` 是
       `role='user'` / `is_superuser=0`（由 deploy.sh 早期版本建出来）。
       知识库改成管理员专属后，唯一的运维账号会把自己关在门外。

    🔴 顺带给它挂内部公司 —— 早期账号是在「公司」这个概念存在之前建的，
       不挂的话管理员的报告里公司信息会显示「待填写」。
       **只动这一个引导账号**：绝不能写成「把所有没公司的账号都挂过去」，
       那会把「还没填公司信息」的外部用户误挂成我们公司（实测踩过：
       新建的无公司账号被自动挂上了江苏众拓，人员名单也跟着串了）。
    """
    import logging
    from sqlalchemy import text as _text

    res = conn.execute(_text(
        "UPDATE users SET role='admin', is_superuser=1, is_active=1 "
        "WHERE username='admin' AND (is_superuser IS NULL OR is_superuser = 0)"
    ))
    if res.rowcount:
        logging.getLogger(__name__).warning(
            f"已将引导账号 'admin' 提权为管理员（{res.rowcount} 行）—— "
            f"否则知识库设为管理员专属后无人可登录。"
        )

    row = conn.execute(_text(
        "SELECT id FROM companies WHERE is_internal = 1 LIMIT 1"
    )).fetchone()
    if row is not None:
        conn.execute(_text(
            "UPDATE users SET company_id = :cid "
            "WHERE username = 'admin' AND company_id IS NULL"
        ), {"cid": row[0]})
