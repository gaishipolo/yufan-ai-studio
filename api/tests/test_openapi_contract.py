"""接口契约文件的一致性测试。

`docs/openapi.json`、`docs/API.md`、`studio/src/shared/api/schema.ts` 都是
`app export-openapi` 生成物。它们最容易出的问题不是「生成得不对」，
而是**改了接口忘了重新生成** —— 前端照着旧文档写完，联调时才发现对不上。

这些测试把「重新生成」变成硬约束：只要代码里的接口变了而文件没更新，CI 就红。
修法永远是 `app export-openapi`，不要手工改那三个文件。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from openapi_export import (
    DEFAULT_JSON,
    DEFAULT_MD,
    DEFAULT_TS,
    build_spec,
    render_markdown,
    render_typescript,
)

PUBLIC_PATHS = ("/api/v1/identity/login", "/api/v1/health", "/api/v1/health/ready")


# --------------------------------------------------------------------------- #
# 1. 生成物与代码同步
# --------------------------------------------------------------------------- #
def test_openapi_json_is_up_to_date() -> None:
    """docs/openapi.json 必须等于当前代码的 OpenAPI。"""
    assert DEFAULT_JSON.exists(), "缺少 docs/openapi.json，先跑 app export-openapi"
    on_disk = json.loads(DEFAULT_JSON.read_text(encoding="utf-8"))
    assert on_disk == build_spec(), (
        "docs/openapi.json 与代码不一致 —— 运行 `app export-openapi` 重新生成"
    )


def test_api_markdown_is_up_to_date() -> None:
    assert DEFAULT_MD.exists(), "缺少 docs/API.md，先跑 app export-openapi"
    expected = render_markdown(build_spec())
    assert DEFAULT_MD.read_text(encoding="utf-8") == expected, (
        "docs/API.md 与代码不一致 —— 运行 `app export-openapi`"
    )


def test_typescript_schema_is_up_to_date() -> None:
    assert DEFAULT_TS.exists(), "缺少 studio/src/shared/api/schema.ts，先跑 app export-openapi"
    expected = render_typescript(build_spec())
    assert DEFAULT_TS.read_text(encoding="utf-8") == expected, (
        "前端 schema.ts 与代码不一致 —— 运行 `app export-openapi`"
    )


# --------------------------------------------------------------------------- #
# 2. 鉴权标注正确（前端据此判断哪些接口要带令牌）
# --------------------------------------------------------------------------- #
def test_bearer_scheme_is_declared() -> None:
    schemes = build_spec()["components"].get("securitySchemes") or {}
    assert "bearerAuth" in schemes, "OpenAPI 缺 securitySchemes，导入 Apifox 后看不到要带令牌"
    assert schemes["bearerAuth"]["scheme"] == "bearer"


def test_public_endpoints_are_not_marked_secured() -> None:
    """登录/健康检查不能标成需要令牌，否则前端会以为要先登录才能登录。"""
    paths = build_spec()["paths"]
    for path in PUBLIC_PATHS:
        for method, operation in paths[path].items():
            assert not operation.get("security"), f"{method.upper()} {path} 不该要求鉴权"


def test_protected_endpoints_declare_bearer() -> None:
    paths = build_spec()["paths"]
    for path, method in (
        ("/api/v1/tasks", "get"),
        ("/api/v1/identity/users", "get"),
        ("/api/v1/identity/me", "get"),
    ):
        assert paths[path][method].get("security"), f"{method.upper()} {path} 应标注需要令牌"


# --------------------------------------------------------------------------- #
# 3. 分组与噪声
# --------------------------------------------------------------------------- #
def test_no_duplicate_tags() -> None:
    """`include_router` 追加 tags 会让 OpenAPI 出现两个同名分组。"""
    duplicates = [
        operation.get("tags")
        for operations in build_spec()["paths"].values()
        for operation in operations.values()
        if operation.get("tags") and len(operation["tags"]) != len(set(operation["tags"]))
    ]
    assert not duplicates, f"存在重复 tag：{duplicates[:3]}"


def test_markdown_does_not_leak_pydantic_titles() -> None:
    """Pydantic 会把 `isActive` 机械转成 `Isactive`，当成说明填进文档是噪音。"""
    markdown = render_markdown(build_spec())
    for noise in ("| Isactive |", "| Displayname |", "| Avatarurl |"):
        assert noise not in markdown


# --------------------------------------------------------------------------- #
# 4. 文档覆盖度：新增错误码/权限点会自动出现在文档里
# --------------------------------------------------------------------------- #
def test_every_error_has_a_readable_code() -> None:
    """业务码必须满足 `code // 100 == http_status`，否则前端按状态码分流会错。"""
    from core.exceptions import AppError

    markdown = render_markdown(build_spec())
    stack: list[type[AppError]] = list(AppError.__subclasses__())
    checked = 0
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        code = getattr(cls, "code", 0)
        if not isinstance(code, int) or code == 0:
            continue
        checked += 1
        assert code // 100 == int(getattr(cls, "http_status", 0) or 0), (
            f"{cls.__name__}: code={code} 与 http_status={getattr(cls, 'http_status', None)} 不匹配"
        )
        assert f"`{code}`" in markdown, f"{cls.__name__}（{code}）没出现在 docs/API.md"

    assert checked >= 10, "错误码反射数量异常，生成器可能坏了"


def test_every_permission_is_documented() -> None:
    from core.permissions import catalog

    markdown = render_markdown(build_spec())
    for spec in catalog.all():
        assert f"`{spec.code}`" in markdown, f"权限点 {spec.code} 没出现在 docs/API.md"
    assert len(catalog) >= 10


# --------------------------------------------------------------------------- #
# 5. 运行时行为没被导出逻辑破坏
# --------------------------------------------------------------------------- #
def test_served_openapi_matches_export(client: TestClient) -> None:
    """服务实际吐出的 /openapi.json 与落盘快照一致，避免「文档对、服务错」。"""
    served = client.get("/openapi.json").json()
    assert served == build_spec()


def test_anonymous_fallback_still_works(client: TestClient, api: str) -> None:
    """改用 HTTPBearer 解析令牌后，非生产环境的匿名回退不能被破坏。"""
    assert client.get(f"{api}/tasks").status_code == 200


@pytest.mark.parametrize("header", ["Bearer nonsense", "Basic abc", ""])
def test_malformed_authorization_does_not_500(client: TestClient, api: str, header: str) -> None:
    headers = {"Authorization": header} if header else {}
    resp = client.get(f"{api}/tasks", headers=headers)
    assert resp.status_code in (200, 401), resp.text
