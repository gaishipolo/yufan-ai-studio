"""应用配置：单一事实来源，全部通过环境变量覆盖。

约定：环境变量名 = 字段名大写（大小写不敏感），例如 `DB_URL`、`CORS_ORIGINS`。
本地开发可直接复制 `.env.example` 为 `.env`。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 生产环境 JWT 密钥最小长度（HS256 推荐值，见 RFC 7518 §3.2）
_MIN_SECRET_BYTES = 32
_WEAK_SECRET_PREFIXES = ("dev-only", "change-me")


class Settings(BaseSettings):
    """全局配置。新增配置项时只需在此添加字段并补充 .env.example。"""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "天火 AI 短视频工作台 API"
    app_env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # ---------- 跨域 ----------
    # 支持 `a,b,c` 逗号分隔或 JSON 数组两种写法
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    cors_allow_credentials: bool = True

    # ---------- 数据库 ----------
    db_url: str = "sqlite+aiosqlite:///./var/app.db"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---------- 鉴权 ----------
    # 未携带令牌时是否回退到演示账号（None = dev/test 自动放开，prod 关闭）
    allow_anonymous: bool | None = None
    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 7200

    # ---------- 基础设施（策略选择）----------
    # 刻意不使用 Literal：新增驱动/供应商不应该需要修改本文件。
    # 名字对应 `infra.<kind>.<name>` 模块，未实现时启动即报错。
    storage_driver: str = "local"
    storage_local_root: str = "./var/storage"
    storage_public_base: str = "/static"

    queue_driver: str = "memory"
    worker_enabled: bool = True
    worker_concurrency: int = 2

    # 兼容旧的单值写法 AI_PROVIDER；推荐使用 AI_PROVIDERS=echo,volcengine
    ai_provider: str = "echo"
    ai_providers: str = ""

    # ---------- 生成编排 ----------
    # 轮询用「延迟投递」实现，不占 worker 槽位，因此可以设得比较密
    generation_poll_interval_sec: float = 2.0
    generation_max_polls: int = 150
    generation_timeout_sec: int = 1800

    # ---------- 派生属性 ----------
    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def anonymous_allowed(self) -> bool:
        """匿名访问策略：显式配置优先，否则只在非生产环境放开。"""
        if self.allow_anonymous is not None:
            return self.allow_anonymous
        return self.app_env in ("dev", "test")

    @property
    def ai_provider_list(self) -> list[str]:
        """要加载的供应商列表。`AI_PROVIDERS` 优先，回退到单值 `AI_PROVIDER`。"""
        raw = self.ai_providers or self.ai_provider or "echo"
        return [name.strip() for name in raw.split(",") if name.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """兼容 `a,b` / `["a","b"]` / 已解析列表三种输入。"""
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return json.loads(raw)
            return [item.strip() for item in raw.split(",") if item.strip()]
        return v

    @field_validator("api_prefix")
    @classmethod
    def _normalize_prefix(cls, v: str) -> str:
        if not v.startswith("/"):
            v = f"/{v}"
        return v.rstrip("/")

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        """生产启动前的硬性检查：宁可起不来，也不要裸奔上线。

        这类配置错误的典型剧本是「照着 .env.example 部署」，
        出错后日志里看不出任何异常，因此必须挡在启动期。
        """
        if self.app_env != "prod":
            return self

        problems: list[str] = []
        if self.allow_anonymous:
            problems.append("ALLOW_ANONYMOUS 必须为 false（匿名请求会拿到超级权限）")
        if self.jwt_secret.startswith(_WEAK_SECRET_PREFIXES):
            problems.append("JWT_SECRET 仍是样例值，请用 `openssl rand -hex 32` 生成")
        elif len(self.jwt_secret.encode()) < _MIN_SECRET_BYTES:
            problems.append(f"JWT_SECRET 长度不足 {_MIN_SECRET_BYTES} 字节")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS 不允许通配 *，请写具体来源")
        if self.debug:
            problems.append("DEBUG 必须为 false")
        if problems:
            raise ValueError("生产环境配置不安全：" + "；".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """进程内单例。测试中可用 `get_settings.cache_clear()` 重置。"""
    return Settings()


settings = get_settings()
