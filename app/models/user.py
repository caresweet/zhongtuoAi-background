"""SQLAlchemy ORM model for user authentication."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Company(Base):
    """公司实体 —— 报告里「公司信息」的归属单位（多租户的核心概念）。

    🔴 为什么公司要独立成表而不是塞进 users
        一家公司下会有多个账号（同一家咨询公司的几个员工），公司资料
        （简介/营业执照/资质证书/人员名册）应当**挂在公司上**，改一次全员生效。
        报告归属仍按**账号**（业主口径：每个账号只看自己生成的报告）。

    `is_internal=True` 的那一行是我们自己（江苏众拓）—— 它的简介/编写单位
    等文案由迁移从原先写死在装配器里的文本搬过来。外部用户的公司为 False，
    资料取不到时报告里显示「待填写」，**绝不回落成我们的**。
    """

    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    is_internal = Column(Boolean, default=False, nullable=False)
    short_name = Column(String(100), nullable=True, default="")

    # 报告前置「公司简介」区的正文（按空行分段）
    intro_text = Column(Text, nullable=True, default="")
    # 报告封面 / 编写单位 / 实施单位显示的单位名
    implement_unit = Column(String(255), nullable=True, default="")
    business_license_no = Column(String(100), nullable=True, default="")
    qualification_text = Column(Text, nullable=True, default="")
    address = Column(String(255), nullable=True, default="")
    contact = Column(String(100), nullable=True, default="")

    # 🔴 负责人（姓名 + 职务）—— 评审表「稳评实施单位·负责人 / 职务」两格的值。
    #    每家公司的负责人不同（内部是陈春/法定代表人，外部客户各有其人），
    #    所以必须**挂在公司行上**，按账号归属自动取；空则报告里显示「待填写」。
    #    以前这两格没有数据来源：弹窗不问、库里没字段，只能写死成「陈春 / 法人」，
    #    外部用户出报告会印上我们的负责人（业主 2026-09-28 指出）。
    leader_name = Column(String(100), nullable=True, default="")
    leader_title = Column(String(100), nullable=True, default="")

    status = Column(String(20), nullable=False, default="active")  # active, disabled
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f"<Company(id={self.id}, name={self.name!r}, internal={self.is_internal})>"


class User(Base):
    """Application user for authentication and authorization."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(100), nullable=True, default="")
    role = Column(String(20), nullable=False, default="user")  # admin, user

    # 归属公司 —— 报告里的公司信息全部按它解析；为空表示「外部用户还没填」
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    phone = Column(String(40), nullable=True, default="")

    # 自助注册的账号 `is_active=False` 待审核，管理员在后台上放行
    review_note = Column(String(255), nullable=True, default="")
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, nullable=True)

    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    last_login_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username!r}, role={self.role})>"
