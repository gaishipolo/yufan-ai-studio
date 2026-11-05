"""接口契约文件的一致性测试。

`docs/openapi.json`、`docs/API.md`、`studio/src/shared/api/schema.ts` 都是
`app export-openapi` 生成物。它们最容易出的问题不是「生成得不对」，
而是**改了接口忘了重新生成** —— 前端照着旧文档写完，联调时才发现对不上。

这些测试把「重新生成」变成硬约束：只要代码里的接口变了而文件没更新，CI 就红。
修法永远是 `app export-openapi`，不要手工改那三个文件。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from openapi_export import (
    DEFAULT_JSON,
    DEFAULT_MD,
    DEFAULT_TS,
    _response_data_type,
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
# 5. 生成器不能编造类型名（R10-01）
# --------------------------------------------------------------------------- #
_TS_BUILTINS = {
    "ApiResponse",
    "Page",
    "Record",
    "boolean",
    "null",
    "number",
    "string",
    "unknown",
}


def _referenced_types(expr: str) -> set[str]:
    """类型表达式里引用到的标识符（去掉对象字面量的键与 TS 内建名）。

    `{ revoked: number }` 里的 `revoked` 是键，`number` 是内建；只有 `TaskOut`
    这种「应该在 components.schemas 里存在」的名字才值得检查。
    """
    cleaned = re.sub(r"/\*.*?\*/", " ", expr, flags=re.S)
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", cleaned))
    keys = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\??\s*:", cleaned))
    return tokens - keys - _TS_BUILTINS


def test_every_endpoint_data_type_is_defined() -> None:
    """每个接口的 data 类型只能引用真实存在的组件。

    修复前 `_response_data_type` 把 `ApiResponse_dict_str__int__` 切成
    `dict_str__int` 就当类型名写进 `schema.ts`，而这个名字在 `components.schemas`
    里不存在——生成物 TS2304 编不过；前端手改补上别名，后端的字节比对又红。
    字节比对保证「生成物与生成器一致」，保证不了「生成器产出的是合法代码」，
    所以这里从类型引用这一侧再守一遍。
    """
    spec = build_spec()
    schemas = set(spec["components"]["schemas"])
    undefined: list[str] = []
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            for name in _referenced_types(_response_data_type(operation, spec)):
                if name not in schemas:
                    undefined.append(f"{method.upper()} {path} → {name}")
    assert not undefined, f"生成物引用了不存在的类型：{undefined}"


def test_wrapper_without_a_named_model_falls_back_to_the_inline_shape() -> None:
    """封套内层不是具名模型时，渲染 `data` 的真实形状，而不是编一个名字。"""
    spec = {
        "components": {
            "schemas": {
                "ApiResponse_dict_str__int__": {
                    "type": "object",
                    "properties": {
                        "data": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "additionalProperties": {"type": "integer"},
                                },
                                {"type": "null"},
                            ]
                        }
                    },
                }
            }
        }
    }
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": "#/components/schemas/ApiResponse_dict_str__int__"
                        }
                    }
                }
            }
        }
    }
    # 不带 `| null`：按名字引用时生成器也写 `T`，两条路径的口径要一致
    assert _response_data_type(operation, spec) == "Record<string, number>"


# --------------------------------------------------------------------------- #
# 6. 运行时行为没被导出逻辑破坏
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
