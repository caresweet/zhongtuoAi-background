"""鉴权覆盖面 —— 防止「新增 router 忘了挂鉴权」这类事故再发生。

🔴 为什么要有这个文件
    `main.py` 原先是一串裸 `app.include_router(x.router)`，加不加鉴权全靠人记得。
    实测结果：13 个 router 里**只有 auth 自己带鉴权** —— 报告列表全量返回、
    `/reports/{id}/download` 知道 id 就能下别人的报告，而**没有任何人发现**。

    现在 `main.py` 的装配默认挂 `get_current_user`，这个测试是它的**强制配套**：
    「要公开必须先改这里」。放开一个口子从此是一次 code review 事件，
    而不是某次疏忽。

🔴 为什么是行为断言而不是读 `route.dependant`
    FastAPI 0.138 把 `include_router` 的结果包成私有类 `_IncludedRouter`，
    router 级 dependencies **不会**烘进子路由的 `dependant` 里（实测 111 条路由
    里有 108 条读不出鉴权依赖，但运行时其实是被拦住的）。读内部结构会得出
    错误结论，所以这里**直接发请求看返回码**。
"""

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

# 允许**匿名**访问的接口 —— 精确路径，改动这里必须是一次有意识的行为。
PUBLIC_PATHS = {
    "/",                       # 根健康检查（门户探活）
    "/api/v1/health",          # 健康检查
    "/api/v1/public/stats",    # 门户首屏统计
    "/api/v1/auth/login",      # 登录
    "/api/v1/auth/register",   # 注册
    "/api/knowledge/chat",     # 门户客服问答（匿名**只读**：不落会话、不学习）
}


def _all_http_routes():
    """枚举应用内全部 HTTP 路由（递归展开 `_IncludedRouter`）。"""
    from app.main import app

    out = []

    def walk(router):
        for r in router.routes:
            sub = getattr(r, "original_router", None)
            if sub is not None:
                walk(sub)
            elif isinstance(r, APIRoute):
                out.append(r)

    walk(app)
    return out


def _norm_path(path: str) -> str:
    """`/x/{id:path}` → `/x/{id}`。

    FastAPI 内部用带转换器的写法，`openapi()` 会归一化掉 `:path` —— 比对前两边都要归一。
    """
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


def _probe_url(path: str) -> str:
    """把 `/x/{id}` 变成 `/x/probe`，让请求能匹配上路由（只为拿到鉴权判定）。"""
    return re.sub(r"\{[^}]+\}", "probe", path)


def test_route_enumeration_is_not_silently_empty():
    """先保证「枚举」本身可信，否则下面的断言都是空转。

    与 `openapi()` 交叉校验：如果哪天 FastAPI 换了内部结构导致枚举漏项，
    这里会直接失败，而不是「路由没被枚举到 → 测试全部通过」。
    """
    from app.main import app

    enumerated = {_norm_path(r.path) for r in _all_http_routes()}
    openapi_paths = {_norm_path(p) for p in app.openapi().get("paths", {})}

    assert len(enumerated) > 50, f"只枚举到 {len(enumerated)} 条路由，枚举逻辑可疑"
    missing = openapi_paths - enumerated
    assert not missing, (
        f"枚举漏了这些路径（FastAPI 内部结构可能变了，要修 _all_http_routes）："
        f"{sorted(missing)}"
    )


def test_public_whitelist_has_no_stale_entries():
    """白名单里不能有已经不存在的路径 —— 否则它只是「看起来放开了」。"""
    actual = {r.path for r in _all_http_routes()}
    stale = PUBLIC_PATHS - actual
    assert not stale, f"PUBLIC_PATHS 里有已不存在的路径，应删除：{sorted(stale)}"


def test_every_non_public_route_requires_login():
    """**核心断言**：除白名单外，任何一条接口不带 token 都必须 401。

    为什么必须是 401 而不是 422 —— 前端 `api/client.js` 的响应拦截器**只在
    401 时**清 token 跳登录；返回 422 的话用户看到的是「莫名其妙报错」。
    """
    from app.main import app

    client = TestClient(app)
    offenders = []

    for route in _all_http_routes():
        if route.path in PUBLIC_PATHS:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            resp = client.request(method, _probe_url(route.path))
            if resp.status_code != 401:
                offenders.append((method, route.path, resp.status_code))

    assert not offenders, (
        "以下接口**没有鉴权**（不带 token 也没被拦下），"
        "要么补进 main.py 的 USER_ROUTERS，要么明确写进 PUBLIC_PATHS：\n"
        + "\n".join(f"  {m:<6} {p:<55} → {s}" for m, p, s in offenders)
    )


@pytest.mark.parametrize("bad_header", ["Bearer garbage", "Basic abc", "garbage"])
def test_malformed_token_is_rejected(bad_header):
    """畸形/伪造的 token 必须是 401，不能 500（`int(sub)` 抛 ValueError 踩过）。"""
    from app.main import app

    resp = TestClient(app).get("/api/v1/dashboard/stats",
                               headers={"Authorization": bad_header})
    assert resp.status_code == 401, f"{bad_header!r} → {resp.status_code}，应为 401"
