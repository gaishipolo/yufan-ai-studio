"""受认证的 HTTP 边界；不输出令牌，不自动重试写请求。"""

from __future__ import annotations

import ssl
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import httpx


class PackagingClient:
    def __init__(self, base_url: str, token: str, *, transport=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("API 地址必须是无账号、查询参数的 HTTP(S) 地址")
        if not token:
            raise ValueError("请设置 YUFAN_API_TOKEN 环境变量")
        self.base_url = base_url.rstrip("/")
        # 显式证书上下文保持 HTTPS 校验，同时不继承 SSLKEYLOGFILE 的调试写入。
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.load_verify_locations(cafile=certifi.where())
        self.http = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(120, connect=10),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            verify=tls,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()

    def request(self, method: str, path: str, *, raw=False, **kwargs):
        url = self.base_url + path
        if path == "/openapi.json":
            parsed = urlsplit(self.base_url)
            url = f"{parsed.scheme}://{parsed.netloc}/openapi.json"
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ValueError("请求未完成；可用相同配置重新执行以恢复") from exc
        if not response.is_success:
            # 不回显请求、响应正文；认证错误可能包含敏感上下文。
            raise ValueError(
                f"接口 {method} {path} 返回 HTTP {response.status_code}；"
                "请检查账号权限、素材有效性和服务日志，再重试"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ValueError("服务未返回 JSON，请核对 API 地址") from exc
        if raw:
            return body
        if body.get("code") != 0 or "data" not in body:
            raise ValueError("接口未返回成功结果，请检查服务日志")
        return body["data"]

    def upload(self, file: Path, category: str):
        with file.open("rb") as handle:
            return self.request(
                "POST",
                "/assets/upload",
                params={"category": category, "filename": file.name},
                content=handle,
                headers={"Content-Type": "application/octet-stream"},
            )

    def check_server(
        self, *, sharing=False, creating_packs=True, uploading=True, role_bindings=False,
        scene_text=False,
    ):
        spec = self.request("GET", "/openapi.json", raw=True)
        schemas = spec.get("components", {}).get("schemas", {})
        current = any(
            "videoTemplateId" in schema.get("properties", {}) for schema in schemas.values()
        )
        operation = (
            spec.get("paths", {}).get("/api/v1/templates/video-templates", {}).get("post", {})
        )
        if not current or operation.get("x-template-key-idempotency") != 1:
            raise ValueError("后端尚未加载封装契约，请更新并重启后端后再执行")
        if role_bindings and operation.get("x-template-role-bindings", 0) < int(role_bindings):
            raise ValueError("后端尚未加载用途绑定契约，请更新并重启后端后再执行")
        if scene_text and operation.get("x-template-scene-text") != 1:
            raise ValueError("后端尚未加载文字换场景契约，请更新并重启后端后再执行")
        me = self.request("GET", "/identity/me")
        permissions = me.get("permissions", [])
        required = ["asset:read", "template:read", "template:pack:manage"]
        if uploading:
            required.append("asset:create")
        if creating_packs:
            required.extend(["asset:reference:manage", "template:pack:publish"])
        if sharing:
            required.append("asset:share")
        missing = [
            permission
            for permission in required
            if not any(
                grant == "*"
                or grant == permission
                or (grant.endswith(":*") and permission.startswith(grant[:-1]))
                for grant in permissions
            )
        ]
        if missing:
            raise ValueError("账号缺少权限：" + "、".join(missing))
        return me
