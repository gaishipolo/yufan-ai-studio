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

    def upload(self, file: Path, kind: str):
        """上传一份素材，返回资产库 v2 的资产行。

        视频必须走 `/library/source-videos`：那条路由落库时写死
        `origin=template_source`，模板库正是靠这个来源把原片挑出来。其余类型走
        `/library/assets/upload`。v1 的 `category` 参数随 `assets` 表下线——v2 只分
        `kind`（文件类型）与 `origin`（来源，由服务端决定，不由调用方传）。
        """
        source = kind == "video"
        with file.open("rb") as handle:
            return self.request(
                "POST",
                "/library/source-videos" if source else "/library/assets/upload",
                params={"filename": file.name} if source else {"filename": file.name, "kind": kind},
                content=handle,
                headers={"Content-Type": "application/octet-stream"},
            )

    def share_to_team(self, asset: dict):
        """把原片共享到团队库。

        账号带 `library:share:direct` 时服务端直接生效，否则只留下一条待审申请——
        审批通过之前原片仍然只有自己能看见，所以调用方必须回读 `visibility` 复核，
        不能凭「接口 200」就当共享成功。
        """
        return self.request(
            "POST",
            "/library/share-requests",
            json={
                "assetId": asset["id"],
                "action": "share",
                "target": "team",
                "expectedVersion": asset.get("stateVersion", 0),
            },
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
        required = ["library:read", "template:read", "template:pack:manage"]
        if uploading or creating_packs:
            # 封装成员只能是「我上传的 / 我看得见的」资产，v2 把这两件事都收在
            # `library:create` 上（v1 时代是 `asset:create` + `asset:reference:manage`）。
            required.append("library:create")
        if creating_packs:
            required.append("template:pack:publish")
        if sharing:
            required.extend(["library:share", "library:share:direct"])
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
