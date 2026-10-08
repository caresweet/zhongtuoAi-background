"""Pydantic schemas for authentication endpoints."""
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    display_name: Optional[str] = None

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2 or len(v) > 50:
            raise ValueError("用户名长度需要 2-50 个字符")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码长度至少 6 个字符")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    user: "UserInfo"


class UserInfo(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: str
    is_superuser: bool = False
    created_at: Optional[datetime] = None
    # 🔴 前端据此判断「是不是自己人」（内部工具菜单显不显示）。
    #    权限是**两个独立的轴**：company_id 决定报告用哪家公司的资料；
    #    company_is_internal 决定能不能看知识库/校对/审批/账号管理。
    #    前端 `authStore.isStaff` = isAdmin || company_is_internal，
    #    必须与后端 `deps.require_staff` 同口径。
    company_id: Optional[int] = None
    company_is_internal: bool = False


class UserUpdateRequest(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    password: Optional[str] = None
