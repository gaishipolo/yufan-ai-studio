"""应用配置：单一事实来源，全部通过环境变量覆盖。

约定：环境变量名 = 字段名大写（大小写不敏感），例如 `DB_URL`、`CORS_ORIGINS`。
本地开发可直接复制 `.env.example` 为 `.env`。
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 生产环境 JWT 密钥最小长度（HS256 推荐值，见 RFC 7518 §3.2）
_MIN_SECRET_BYTES = 32
_WEAK_SECRET_PREFIXES = ("dev-only", "change-me")

# 允许的签名算法。**刻意不含 `none`**：PyJWT 支持它（= 不签名），而
# `jwt.decode(..., algorithms=[settings.jwt_algorithm])` 会原样接受它。
# 也就是说 `JWT_ALGORITHM=none` 这一个拼写错误，就能把整套鉴权变成
# 「谁都能用任意 `sub` 自签一张票」——而且日志里不会有任何异常。
# 不在白名单里的名字（含 typo）一律启动失败，不静默回退到默认值。
_JWT_ALGORITHMS = frozenset(
    {
        "HS256",
        "HS384",
        "HS512",
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "EdDSA",
    }
)

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

    # ---------- 运行期依赖 ----------
    # node 可执行文件：提示词编译器要用它，执行器子进程也要用它。
    # **不挂在某个驱动/供应商的设置下**：模板模块为了「编译一段提示词」去读
    # `LIBTV_*`，等于把供应商当成运行环境，别的供应商接进来就没有 node 可用。
    # `LIBTV_NODE` 作为别名继续生效——旧部署写的是这个名字，改了要现场配环境。
    node_binary: str = Field(
        "node", validation_alias=AliasChoices("NODE_BINARY", "LIBTV_NODE")
    )

    @field_validator("node_binary", mode="before")
    @classmethod
    def _empty_node_means_default(cls, v: object) -> object:
        """`NODE_BINARY=`（写成空）等同于没配。

        空串不是「走 PATH」而是「找不到可执行文件」：`shutil.which("")` 返回 None，
        自检会报 `PATH 里找不到 ''`，而配置看上去是配了。
        """
        if isinstance(v, str) and not v.strip():
            return "node"
        return v

    # ---------- 演示数据 ----------
    # `app seed` 种出来的演示账号是谁。放在配置里而不是写死成某个模块的常量：
    # 演示数据是**跨模块连贯**的（任务属于这个用户、分镜引用模板），各模块种的
    # 行必须指向同一个账号。让框架硬编码 `"u_001"` 等于框架认识业务数据；
    # 让各模块各抄一份常量，改一次要改五处且没人告诉你改漏了。
    # 这是框架唯一认识的「演示」语义：一个 id 和一个昵称，都是字符串。
    demo_user_id: str = "u_001"
    demo_user_name: str = "老大"

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
    # 从池里借连接最多等多久。默认的 30 秒是「全站卡死三十秒」的长度：
    # 池被占满时，每一个新请求都会安静地挂在这里，看起来像服务没响应，
    # 而日志里一条错误都没有。5 秒足够覆盖正常的排队抖动，超出就该快速失败。
    # 别往 1 秒以下调：启动期有并发借连接（worker 起来就开始取任务、各模块
    # 恢复也并行查库），实际量到 `0.05s` 会让服务卡在 startup 起不来。
    db_pool_timeout: float = 5.0
    # 连接最长复用时长（秒）。PG 侧被中间件/防火墙掐掉空闲连接后，
    # 拿到的是半开连接，报 `server closed the connection unexpectedly`；
    # `pool_pre_ping` 能挡大部分，但它与真正使用之间仍有窗口。定期换新连接更彻底。
    db_pool_recycle: int = 1800
    # 单条语句的服务端超时（毫秒，PostgreSQL 的 `statement_timeout`；0 = 不设）。
    # 没有它，一条漏了索引的大表扫描会一直占着连接直到把池占满——
    # 故障形态不是「变慢」，而是「同时全部停住」。sqlite 无此概念，配了也不生效。
    db_statement_timeout_ms: int = 0

    # ---------- 鉴权 ----------
    # 未携带令牌时是否回退到演示账号（None = dev/test 自动放开，prod 关闭）
    allow_anonymous: bool | None = None
    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"

    @field_validator("jwt_algorithm")
    @classmethod
    def _check_jwt_algorithm(cls, value: str) -> str:
        """算法白名单。`none` 与拼错的名字都在启动期炸掉（见 `_JWT_ALGORITHMS`）。

        这类配置的失败形态特别安静：`none` 下一切功能正常，只是令牌不再校验
        签名；拼成 `HS25G` 则要等第一次登录才报 `Algorithm not supported`。
        两种都不该等到线上才被发现。
        """
        normalized = value.strip()
        if normalized not in _JWT_ALGORITHMS:
            hint = (
                "（`none` 等于不签名，任何人都能伪造任意用户）"
                if normalized.lower() == "none"
                else ""
            )
            raise ValueError(
                f"JWT_ALGORITHM={normalized!r} 不在允许的算法列表里{hint}；"
                f"可选：{', '.join(sorted(_JWT_ALGORITHMS))}"
            )
        return normalized

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
                network = ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"TRUSTED_PROXIES 中的 {item!r} 不是合法地址/网段") from exc
            if network.prefixlen == 0:
                # `0.0.0.0/0` / `::/0` 是**语法合法**的，但语义是「信任任何人」：
                # `core.net` 在整条链都可信时会取链首地址，客户端伪造一个
                # X-Forwarded-For 就能每请求换一个限流桶，审计里的来源 IP 也全假。
                # 这类配置最要命的是「看起来生效了」，所以在启动期拒掉。
                raise ValueError(
                    f"TRUSTED_PROXIES 中的 {item!r} 覆盖全部地址："
                    "等于信任任意伪造的 X-Forwarded-For，请写具体代理地址/网段"
                )
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
    # 租约时长：job 被领走后多久没续租就认为「领的人崩了」，可以被别人接手。
    # worker 在 handler 执行期间按它的 1/4 续租（`workers.py` 的 `_renew_lease`），
    # 所以这个值**不需要**大于 handler 的最长耗时——它只决定「崩溃后多久被接走」。
    # 调小 = 故障恢复更快，代价是数据库抖动时更容易被误判为「崩了」（续租会补上）。
    queue_lease_sec: float = 300.0
    # 空队列时的轮询间隔。db 驱动每个 worker 每这么久查一次库：
    # 10 个 worker × 5 次/秒 = 50 QPS 的空查询，小库能扛；再高就该换驱动了。
    queue_poll_interval_sec: float = 0.2
    # 连续空转时退避的封顶间隔。**它同时是「空闲时入队到被取走」的最坏延迟**：
    # 默认 1 秒，比 200ms 差，换来空转查询少 5 倍；调小等于取消退避。
    # 队列里跑的是「短 job + 延迟重投」，1 秒抖动远小于一次供应商轮询。
    queue_idle_max_interval_sec: float = 1.0
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
    # 视频模板目录（卡片 + 提示词）。它是「读全量 → 改一条 → 写全量」的 JSON，
    # 必须能原子重命名，所以只能放本地盘或共享盘，不能放对象存储。
    # 留空 = `api/var/video_templates/catalog.json`；多机部署指向共享盘上的同一份文件。
    video_template_catalog: str = ""
    worker_enabled: bool = True
    worker_concurrency: int = 2
    # 周期任务调度器（模块自己 @periodic 注册）。多副本部署时每个进程都会跑一份，
    # 所以 handler 必须幂等；需要「全集群只跑一次」要先换掉 queue_driver。
    scheduler_enabled: bool = True
    # 回收站 GC 默认关闭：删除不可逆，而调度器没有分布式选主，多实例会各跑一份。
    # 单机确认后再开；间隔、保留期都可单独调。
    # 周期任务是**小时级扫描**而不是「每日 03:00」：调度器只支持间隔，没有 cron；
    # 保留期是 30 天，早一小时晚一小时对用户无差别，而漏跑一天差别很大。
    library_trash_gc_enabled: bool = False
    library_trash_gc_interval_sec: float = 3600.0
    library_trash_retention_days: float = 30.0
    # 单轮 GC 的批大小：一次最多处理多少条过期条目，避免一轮跑太久
    # 把调度器的其他任务推后（handler 超时是 every*2）。
    library_trash_gc_batch: int = 200
    # 批量下载：单次**展开后**的条目上限与字节上限。上限存在的理由不是省带宽，
    # 而是「一个人点一下就把 worker 占满十分钟」。超限返回 413，提示分批。
    library_download_max_items: int = 100
    # 2 GB：解压后总量，按资产行登记的 size_bytes 求和（不用 zip 实际大小，
    # 否则压缩率高的视频会让闸门形同虚设）。
    library_download_max_bytes: int = 2 * 1024 * 1024 * 1024
    # zip 保留时长。任务行不删，只删字节；过期后再取文件给 409。
    library_download_ttl_hours: float = 24.0
    # 过期 zip 的清扫。与回收站 GC 不同，这里删的是**我们自己生成的临时产物**，
    # 用户点开只会看到 409，所以默认开启；多实例仍靠 leader_only 收敛到一台。
    library_download_gc_enabled: bool = True
    library_download_gc_interval_sec: float = 3600.0
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
    # 按 scope 覆盖额度：JSON，`{"task.create": 120, "asset.upload": 10}`。
    # 写错（非法 JSON、scope 里带冒号、次数不是非负整数）直接启动失败；
    # 写了代码里不存在的 scope 由测试对账（`tests/test_ratelimit.py`）。
    rate_limit_overrides: str = ""
    # 整体倍率：活动期间放宽（2.0）、故障时收紧（0.5）、全关（0）。
    # 先覆盖后缩放——`SCALE=0` 必须真的全关，不能有接口从覆盖里漏出来。
    rate_limit_scale: float = 1.0

    # ---------- 备份 ----------
    # 自动备份默认关：开着的代价是每天在磁盘上多一份库、且 sqlite 下 backup API 要持读锁。
    # 生产建议开（BACKUP_AUTO_ENABLED=true），并配好保留天数。
    backup_auto_enabled: bool = False
    backup_dir: str = "var/backups"
    backup_interval_hours: int = 24
    # 保留天数：0 表示永不删除（磁盘会一直涨，只适合手动管理）。
    backup_retention_days: int = 7
    # 单个 `pg_dump` / `pg_restore` 命令的上限（秒）。**必须有**：
    # 命令卡住（库连不上、被锁、网络半开）时 `subprocess.run` 会永远等下去，
    # 而它跑在 `asyncio.to_thread` 里——取消协程并不会杀掉子进程和那个线程。
    # 一轮一轮攒下来，线程池会被占满，之后所有 `to_thread` 都排队。
    # 默认 1 小时：大于任何正常备份耗时，又小于「没人注意到」的时间尺度。
    backup_command_timeout_sec: float = 3600.0
    # `pg_dump` / `pg_restore` 所在目录。留空 = 只从进程 PATH 找。
    # 服务由计划任务 / 容器 / 桌面启动器拉起时，继承的 PATH 常常不含 PG 客户端，
    # 显式配一个目录比「装了就行」可靠；Docker 化部署尤其需要（客户端只在容器内）。
    backup_pg_bin_dir: str = ""

    # ---------- 导出 ----------
    # 单次导出的行数上限。**必须有**：没有上限的导出等于「把整张表拉进内存」，
    # 一个几百万行的任务表就能把 worker 的 RSS 打满；而 worker 是共享进程，
    # 会连带同一进程里其他模块的 job 一起 OOM。
    export_max_rows: int = 100_000
    # 产物保留时长（小时）。到期由周期任务 `export.reclaim` 删字节，**记录行保留**
    # （状态转 expired），这样「上周导过什么」查得到，只是文件没了。
    export_ttl_hours: int = 168
    # 「卡住」判定：`queued` / `running` 超过这么久没有任何推进，视为丢失并置失败。
    # 必须**大于** worker 单次 job 的超时（否则会把正常在跑的导出判成卡死），
    # 又必须**小于**「用户会来问」的时间尺度（否则记录永远挂在列表上转圈）。
    export_stale_after_sec: int = 900

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


_settings = Settings()


def get_settings() -> Settings:
    """进程内单例。**不要直接 `Settings()`**：那会得到第二套配置。

    这里刻意不是 `lru_cache`：`cache_clear()` 之后 `get_settings()` 会返回**新对象**，
    而所有 `from core.config import settings` 过的地方还绑在旧对象上——
    症状是「改了环境变量，一半代码生效、一半不生效」，而且看起来完全不合理。
    要重读环境变量请用 `reload_settings()`。
    """
    return _settings


def reload_settings() -> Settings:
    """按当前环境变量重建配置，并**原地**刷新单例。

    为什么是原地刷新而不是换对象：见 `get_settings` 的说明。原地刷新之后，
    所有已经 import 过 `settings` 的模块会一起看到新值——「一个进程只有一份配置」
    这件事必须始终成立，否则排查配置问题时看到的现场是自相矛盾的。

    **不重建 `@lru_cache` 派生的单例**（`get_archive_storage()`、`get_event_bus()`、
    `get_rate_limiter()`、`get_leader_elector()`、`get_model_router()` 等 10 个）。
    这些对象在第一次取用时就把 `settings` 里的值读走了：改了 `ARCHIVE_ROOT`
    再 reload，`settings.archive_root` 是新值，但归档盘实例还指着旧目录。

    这不是「忘了清缓存」，而是分层决定的：`core` 不能反向 import `infra` 去调
    `cache_clear()`。所以它只承诺「配置值刷新」，不承诺「派生对象重建」——
    需要换驱动/换目录的场景请重启进程（运维脚本 `app migrate-storage` 也是这么做的）。

    测试里要临时改配置，**优先 monkeypatch 那个单例对象的属性**
    （`monkeypatch.setattr(get_archive_settings(), "root", tmp)`），而不是 `setenv`
    之后指望 reload 生效——后者会得到「一半新一半旧」的现场。
    """
    fresh = Settings()
    for name in type(fresh).model_fields:
        setattr(_settings, name, getattr(fresh, name))
    return _settings


settings = _settings
