"""接口契约导出：把运行时的 OpenAPI 固化成三份给前端用的一致性文件。

    app export-openapi            # 写入 docs/ + studio/
    app export-openapi --check    # 只比对，不写（CI / 测试用）

产物：

| 文件 | 读者 | 用途 |
|---|---|---|
| `docs/openapi.json` | 机器 | Apifox / Postman / IDE 插件直接导入 |
| `docs/API.md` | 前后端人 | 约定、错误码、权限点、接口清单、模型字段表 |
| `studio/src/shared/api/schema.ts` | TypeScript | 前端 import 复用，不再手写重复类型 |

三份文件都不是手写的，所以不会腐坏：改完代码重跑一次即可。
`tests/test_openapi_contract.py` 会拦住「改了接口忘了重跑」的情况。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Schema = dict[str, Any]
Spec = dict[str, Any]

_PKG_ROOT = Path(__file__).resolve().parents[2]  # 工作区根目录
DEFAULT_JSON = _PKG_ROOT / "api" / "docs" / "openapi.json"
DEFAULT_MD = _PKG_ROOT / "api" / "docs" / "API.md"
DEFAULT_TS = _PKG_ROOT / "studio" / "src" / "shared" / "api" / "schema.ts"

_BANNER = "此文件由 `app export-openapi` 自动生成，请勿手工编辑。"

# 泛型封套：`ApiResponse_UserOut_` → `ApiResponse<UserOut>`
_GENERICS = ("ApiResponse_", "Page_")
_HELPER_WRAPPERS = ("ApiResponse", "Page")


# --------------------------------------------------------------------------- #
# OpenAPI 取值
# --------------------------------------------------------------------------- #
def build_spec() -> Spec:
    """构建 OpenAPI 文档。

    `create_app()` 需要设置环境变量（或使用默认的 dev 配置），但**不会**建表、
    不会起 worker —— lifespan 只在 uvicorn 启动时执行，这里只取路由定义。
    """
    from app import create_app

    return create_app().openapi()


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _type_name(name: str) -> str:
    """把 Pydantic 生成的自动名映射成可读的 TypeScript 类型表达式。"""
    stripped = name.rstrip("_")
    if stripped.startswith("list_"):
        return f"{_type_name(stripped[5:])}[]"
    if stripped == "NoneType":
        return "null"
    for prefix in _GENERICS:
        if stripped.startswith(prefix):
            inner = _type_name(stripped[len(prefix) :])
            generic = prefix[:-1]
            return f"{generic}<{inner}>"
    return stripped


def _is_wrapper(name: str) -> bool:
    return any(name.startswith(p) for p in _GENERICS)


def _ts_type(schema: Schema, spec: Spec, *, depth: int = 0) -> str:
    """OpenAPI 3.1 schema → TypeScript 类型表达式。"""
    if not schema:
        return "unknown"
    if "$ref" in schema:
        return _type_name(_ref_name(schema["$ref"]))

    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        rendered = [_ts_type(v, spec, depth=depth) for v in variants]
        # 去掉被别的宽类型覆盖的重复项（保序）
        deduped: list[str] = []
        for item in rendered:
            if item not in deduped:
                deduped.append(item)
        return " | ".join(deduped) if deduped else "unknown"

    if "enum" in schema:
        values = schema["enum"]
        if len(values) == 1:
            return json.dumps(values[0], ensure_ascii=False)
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in values)

    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)

    node_type = schema.get("type")
    if isinstance(node_type, list):  # 3.1 的 type 数组形式
        return " | ".join(
            _ts_type({**schema, "type": t}, spec, depth=depth)
            for t in node_type
        )

    if node_type == "array":
        items = schema.get("items") or {}
        inner = _ts_type(items, spec, depth=depth)
        return f"({inner})[]" if " | " in inner else f"{inner}[]"

    if node_type == "object" or "properties" in schema:
        props: dict[str, Schema] = schema.get("properties") or {}
        if not props:
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict):
                return f"Record<string, {_ts_type(extra, spec, depth=depth)}>"
            return "Record<string, unknown>"
        required = set(schema.get("required") or [])
        lines = ["{"]
        for prop_name, prop_schema in props.items():
            optional = "" if prop_name in required else "?"
            comment = prop_schema.get("description", "")
            note = f"  /** {comment} */\n" if comment and depth == 0 else ""
            lines.append(
                f"{note}  {prop_name}{optional}: "
                f"{_ts_type(prop_schema, spec, depth=depth + 1)};"
            )
        lines.append("}")
        return "\n".join(lines)

    if node_type in ("integer", "number"):
        return "number"
    if node_type == "boolean":
        return "boolean"
    if node_type == "null":
        return "null"
    if node_type == "string":
        return "string"
    return "unknown"


# --------------------------------------------------------------------------- #
# TypeScript
# --------------------------------------------------------------------------- #
def render_typescript(spec: Spec) -> str:
    schemas: dict[str, Schema] = spec.get("components", {}).get("schemas", {})

    out: list[str] = [
        "/**",
        " * 后端接口类型（自动生成，请勿手工编辑）。",
        " *",
        " * 重新生成：`cd api && .venv/Scripts/python -m cli export-openapi`",
        " *",
        " * 用法：",
        " *   import type { ApiResponse, Page, TaskOut, EndpointData } from '@/shared/api/schema';",
        " *   const task = await api.get<TaskOut>(`/tasks/${id}`);   // 已自动解包 data",
        " */",
        "",
        "/* eslint-disable */",
        "",
        "/** 统一响应封套：所有接口都返回这个形状 */",
        "export interface ApiResponse<T = unknown> {",
        "  /** 0 表示成功，其余为业务错误码（见 docs/API.md 的错误码总表） */",
        "  code: number;",
        "  message: string;",
        "  data: T;",
        "}",
        "",
        "/** 分页封套 */",
        "export interface Page<T> {",
        "  items: T[];",
        "  total: number;",
        "  page: number;",
        "  pageSize: number;",
        "}",
        "",
    ]

    # 枚举单独归拢到一处，前端做下拉/标签映射时直接引用
    enums = {
        name: schema["enum"]
        for name, schema in schemas.items()
        if "enum" in schema and isinstance(schema.get("enum"), list)
    }
    if enums:
        out.append("/* ------------------------------ 枚举 ------------------------------ */")
        out.append("")
        for name, values in enums.items():
            literal = " | ".join(json.dumps(v, ensure_ascii=False) for v in values)
            out.append(f"/** 取值：{', '.join(str(v) for v in values)} */")
            out.append(f"export type {name} = {literal};")
            serialized = json.dumps(values, ensure_ascii=False)
            out.append(f"export const {name}_VALUES = {serialized} as const;")
            out.append("")

    out.append("/* ------------------------------ 模型 ------------------------------ */")
    out.append("")
    for name in sorted(schemas):
        if _is_wrapper(name) or name in enums:
            continue
        schema = schemas[name]
        title = schema.get("title") or name
        doc = schema.get("description") or title
        body = _ts_type(schema, spec)
        if body.startswith("{"):
            out.append(f"/** {doc} */")
            out.append(f"export interface {name} {body}")
        else:
            out.append(f"/** {doc} */")
            out.append(f"export type {name} = {body};")
        out.append("")

    # 接口 → 解包后的 data 类型
    out.append("/* --------------------- 接口 → data 类型（已解包封套） --------------------- */")
    out.append("")
    out.append("/** 键是 `METHOD path`，值是去掉 {code,message,data} 封套后的类型 */")
    out.append("export interface EndpointData {")
    for path, operations in sorted(spec.get("paths", {}).items()):
        for method, operation in sorted(operations.items()):
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            data_type = _response_data_type(operation, spec)
            summary = operation.get("summary", "")
            out.append(f"  /** {summary} */")
            out.append(f'  "{method.upper()} {path}": {data_type};')
    out.append("}")
    out.append("")
    return "\n".join(out)


def _response_data_type(operation: Schema, spec: Spec) -> str:
    responses = operation.get("responses") or {}
    for status in ("200", "201", 200, 201):
        resp = responses.get(status) or responses.get(str(status))
        if not resp:
            continue
        for media_type, media in (resp.get("content") or {}).items():
            if "json" not in media_type:
                continue
            schema = media.get("schema") or {}
            name = _ref_name(schema["$ref"]) if "$ref" in schema else ""
            if name.startswith("ApiResponse_"):
                return _type_name(name[len("ApiResponse_") :])
            return _ts_type(schema, spec)
    return "unknown"


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def _cell(text: str) -> str:
    """表格单元格转义：竖线、换行会破坏 markdown 表格。"""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _schema_fields(schema: Schema, spec: Spec) -> list[tuple[str, str, str, str]]:
    """展开一个对象 schema 的字段：(名称, 类型, 必填, 说明)。"""
    if "$ref" in schema:
        name = _ref_name(schema["$ref"])
        target = spec.get("components", {}).get("schemas", {}).get(name) or {}
        return _schema_fields(target, spec)

    props: dict[str, Schema] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    rows: list[tuple[str, str, str, str]] = []
    for prop_name, prop in props.items():
        # 只用 description：`title` 是 Pydantic 由字段名机械生成的（`isActive` → `Isactive`），
        # 当成说明填进文档等于制造噪音
        rows.append(
            (
                prop_name,
                _ts_type(prop, spec),
                "是" if prop_name in required else "否",
                str(prop.get("description") or ""),
            )
        )
    return rows


def _collect_errors() -> list[tuple[int, int, str, str, str]]:
    """反射全部 AppError 子类：(业务码, HTTP 状态, 来源模块, 类名, 提示文案)。"""
    from core.exceptions import AppError

    found: dict[int, tuple[int, int, str, str, str]] = {}
    stack: list[type[AppError]] = list(AppError.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        code = getattr(cls, "code", 0)
        if not isinstance(code, int) or code == 0:
            continue
        module = getattr(cls, "__module__", "")
        origin = "框架" if not module.startswith("modules.") else module.split(".")[1]
        found.setdefault(
            code,
            (
                code,
                int(getattr(cls, "http_status", 0) or 0),
                origin,
                cls.__name__,
                str(getattr(cls, "message", "")),
            ),
        )
    return [found[k] for k in sorted(found)]


def _response_rows(data_type: str, spec: Spec) -> list[tuple[str, str, str, str]]:
    """把 `TaskListOut` / `Page<UserOut>` / `RoleOut[]` 展开成字段表。"""
    schemas: dict[str, Schema] = spec.get("components", {}).get("schemas", {})

    if data_type.startswith("Page<") and data_type.endswith(">"):
        inner = data_type[len("Page<") : -1]
        return [
            ("items", f"{inner}[]", "是", "当前页数据"),
            ("total", "number", "是", "总条数（用于分页器）"),
            ("page", "number", "是", "当前页码，从 1 开始"),
            ("pageSize", "number", "是", "每页条数"),
        ]

    if data_type.endswith("[]"):
        inner = data_type[:-2]
        rows = _schema_fields(schemas.get(inner, {}), spec)
        return [(f"[].{name}", ts, req, note) for name, ts, req, note in rows]

    if data_type in schemas:
        return _schema_fields(schemas[data_type], spec)
    return []


def render_markdown(spec: Spec) -> str:
    from core.permissions import catalog

    info = spec.get("info", {})
    paths: dict[str, dict[str, Schema]] = spec.get("paths", {})
    schemas: dict[str, Schema] = spec.get("components", {}).get("schemas", {})

    # 按 tag 归组
    groups: dict[str, list[tuple[str, str, Schema]]] = {}
    for path, operations in paths.items():
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            tags = operation.get("tags") or ["未分类"]
            groups.setdefault(str(tags[0]), []).append((method.upper(), path, operation))

    total_endpoints = sum(len(v) for v in groups.values())

    lines: list[str] = [
        f"# {info.get('title', 'API')} — 接口文档",
        "",
        f"> {_BANNER}",
        ">",
        f"> 版本 `{info.get('version', '')}`｜共 **{total_endpoints}** 个接口"
        f" / **{len(groups)}** 个模块",
        ">",
        "> 重新生成：`cd api && .venv/Scripts/python -m cli export-openapi`",
        "",
        "在线调试（仅非生产环境开放）：<http://127.0.0.1:8080/docs>",
        "",
        "**怎么查**：某个接口怎么调 → 第 4 节（按模块分）｜某个字段什么意思 → 第 5 节｜"
        "报错了是什么原因 → 第 3 节｜这个按钮要不要显示 → 第 2 节的权限码。",
        "",
        "前端拿类型不用手抄：",
        "",
        "```ts",
        "import type { TaskOut, EndpointData } from '@/shared/api/schema';",
        "```",
        "",
        "`EndpointData` 的键是 `\"METHOD 路径\"`，值已经去掉 `{code,message,data}` 封套。",
        "这份 `schema.ts` 与本文档由同一个命令生成，不会对不上。",
        "",
        "---",
        "",
        "## 1. 三条约定",
        "",
        "**① 所有响应都是同一个封套**，前端统一按 `data` 取业务数据：",
        "",
        "```json",
        '{ "code": 0, "message": "OK", "data": { } }',
        "```",
        "",
        "- `code = 0` 成功；非 0 是业务错误码，**HTTP 状态同时也非 2xx**",
        "- 出错的 `data` 为 `null`，错误详情看 `message`（可直接展示给用户）",
        "- 响应头带 `X-Request-Id`，报错时把它给后端能直接定位日志",
        "",
        "**② 字段一律 camelCase**，与 TypeScript 完全一致，不需要任何转换层。",
        "",
        "**③ 时间一律 ISO 8601 UTC**，带 `Z` 后缀（如 `2026-11-05T06:24:11Z`），",
        "前端用 `new Date(value)` 即可，展示时再转本地时区。",
        "",
        "### 鉴权",
        "",
        "```http",
        "Authorization: Bearer <accessToken>",
        "```",
        "",
        "`accessToken` 从 `POST /api/v1/identity/login` 拿，默认有效期 2 小时。",
        "标注 🔒 的接口必须带令牌；标「公开」的不需要。",
        "",
        "令牌只携带身份，**权限以数据库为准**：账号被停用或角色被改，",
        "下一个请求立刻生效（这是后端行为，前端不必轮询）。",
        "",
        "拿不到业务数据时按状态码分流：",
        "",
        "| 状态码 | 含义 | 前端应对 |",
        "|---|---|---|",
        "| 401 | 未登录 / 令牌过期 / 账号已停用 | 清本地令牌，跳登录页 |",
        "| 403 | 已登录但缺权限 | 提示无权限，别跳登录页 |",
        "| 404 | 资源不存在或无权访问 | 空态 |",
        "| 409 | 状态冲突（如重复操作） | 提示后刷新列表 |",
        "| 422 | 参数校验失败 | 按 `message` 定位到字段 |",
        "| 5xx | 服务端问题 | 可重试，带 `X-Request-Id` 反馈 |",
        "",
        "### 分页",
        "",
        "列表接口统一收 `page`（从 1 开始）/ `pageSize`（默认 20，上限 100），",
        "返回 `{ items, total, page, pageSize }`。",
        "",
    ]

    # ---- 权限点 ----
    lines += [
        "---",
        "",
        "## 2. 权限点（前端控制菜单 / 按钮显隐用）",
        "",
        "`GET /api/v1/identity/me` 返回的 `permissions` 就是这个列表的子集。",
        "持有 `*` 表示超级管理员，所有权限点都成立。",
        "",
    ]
    for group, specs in catalog.grouped().items():
        lines += [f"**{group}**", "", "| 权限码 | 名称 | 说明 |", "|---|---|---|"]
        for permission in specs:
            lines.append(
                f"| `{permission.code}` | {_cell(permission.name)} "
                f"| {_cell(permission.description or '')} |"
            )
        lines.append("")
    lines.append(
        "> 判定规则：`*` 恒真；`task:*` 这类通配可覆盖 `task:read`；否则按字符串精确匹配。"
    )
    lines.append("")

    # ---- 错误码 ----
    lines += [
        "---",
        "",
        "## 3. 业务错误码总表",
        "",
        "规则：`code = HTTP状态 × 100 + 模块段 × 10 + 序号`，所以",
        "`40926` = HTTP 409 + `identity` 段 + 第 6 个错误。",
        "**前端只需判断 `code !== 0`**，具体文案用 `message`（可直接展示）。",
        "",
        "| code | HTTP | 来源 | 异常类 | 默认文案 |",
        "|---|---|---|---|---|",
    ]
    for code, http_status, origin, cls_name, message in _collect_errors():
        lines.append(
            f"| `{code}` | {http_status} | {origin} | `{cls_name}` | {_cell(message)} |"
        )
    lines.append("")

    # ---- 接口清单 ----
    order = [
        "身份",
        "任务",
        "模型",
        "素材",
        "生图",
        "模板",
        "分镜模板",
        "镜头库",
        "系统",
    ]
    ordered_tags = [t for t in order if t in groups] + [
        t for t in groups if t not in order
    ]

    lines += ["---", "", "## 4. 接口清单", ""]

    # 目录：模块多起来之后，1500 行文件没有目录基本没法查
    lines += ["| 模块 | 接口数 | 说明 |", "|---|---|---|"]
    for tag in ordered_tags:
        lines.append(f"| {tag} | {len(groups[tag])} | 见下方 `4.{ordered_tags.index(tag) + 1}` |")
    lines.append("")

    for index, tag in enumerate(ordered_tags, start=1):
        endpoints = groups[tag]
        lines += [f"### 4.{index} {tag}（{len(endpoints)} 个）", ""]
        lines += ["| 方法 | 路径 | 说明 | 鉴权 |", "|---|---|---|---|"]
        for method, path, operation in sorted(endpoints, key=lambda x: x[1]):
            locked = "🔒" if operation.get("security") else "公开"
            lines.append(
                f"| `{method}` | `{path}` | {_cell(operation.get('summary', ''))} | {locked} |"
            )
        lines.append("")

        for method, path, operation in sorted(endpoints, key=lambda x: x[1]):
            lines.append(f"#### `{method} {path}`")
            lines.append("")
            summary = operation.get("summary", "")
            description = operation.get("description", "")
            if summary:
                lines.append(f"**{summary}**")
                lines.append("")
            if description:
                lines.append(description)
                lines.append("")

            # 路径 / 查询参数
            params = [
                p
                for p in operation.get("parameters", [])
                if p.get("in") in ("path", "query", "header")
            ]
            if params:
                lines += ["| 参数 | 位置 | 类型 | 必填 | 说明 |", "|---|---|---|---|---|"]
                for p in params:
                    schema = p.get("schema") or {}
                    lines.append(
                        f"| `{p['name']}` | {p['in']} | `{_ts_type(schema, spec)}` | "
                        f"{'是' if p.get('required') else '否'} | "
                        f"{_cell(p.get('description') or schema.get('description') or '')} |"
                    )
                lines.append("")

            # 请求体
            request_body = operation.get("requestBody") or {}
            for media_type, media in (request_body.get("content") or {}).items():
                if "json" not in media_type:
                    continue
                body_schema = media.get("schema") or {}
                ref = _ref_name(body_schema["$ref"]) if "$ref" in body_schema else ""
                lines.append(
                    f"**请求体** `{ref}`"
                    if ref
                    else "**请求体**（内联结构）"
                )
                lines.append("")
                rows = _schema_fields(body_schema, spec)
                if rows:
                    lines += ["| 字段 | 类型 | 必填 | 说明 |", "|---|---|---|---|"]
                    for name, ts_type, required, comment in rows:
                        lines.append(
                            f"| `{name}` | `{_cell(ts_type)}` | {required} | {_cell(comment)} |"
                        )
                    lines.append("")

            # 响应
            data_type = _response_data_type(operation, spec)
            lines.append(f"**响应** `ApiResponse<{data_type}>`（前端取 `res.data`）")
            lines.append("")
            response_rows = _response_rows(data_type, spec)
            if response_rows:
                lines += ["| 字段 | 类型 | 必填 | 说明 |", "|---|---|---|---|"]
                for field_name, ts_type, required, comment in response_rows:
                    lines.append(
                        f"| `{field_name}` | `{_cell(ts_type)}` | {required} | {_cell(comment)} |"
                    )
                lines.append("")

    # ---- 模型定义 ----
    lines += ["---", "", "## 5. 数据模型", ""]
    for name in sorted(schemas):
        if _is_wrapper(name):
            continue
        schema = schemas[name]
        if "enum" in schema:
            values = schema["enum"]
            lines.append(
                f"### `{name}`（枚举）"
            )
            lines.append("")
            lines.append(
                "取值：" + "、".join(f"`{v}`" for v in values)
            )
            lines.append("")
            continue
        rows = _schema_fields(schema, spec)
        if not rows:
            continue
        lines.append(f"### `{name}`")
        lines.append("")
        if schema.get("description"):
            lines.append(str(schema["description"]))
            lines.append("")
        lines += ["| 字段 | 类型 | 必填 | 说明 |", "|---|---|---|---|"]
        for field_name, ts_type, required, comment in rows:
            lines.append(
                f"| `{field_name}` | `{_cell(ts_type)}` | {required} | {_cell(comment)} |"
            )
        lines.append("")

    # 不写生成时间：`--check` 靠字节比对，带时间戳会让它每天误报「已过期」。
    # 需要溯源就 `git log -1 -- docs/API.md`。
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 写盘
# --------------------------------------------------------------------------- #
def export(
    *,
    json_path: Path = DEFAULT_JSON,
    md_path: Path = DEFAULT_MD,
    ts_path: Path = DEFAULT_TS,
    check: bool = False,
) -> dict[str, str]:
    """生成三份文件。

    `check=True` 时只比对，不写盘；返回每个文件的状态：
    `written` / `unchanged` / `stale`（内容不一致）/ `missing`。
    """
    spec = build_spec()
    payloads: dict[Path, str] = {
        json_path: json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
        md_path: render_markdown(spec),
        ts_path: render_typescript(spec),
    }

    status: dict[str, str] = {}
    stale: list[str] = []
    for path, content in payloads.items():
        exists = path.exists()
        same = exists and path.read_text(encoding="utf-8") == content
        key = str(path)
        if same:
            status[key] = "unchanged"
            continue
        if check:
            status[key] = "stale" if exists else "missing"
            stale.append(key)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        status[key] = "written"

    if check and stale:
        raise SystemExit(
            "接口契约文件与代码不一致（请运行 `app export-openapi` 重新生成）：\n  "
            + "\n  ".join(stale)
        )
    return status


__all__ = [
    "DEFAULT_JSON",
    "DEFAULT_MD",
    "DEFAULT_TS",
    "build_spec",
    "export",
    "render_markdown",
    "render_typescript",
]
