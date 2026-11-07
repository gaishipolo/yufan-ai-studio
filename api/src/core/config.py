"""应用配置：单一事实来源，全部通过环境变量覆盖。

约定：环境变量名 = 字段名大写（大小写不敏感），例如 `DB_URL`、`CORS_ORIGINS`。
本地开发可直接复制 `.env.example` 为 `.env`。
"""

from __future__ import annotations

import ipaddress
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 生产环境 JWT 密钥最小长度（HS256 推荐值，见 RFC 7518 §3.2）
_MIN_SECRET_BYTES = 32
_WEAK_SECRET_PREFIXES = ("dev-only", "change-me")

# api/ 目录（本文件在 api/src/core/config.py）：运行期产物的锚点。
# 用绝对路径做默认值，保证「从哪个目录启动都读到同一份数据」。
_VAR_DIR = Path(__file__).resolve().parents[2] / "var"

# `.env` 的位置可被 `SETTINGS_ENV_FILE` 覆盖，测试用它指向一个不存在的文件：
# 否则测试结果取决于开发者本机 `.env`——把 `STORAGE_DRIVER` 切成 s3、或者填了
# `S3_*` 凭据之后，「本机绿、CI 红」（或反过来）就一定会发生。
# `infra/driver_config.py` 的驱动配置读同一个变量，改这里要一起改。
ENV_FILE = os.getenv("SETTINGS_ENV_FILE", ".env")


class Settings(BaseSettings):
    """全局配置。新增配置项时只需在此添加字段并补充 .env.example。"""

    model_config = SettingsConfigDict(
        env_file=(ENV_FILE,),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "雨范 AI 短视频工作台 API"
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
    # 默认值是**绝对路径**（锚定 api/ 目录），不随 cwd 变化：
    # 用 `./var/app.db` 时，在 api/ 下启动和在 api/src 下启动会落到两个不同的库，
    # 后者是个空库——登录 401、列表空，排查半天才发现数据在另一个文件里。
    db_url: str = f"sqlite+aiosqlite:///{(_VAR_DIR / 'app.db').as_posix()}"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---------- 鉴权 ----------
    # 未携带令牌时是否回退到演示账号（None = dev/test 自动放开，prod 关闭）
    allow_anonymous: bool | None = None
    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 7200
    # 刷新令牌有效期（默认 7 天）。access 令牌短、refresh 令牌长是刻意的：
    # 短的那张每次请求都在验证，长的那张只在续期时用一次，泄漏面小得多。
    # 续期时滑动延长，活跃用户的会话不会在第 7 天突然断掉。
    refresh_token_ttl_seconds: int = 7 * 24 * 3600
    # 登录审计保留天数：超过的由 `app prune-logins` 清掉（不自动跑，见 README）
    login_event_retention_days: int = 90
    # 可信反向代理的地址/网段（逗号分隔），只有直连对端在名单里才读 X-Forwarded-For。
    # 留空 = 不信任任何代理：限流按「直连 IP」计数，伪造请求头无效。
    trusted_proxies: str = ""

    @field_validator("trusted_proxies")
    @classmethod
    def _check_trusted_proxies(cls, value: str) -> str:
        """写错一个网段就启动失败，而不是默默按「不信任」跑。

        这类配置的错误表现是「限流看起来生效了，实际全站共用一个键」，
        上线后极难发现，不如在启动期炸掉。
        """
        for item in value.replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"TRUSTED_PROXIES 中的 {item!r} 不是合法地址/网段") from exc
        return value

    # ---------- 基础设施（策略选择）----------
    # 刻意不使用 Literal：新增驱动/供应商不应该需要修改本文件。
    # 名字对应 `infra.<kind>.<name>` 模块，未实现时启动即报错。
    #
    # 驱动**自己的参数**（provider 的 base_url/api_key、归档盘根目录、node 路径）
    # 不放这里，见 `infra/driver_config.py`：每个驱动一个 `XxxSettings`，
    # 环境变量前缀 = 驱动名大写（`AGNES_API_KEY`、`LIBTV_NODE`、`MEDIA_ARCHIVE_ROOT`）。
    # 这条边界是「部署时就确定、所有驱动共有」与「只有某个驱动懂」的分界。
    storage_driver: str = "local"
    storage_local_root: str = (_VAR_DIR / "storage").as_posix()
    storage_public_base: str = "/static"

    # memory = 进程内（重启即丢，开发用）；db = 落 queued_jobs 表（重启不丢，租约接管
    # 崩溃任务）。将来接 redis 只需新增 infra/queue/redis.py 并改这一行。
    queue_driver: str = "memory"
    # 可见性超时：job 被领走后多久算「领的人可能崩了」，可以被别人重新领走。
    # 必须大于单次 handler 的最长耗时，否则正干着的活会被第二个 worker 重复执行。
    # 视频/图片都是「短 job + 延迟重投」的形态，300 秒覆盖得住。
    queue_lease_sec: float = 300.0
    # 空队列时的轮询间隔。db 驱动每个 worker 每这么久查一次库：
    # 10 个 worker × 5 次/秒 = 50 QPS 的空查询，小库能扛；再高就该换驱动了。
    queue_poll_interval_sec: float = 0.2
    # 缓存驱动：memory = 进程内，随进程消失；redis 等共享实现未落地时启动即报错。
    # **只放可丢失的加速数据**：限流计数、模型清单、热点只读查询。
    # 会话与令牌的权威状态在 DB，不能只放这里——多实例下各缓存各的等于没登录。
    cache_driver: str = "memory"
    # key 前缀：多个环境共用一个 Redis 时用来隔离。改名只会让旧键全部 miss（无害）。
    cache_key_prefix: str = "yufan"
    # 归档盘驱动。以前归档盘是写死的 `LocalArchiveStorage()`，`.env.example`
    # 里「换实现只改这里」这句承诺对它不成立；现在它和 storage / queue 同形态。
    archive_driver: str = "local"
    # 替换任务的运行工作区（执行器要真实目录：inputs/outputs/records）。
    # 留空 = 本地驱动沿用 `<归档根>/runs`；归档切远端时落到 `api/var/runs`。
    replacement_runs_root: str = ""
    worker_enabled: bool = True
    worker_concurrency: int = 2
    # 周期任务调度器（模块自己 @periodic 注册）。多副本部署时每个进程都会跑一份，
    # 所以 handler 必须幂等；需要「全集群只跑一次」要先换掉 queue_driver。
    scheduler_enabled: bool = True
    # 归档 GC 默认关闭：删除不可逆，而调度器没有分布式选主，多实例会各跑一份。
    # 单机确认后再开；间隔、保护期、回收站保留期都可单独调。
    archive_gc_enabled: bool = False
    archive_gc_interval_sec: float = 86400.0
    archive_gc_min_age_hours: float = 24.0
    archive_gc_trash_days: float = 30.0
    # 每个用户的归档逻辑容量上限（字节）；0 = 不限。只算未删除的归档素材，
    # 内容寻址让同一份字节在物理上共用，但配额按「每个人登记了什么」算。
    archive_quota_bytes_per_user: int = 0
    # 镜像对账：只读 DB + 按 stateVersion 幂等覆盖写，多实例各跑一份结果相同，
    # 所以默认开启（与 GC 不同，它不需要选主）。也可随时 `app rebuild-mirrors`。
    archive_mirror_enabled: bool = True
    archive_mirror_interval_sec: float = 3600.0
    # 周期任务的集群协调能力：local = 单实例直接跑；backend = 用归档存储的
    # 条件写抢租约，全集群只有一个实例执行「不可逆/非幂等」的任务。
    leader_elector: str = "local"

    # ---------- 事件 outbox ----------
    # 进程内事件总线是「尽力而为」：订阅者抛异常只记日志、进程崩了就丢。
    # 需要保证「业务提交成功 → 事件一定被投递」的链路写 outbox 表，
    # 由这个周期任务补投（至少一次，消费方要幂等）。
    outbox_enabled: bool = True
    outbox_dispatch_interval_sec: float = 5.0
    outbox_batch_size: int = 100
    # 投递重试上限：到顶转 failed 留档，不再无限重投
    outbox_max_attempts: int = 5
    # 终态（delivered / failed）事件的保留天数，到点由周期任务清掉。
    # pending 无论多老都不清——那是还没发出去的事件，删了就是丢数据。
    outbox_retention_days: float = 7.0

    # ---------- 限流 ----------
    # 计数落在缓存层（`CACHE_DRIVER`）。memory 时各实例各算各的，
    # 多实例部署要换成共享缓存，否则「每 IP 每分钟 60 次」会变成 60×N 次。
    rate_limit_enabled: bool = True
    # 默认额度：只在路由显式引用时生效
    rate_limit_default_per_min: int = 60
    # 全局兜底额度（按客户端 IP 计，次/分钟）。**0 = 关闭**，默认关闭。
    #
    # 为什么默认关：全局中间件按 IP 计数，NAT / 反向代理后面一个出口 IP 会代表整栋楼，
    # 额度定小了正常用户一起 429。它防的是「脚本扫接口」，不是「用户点太快」——
    # 后者应该由路由级 `rate_limit()` 按用户算。要开就明确写个数，别指望默认值兜底。
    rate_limit_global_per_min: int = 0

    # ---------- 备份 ----------
    # 自动备份默认关：开着的代价是每天在磁盘上多一份库、且 sqlite 下 backup API 要持读锁。
    # 生产建议开（BACKUP_AUTO_ENABLED=true），并配好保留天数。
    backup_auto_enabled: bool = False
    backup_dir: str = "var/backups"
    backup_interval_hours: int = 24
    # 保留天数：0 表示永不删除（磁盘会一直涨，只适合手动管理）。
    backup_retention_days: int = 7

    # ---------- 可观测 ----------
    # Prometheus 抓取端点（`/metrics`，不进 OpenAPI、不走业务鉴权）。
    # 关闭时返回 404 而不是 403：不对外暴露「这里本来有个端点」。
    metrics_enabled: bool = True
    # 抓取凭据（`Authorization: Bearer <token>`）。生产环境留空时端点直接 403：
    # 路由模板、队列积压、驱动名对内部人员是信息，对扫描器是地图。
    metrics_token: str = ""
    # 就绪探针的降级阈值。只影响 `degraded`，不影响 `down`：
    # down 表示「不能服务请求」（库/队列不可用），degraded 表示「能服务但在漏」。
    # 把积压报成 down 会让编排系统摘掉整批实例——本来只是事件晚几分钟。
    health_outbox_pending_limit: int = 1000
    # 死信是「跑过但没成功」的任务，需要人看。默认容忍少量：偶发的坏载荷不该
    # 让整个集群显示不健康，但持续增长必须被看到。
    health_dead_job_limit: int = 10

    # ---------- 功能开关 ----------
    # 不引配置中心：一份静态 JSON 足够覆盖「上线前先关掉、出问题立刻关掉」。
    # 业务侧统一调用 `core.flags.is_on("xxx")`，不要在模块里各自读环境变量——
    # 否则「有哪些开关」这个问题得靠 grep 才能回答。
    feature_flags: Annotated[dict[str, bool], NoDecode] = Field(default_factory=dict)

    # 兼容旧的单值写法 AI_PROVIDER；推荐使用 AI_PROVIDERS=echo,volcengine
    ai_provider: str = "echo"
    ai_providers: str = ""

    # ---------- 生成编排 ----------
    # 轮询用「延迟投递」实现，不占 worker 槽位，因此可以设得比较密
    generation_poll_interval_sec: float = 2.0
    # 轮询次数上限是「本次进程内」的计数，重启会归零；真正的超时预算见
    # `generation_timeout_sec`（按任务创建时间算墙钟）。两个值应当互相匹配：
    # 900 × 2.0 = 1800 = timeout，配置不一致会在启动期提示。
    generation_max_polls: int = 900
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

    @field_validator("feature_flags", mode="before")
    @classmethod
    def _parse_flags(cls, v: object) -> object:
        """兼容 JSON 与环境变量里的 `a,b` 写法。

        两种都支持是因为两种场景都真实存在：Compose / K8s 的配置里写
        `FEATURE_FLAGS={"new_upload": false}` 更清楚；命令行临时试用
        `FEATURE_FLAGS=new_upload` 更省事。值为 `false` 的键保留在字典里，
        这样「显式关掉」和「从没声明」在 `enabled()` 里可以区分。
        """
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return {}
            if raw.startswith("{"):
                parsed = json.loads(raw)
                return {str(key): bool(value) for key, value in parsed.items()}
            return {item.strip(): True for item in raw.split(",") if item.strip()}
        return v

    @model_validator(mode="after")
    def _guard_generation_budget(self) -> Settings:
        """轮询预算必须撑得住声明的超时时间。

        这条守卫的由来：`GENERATION_TIMEOUT_SEC` 曾经**没有任何代码读取**，
        真实超时是 `max_polls × poll_interval`（默认 150×2 = 300 秒），
        而配置写着 1800 秒。默认值下每个 5 分钟的视频都会被误判成超时，
        而配置里那两个数字看上去都很正常 —— 这种错只能靠启动期算一遍才发现。

        允许 `预算 > 超时`（墙钟是权威，多留的轮询次数不会被用到），
        但 `预算 < 超时 × 0.8` 说明调小 `max_polls` 的人真正改变的是超时语义，
        必须显式改 `generation_timeout_sec`。
        """
        budget = self.generation_max_polls * self.generation_poll_interval_sec
        if budget < self.generation_timeout_sec * 0.8:
            raise ValueError(
                f"轮询预算不足：GENERATION_MAX_POLLS({self.generation_max_polls}) × "
                f"GENERATION_POLL_INTERVAL_SEC({self.generation_poll_interval_sec}) "
                f"= {budget:.0f} 秒，撑不到 GENERATION_TIMEOUT_SEC="
                f"{self.generation_timeout_sec} 秒。请调大轮询次数，"
                f"或显式调小超时时间（两者语义不同，不要只改一个）"
            )
        return self

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
