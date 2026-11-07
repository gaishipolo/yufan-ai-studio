# 雨范 AI 短视频工作台 · 后端

FastAPI + SQLAlchemy 2.0（async）+ Pydantic v2 的分层后端。
与前端工程 `studio`（Vite + React + TS）配套，接口契约一一对应。

素材库与模板库已支持公共 / 个人原片、单条分享与回收站、专员封装发布及版本调用。
角色适配与验收结果见 [素材库开发交付记录](../docs/ASSETS_INTEGRATION_PLAN.md#11-t01t07-实施与交付记录2026-11-06)。
现有环境启用前先运行 `python scripts/migrate_asset_roles.py` 预览角色差异，再按交付说明显式应用。

设计目标不是"能跑"，而是**加功能时知道该改哪个文件**。

---

## 快速开始

```bash
cd api

python -m venv .venv
.venv/Scripts/activate            # Windows；macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # 可选，不建也能跑（走默认值）

app seed --reset              # 建表 + 灌演示数据
app serve --reload            # http://127.0.0.1:8080/docs
```

演示账号（`app seed` 写入，仅开发环境）：

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin@12345` | 超级管理员（`*`）|
| `editor` | `editor@12345` | 内容编辑（内容生产全量）|
| `viewer` | `viewer@12345` | 只读成员 |

```bash
curl -X POST http://127.0.0.1:8080/api/v1/identity/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin@12345"}'
```

不带令牌时，非生产环境仍回退到演示账号（便于前端联调）；生产环境必须登录。

前端联调：把 `studio/.env.development` 里的 `VITE_USE_MOCK` 改成 `false`，
Vite 会把 `/api` 代理到 `VITE_API_PROXY_TARGET`（默认 `http://127.0.0.1:8080`）。业务代码无需改动。

给前端的四份接口生成物，都由 `app export-openapi` 生成、和代码同源：

| 文件 | 怎么用 |
|---|---|
| `docs/API.md` | 直接看：约定、错误码总表、权限点、每个接口的入参/出参字段表 |
| `docs/openapi.json` | 导入 Apifox / Postman / IDE 插件，也可喂给任意代码生成器 |
| `studio/src/shared/api/schema.ts` | `import type { TaskOut } from '@/shared/api/schema'`，别手写重复类型 |
| `studio/src/shared/api/permissions.ts` | 权限点分组、权限码元组与 `PermissionCode`，菜单/按钮判定从这里派生 |

改完接口跑一次 `app export-openapi`；忘了跑的话 `pytest` 会红着提醒你。
前端消费端与手写类型由同一负责人同步，改完跑 `cd studio && npm run verify`。

常用命令：

模板替换人工验收：新增 `PUT /api/v1/templates/replacement-runs/{id}/review`，
要求 `template:read` + `template:review` 且任务归属本人；只有生成成功且结果文件存在才可评价。
任务详情返回真实评价及输入摘要；`GET /api/v1/templates/replacement-runs/{id}/inputs/{asset_id}`
读取当次归档快照。评价采用独立原子文件、版本校验和修改历史，不改生成状态。
已有角色需管理员显式配置新权限；详见 `../docs/REPLACEMENT_TEMPLATES.md`。

| 命令 | 说明 |
|---|---|
| `app serve --reload` | 开发服务器（热重载） |
| `app init-db` | 按 ORM 元数据建表（开发用） |
| `app seed --reset` | 清空并灌入演示数据 |
| `app create-admin --username admin` | 创建超级管理员（生产首启必跑） |
| `app modules` | 列出模块 / 权限点 / 生成流水线 / job handler |
| `app models` | 列出模型目录（供应商路由结果） |
| `app export-openapi` | 导出接口契约：`docs/openapi.json` + `docs/API.md` + 前端 `schema.ts` |
| `app import-archive-index [--dry-run]` | 把归档目录的 JSON 索引补进 `assets` 表（幂等，不删 JSON） |
| `app migrate-storage [--dry-run] [--delete-source] [--skip-urls]` | `local → s3` 切换前跑：把 `STORAGE_LOCAL_ROOT` 的对象按 key 搬进桶，并把库里 `/static/...` 旧地址**剥成对象 key**（地址在出参时按当前驱动现签，跑完不再随驱动失联） |
| `app migrate-blobs [--dry-run] [--delete-source]` | 归档素材改内容寻址布局（`blobs/<摘要>`），幂等；可原地跑 |
| `app migrate-archive [--dry-run]` | 归档切远端前跑：把本地归档根（`catalog`/`blobs`/`previews`/`library`）搬进当前归档驱动，幂等 |
| `app prune-blobs [--execute] [--min-age-hours 24] [--include-trash] [--trash-age-days 30]` | 回收没人引用的孤儿字节；`--include-trash` 同时彻底清理超期的软删记录（软删本身不释放空间） |
| `app verify-blobs [--all] [--sample 20]` | 读回字节重算 sha256，报告损坏/缺失对象；`key = sha256` 本身就是校验和 |
| `app rebuild-mirrors [--dry-run]` | 按 DB 幂等重建 `catalog/assets/*.json`：补缺镜像、更新 `stateVersion` 落后的、清掉无 DB 行的孤儿 |
| `app doctor [--strict]` | 部署自检（21 项）：条件写、引用来源完备性、预签名、DB/镜像一致性、镜像对账、**schema 是否停在迁移 head**、选主、旧 URL、配额、缓存读写往返、队列可达性、**outbox 积压/失败**、**指标端点**、功能开关、出站 HTTP 配置、完整性抽检 |
| `app audit-events [--action ...] [--target ...]` | 查看最近的操作留痕（分享、回收站、彻底删除、封装发布） |
| `app runs-status` | 打印运行工作区在哪、本机可见多少个档案、多少个「指针在桶里但工作区不在本机」 |
| `app backup [--out var/backups] [--note ...]` | 备份数据库：sqlite 用 backup API 取一致快照（WAL 下 `cp` 会拿到半份），postgres 走 `pg_dump`；同时写 `manifest.json`（时间、环境、schema 版本、各表行数） |
| `app restore --from-dir <dir> [--force]` | 从备份覆盖当前库；覆盖前把现场另存为 `*.pre-restore-<时间戳>`（恢复错了还能退回去） |
| `app prune-logins [--days 90]` | 清理过期的登录审计（默认取 `LOGIN_EVENT_RETENTION_DAYS`） |
| `app new-module x --label 中文名` | 生成新模块脚手架（含权限声明、写接口限流与横向能力用法表） |
| `app sync-roles [--dry-run] [--prune-snapshot]` | 全量补齐内置角色缺失的权限点（启动时只自动补「新增」的；这个命令用于手工兜底/升级前预演）。`--prune-snapshot` 顺带把权限目录快照收缩成当前声明——自动路径只增不减 |
| `app meta [key]` | 查看 `app_meta` 里的运行期状态（权限目录快照等）：第几版、谁写的、最近 50 次写入轨迹 |
| `alembic revision --autogenerate -m "xxx"` | 生成迁移（生产用） |
| `alembic upgrade head` | 执行迁移 |
| `pytest -q` | 运行测试 |
| `ruff check src tests --fix` | 静态检查与修复 |

### 对象存储（S3 / MinIO）

默认 `STORAGE_DRIVER=local`，走 `STORAGE_LOCAL_ROOT`。要接 S3/MinIO：

```bash
STORAGE_DRIVER=s3
S3_ENDPOINT=http://10.0.0.12:9000   # 自建 MinIO；AWS 官方留空
S3_BUCKET=yufan-assets
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_REGION=us-east-1
S3_USE_SSL=false                    # 内网 http endpoint 时必须关
```

驱动的写法是 path-style 寻址（MinIO 必需），`STORAGE_DRIVER=s3` 而缺
`S3_ENDPOINT/BUCKET/ACCESS_KEY/SECRET_KEY` 时**生产启动直接失败**，不会等到第一次上传才报错。

三条部署约束：

1. **桶保持私有。** `GET /assets/{id}/file` 与 `/cover` 对远程驱动返回 `307` 到
   预签名地址（默认 15 分钟），字节不经过应用进程；桶设成公开读等于把访问控制
   交给 URL 的保密性。
2. **桶侧要配 CORS。** 浏览器拿到 `307` 后是**直接向对象存储发请求**，不带我们的
   鉴权头。`AllowOrigin` 填前端来源（不要 `*` 配私有桶）、`AllowMethods` 至少
   `GET, HEAD`、`ExposeHeaders` 带上 `Content-Length, Content-Range, ETag`
   （视频拖进度条要用 `Range`）。
3. **归档盘与对象存储是两套驱动，但都能切远端。** 归档素材（`/assets/archive/*`）
   由 `ARCHIVE_DRIVER` 决定：`local` 读 `MEDIA_ARCHIVE_ROOT`，`s3` 读写
   `S3_ARCHIVE_BUCKET`。切 `STORAGE_DRIVER` 不会自动搬归档内容，反过来也一样。

本机起 MinIO（`minio/minio` 已从 Docker Hub 下架，用社区镜像）：

```bash
docker run -d --name yufan-minio -p 19000:9000 -p 19001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 \
  elestio/minio:latest server /data --console-address ":9001"
```

### 归档素材的字节布局

素材字节按 **sha256 内容寻址**：`blobs/ab/abcdef…{ext}`（前两位分桶），
预览与转码流按同一摘要命名：`previews/ab/abcdef….hd-v2.{jpg,mp4}`。带来的三个变化：

- 同一份字节登记到不同分类、被多个封装引用，**物理上只有一份**；
- "字节还是不是当初那份"不再需要检测 —— key 就是摘要，变了就是另一个 key；
  发布校验只查 key 在不在（`PackService.verify`），不再读全量重算；
- 运行快照用硬链接（`storage.link`），`run/inputs/` 里原片只占一份字节，
  不再是「`reference-video.mp4` + `source-original.mp4`」两份 300MB 副本。

存量的 `library/<分类>/<年月>/<id>.<ext>` 旧布局**不改也能继续读**（按 key 直达），
搬迁是可选动作：

```bash
app migrate-blobs --dry-run     # 先看有多少条要搬
app migrate-blobs               # 搬字节 + 改写 storage_key/cover_key + 重写镜像 JSON
app migrate-blobs --delete-source   # 确认无回滚需求后再删旧文件
```

命令幂等（已是新布局的行直接跳过）。旧文件默认保留一周作为回滚依据。

**归档驱动可以整体切远端**：`ARCHIVE_DRIVER=s3` 默认 `S3_BUCKET_MODE=separate`，
需要 `S3_ARCHIVE_BUCKET`（与 `S3_BUCKET` **分开**——归档桶不开放匿名读，素材桶是
公开读的），端点与凭据复用 `S3_*`。两条通路在 `infra/blob.py` 按连接参数共用同一个
boto3 client；设 `S3_BUCKET_MODE=shared` 则连桶也共用，归档对象统一落在 `archive/`
前缀下（**仅在桶策略已按前缀管控、没有公开读时**才可用）。切换分三步，都可回滚：

```bash
# 1. 灌数据：把本地归档根的内容搬进桶（幂等，第二遍 copied=0）
ARCHIVE_DRIVER=s3 S3_ARCHIVE_BUCKET=yufan-archive app migrate-archive --dry-run
ARCHIVE_DRIVER=s3 S3_ARCHIVE_BUCKET=yufan-archive app migrate-archive

# 2. 切配置
#    ARCHIVE_DRIVER=s3
#    S3_ARCHIVE_BUCKET=yufan-archive
#    REPLACEMENT_RUNS_ROOT=<共享盘>/runs   # 见下方「运行工作区」

# 3. 重启后核对：归档列表、缩略图、封装可用列表、任务中心
```

边界只有一条：**运行工作区必须留在本地**。`run/inputs/` 要真实文件给桥接层算
sha256、给 ffmpeg 派生预览，所以 `runs/` 不随归档进桶——索引指针在桶里
（`catalog/runs/<id>.json`，枚举走 `ListObjectsV2`），工作区目录由
`REPLACEMENT_RUNS_ROOT` 指定（留空时本地驱动沿用 `<归档根>/runs`，远端驱动落到
`api/var/runs`）。代价是「历史任务只在跑它的那台机器上可读」——要跨机器就把这个变量
指到共享盘，再用 `app runs-status` 核对「本机可见 / 总共登记」两个数字是否相等。
枚举一律走 `storage.list_keys(prefix, suffix=)`，不要在 `root()` 上 `glob`。

取件在远端驱动下走 `307`（`/assets/archive/{id}/file|thumbnail|stream|preview` →
预签名地址，`Range` 交给对象存储）；本地驱动仍是 `FileResponse`。共享协议是
`ArchiveStorage.download_url()`：本地返回 `None`，远端返回签名地址。

### 删除、引用与容量

删除是**两步**：`PUT /{id}/deleted` 只打回收站标记（可恢复），
`DELETE /{id}` 才彻底删。两步都会问「还有谁在用」——引用来源由两个模块在 import
期注册（`video_tasks.reference_asset_ids`、`MediaPack.reference_ids`），聚合口在
`core/ports/assets.py`。被引用时返回 409，`details.references` 直接列出引用方。
**新增「把素材 id 写进自己表/文件」的模块时必须同时登记一个来源**，否则删除会
静默放过它。

彻底删除会删 DB 行 + JSON 镜像 + 没人再引用的字节。内容寻址下 blob、缩略图、
转码流都由同一个 sha256 派生，所以「能不能删字节」按**还有没有别的行持有同一
摘要**判断，不是按 key 字符串——漏判一次就是别人的素材裂图。

`ARCHIVE_QUOTA_BYTES_PER_USER`（默认 0=不限）在登记时拦截 413，列表出参带回
`quotaBytes` 与 `ownerUsedBytes`（后者不受分类/分池筛选影响）。
`ARCHIVE_GC_ENABLED` 默认 **false**。不可逆删除由 `@periodic("asset.gc",
leader_only=True)` 保护：多实例部署设 `LEADER_ELECTOR=backend`，调度器会用归档存储的
条件写抢一份带 TTL 的租约，全集群只有一个进程真的删；单实例用默认 `local` 即可。
`app doctor` 在「GC 开启但选主仍是 local」时直接 FAIL。

`catalog/assets/*.json` 是 **DB 的派生投影**，不是第二份索引：业务事务先 commit，
之后才投影；`@periodic("asset.mirror")` 默认每 3600s 按 `stateVersion` 幂等对账
（补缺 / 更新落后 / 清孤儿），也可以随时 `app rebuild-mirrors` 全量重建。
投影失败不会让上传 500，下一轮对账会收敛。

### 部署自检

切到远端后先跑 `app doctor`。它检查的几件事都是「跑起来才发现就晚了」的：

- **条件写是否真生效**：写探针对象再抢第二次 `If-None-Match: *`。某些 S3 兼容
  实现会静默忽略这个头，发布版号的 CAS 随之退化成「两个实例写同一个版本」；
- **引用来源完备性**（不是数数量）：`ModuleSpec` 声明集合与注册实现对账，缺一个
  就 FAIL——删除是 fail-closed 的，漏登记会变成 503 而不是静默放行；
- DB 行与 `catalog/assets` 镜像是否一致、镜像周期对账是否开启、有没有旧的
  `/static` URL 落库、运行工作区孤儿指针、配额水位、选主策略与 GC 开关是否相配；
- `verify_blobs` 抽样读回字节重算摘要，`损坏 / 缺失 / 跳过（旧布局）` 分开报告；
- `storage.topology` 报告两条物理通路是否真的共用同一个 S3 client；
- CORS 是浏览器侧约束，服务端测不了，只在结论里提示要配的项。

镜像缺了**不要**跑 `app import-archive-index`（那是 JSON→DB 的单向导入，方向相反），
跑 `app rebuild-mirrors`。

---

## 目录结构

```
api/
├── src/
│   ├── main.py              ASGI 入口（uvicorn main:app）
│   ├── app.py               应用工厂：中间件 / 路由 / 生命周期装配
│   ├── cli.py               命令行（serve / seed / create-admin / modules / models / new-module）
│   ├── seed.py              演示数据
│   ├── workers.py           消费者骨架：并发、超时、失败重排
│   │
│   ├── core/                与业务无关的底座
│   │   ├── config.py        配置（环境变量 / .env）
│   │   ├── registry.py      通用命名注册表（所有扩展点的公共底座）
│   │   ├── events.py        进程内域事件总线
│   │   ├── permissions.py   权限点目录（由模块声明、核心汇总）
│   │   ├── context.py       request_id、当前用户与权限判定
│   │   ├── exceptions.py    错误码体系（含模块号段 ModuleErrors）
│   │   ├── schema.py        Pydantic 基座（camelCase 契约）
│   │   ├── response.py      统一响应包装与分页原语
│   │   ├── security.py      JWT / 口令哈希
│   │   └── enums.py         跨模块共享枚举
│   │
│   ├── db/                  数据访问层
│   │   ├── base.py          DeclarativeBase + UUID/时间戳混入
│   │   ├── session.py       引擎、会话、`get_session` 依赖
│   │   ├── repository.py    通用仓储（CRUD + 分页）
│   │   ├── jobs.py          queued_jobs（QUEUE_DRIVER=db 的落地表）
│   │   ├── outbox.py        outbox_events + 投递器（事件与业务同事务）
│   │   ├── audit.py         审计事件表
│   │   └── registry.py      模型注册表（自动扫描 modules.*.models）
│   │
│   ├── api/                 HTTP 层
│   │   ├── deps.py          通用依赖（会话/分页/身份/权限/基础设施）
│   │   ├── authn.py         主体解析注入点（权限实时生效，业务侧注册）
│   │   ├── discovery.py     模块自动发现（扫目录，不维护清单）
│   │   ├── module.py        ModuleSpec：模块对外的唯一契约
│   │   ├── middleware.py    request-id 与访问日志
│   │   ├── errors.py        全局异常处理器
│   │   └── v1/router.py     v1 路由聚合
│   │
│   ├── generation/          ★ 生成编排框架（AI 工作台的核心）
│   │   ├── spec.py          跨业务的统一记录视图
│   │   ├── store.py         业务侧实现的存储协议（7 个方法）
│   │   ├── pipeline.py      流水线注册表（kind → 实现）
│   │   ├── runner.py        提交 / 轮询 / 重试 / 超时 / 事件
│   │   ├── events.py        生成域事件名
│   │   └── worker.py        框架自带的两个 job handler
│   │
│   ├── modules/             业务模块（垂直切片，横向零 import）
│   │   ├── task/            ★ 参考实现：含 pipeline 适配
│   │   ├── identity/        用户与角色（登录、RBAC、权限目录）
│   │   ├── asset/ image/ template/ storyboard/ shot/ health/
│   │
│   ├── infra/               可替换实现（面向协议 + 按名加载）
│   │   ├── loader.py        按 `infra.<kind>.<name>` 解析驱动
│   │   ├── storage/         对象存储：local（s3 待实现）
│   │   ├── queue/           任务队列：memory（redis 待实现）
│   │   └── ai/              供应商：echo + 模型路由（多厂商可并存）
│   │
│   └── scaffold/            新模块脚手架（模板 + 生成器）
│
├── docs/
│   ├── API.md               ★ 接口文档（前端对接就看这份）
│   ├── openapi.json         OpenAPI 快照（导入 Apifox / Postman）
│   ├── EXTENDING.md         ★ 怎么加功能（含权限管理完整案例）
│   └── PARALLEL_WORK.md     ★ 多人并行开发与冲突规避
│
├── migrations/              Alembic 迁移
└── tests/                   pytest（含架构约束测试）
```

**导入约定**：`src/` 是导入根（`pyproject.toml` 的 `pythonpath`／wheel `sources`），
所以 `core` / `db` / `api` / `infra` / `generation` / `modules` / `scaffold` 都是**顶层包**，
import 时不带任何前缀：`from core.config import settings`、`from modules.task.models import VideoTask`。

**依赖方向**：`api → modules → generation → db / infra / core`，只允许向下。
业务模块之间不直接 import，共享词汇上提到 `core`（如 `AspectRatio`）、
共享流程下沉到 `generation`。
模块只能引用 `api` 的**装配点**（`api.module` / `api.deps` / `api.accounts` / `api.authn`），
引用其它 `api.*` 会形成反向边；跨模块的只读数据通过 `core` 里的注册表注入（见 `core/feed.py`）。
这三条由 `tests/test_architecture.py` 用 AST 静态检查强制执行。

---

## 一个业务模块长什么样

以 `task` 为例，四层职责清晰：

| 文件 | 职责 | 不做什么 |
|---|---|---|
| `models.py` | ORM 实体、索引、约束 | 不含业务判断 |
| `schema.py` | 出入参、字段校验 | 不碰数据库 |
| `service.py` | 业务规则、事务边界 | 不拼 SQL、不碰 HTTP |
| `router.py` | HTTP 协议转换、参数声明 | 不写业务逻辑 |
| `worker.py` | 异步任务消费 | 不依赖请求上下文 |

调用链：`router → service → repository → ORM`，异常由 `api/errors.py` 统一兜底，业务代码不写 try/except。

---

## 新增一个模块（一条命令）

```bash
app new-module comment --label 评论 --perm comment --slot 9
```

生成 `modules/comment/` 七件套后，**服务已经能跑**：模块被自动发现、权限点进入目录、
表由 `create_all` 建好、接口返回 `501 + "评论列表待实现"`。

不需要改路由聚合、不需要改模型注册表、不需要改依赖文件。
只剩三件正常开发要做的事：写 service、生成迁移、`app modules` 确认。

完整方法论见 **[docs/EXTENDING.md](docs/EXTENDING.md)**，包括：

- 用户权限管理的完整落地步骤（用户表 / 角色 / 权限展开 / 登录签发）
- 接入新模型供应商
- 新增一种生成能力（音乐 / 配音 / 3D）
- 加完功能要同步更新的三份契约文件（`openapi.json` / `API.md` / `schema.ts`）
- 错误码号段分配表
- 提交前检查清单

多人同时开工的约定见 **[docs/PARALLEL_WORK.md](docs/PARALLEL_WORK.md)**。

---

## 接口契约

**统一响应**（与前端 `shared/api/types.ts` 的 `ApiResponse<T>` 一致）：

```json
{ "code": 0, "message": "OK", "data": { } }
```

`code == 0` 为成功；非 0 时前端直接展示 `message`。
错误码规则 `code = HTTP状态码 × 100 + 模块号段 × 10 + 序号`（号段分配见 `EXTENDING.md` 第 7 节），
因此 `code // 100` 恒等于 HTTP 状态码。

**字段命名**：出入参一律 camelCase（`durationSec`、`coverUrl`），由 `CamelModel` 自动转换，Python 侧仍写 snake_case。

**时间格式**：ISO8601 带时区（`2026-11-05T02:52:01.850643Z`），
sqlite 读出的 naive 时间在序列化时按 UTC 补齐，避免前端时区偏移。

**分页**：`?page=1&pageSize=24` → `{ items, total, page, pageSize }`。
任务列表额外返回 `stats`（各状态计数，不受状态筛选影响）。

**现有路由**：

| 方法 | 路径 | 状态 |
|---|---|---|
| GET | `/api/v1/health`、`/health/ready` | ✅ |
| GET | `/api/v1/tasks`（列表 + stats） | ✅ |
| GET | `/api/v1/tasks/stats` | ✅ |
| GET | `/api/v1/tasks/{id}` | ✅ |
| POST | `/api/v1/tasks` | ✅ |
| PATCH | `/api/v1/tasks/{id}` | ✅ |
| POST | `/api/v1/tasks/{id}/retry` | ✅ |
| POST | `/api/v1/tasks/{id}/favorite` | ✅ |
| DELETE | `/api/v1/tasks`（body: `{ids}`） | ✅ |
| GET | `/api/v1/assets` | ✅ |
| GET | `/api/v1/templates`、`/storyboards`、`/shots`、`/images` | ✅ |
| GET | `/api/v1/models?capability=video` | ✅ 模型目录（含参数 schema） |
| POST | `/api/v1/generations/image` | 🚧 501 占位 |
| POST | `/api/v1/assets`（上传凭证）、`DELETE /assets/{id}` | 🚧 待实现 |

未实现的方法统一抛 `NotImplementedFeature`，返回 501 + 明确文案，前端不会误判为崩溃。
每个待实现方法内部都有 `TODO(...)` 注释，标明需要补齐的步骤。

**鉴权**：除 `/health` 外，所有接口都要求权限点（`ModuleSpec.permissions` 声明 →
`require("asset:read")` 挂在路由上）。新增模块必须声明权限点，
`tests/test_architecture.py` 会拦住「能登录就能访问」的裸奔模块。

**数据范围**：列表的 `scope=all` / 详情 / 删除共用同一个权限点 `task:read:all`，
没有它就一律按「只看自己」处理（不是报错，是安全降级）。
重试他人任务单独要 `task:retry:any`：那是「再花一次钱」的动作，
不该由「能看全部」顺带解锁。
换其它角色（运营、审核）只需在登录时把该权限码展开进令牌，
业务代码里没有也不应该有 `"admin"` 这类角色名判断。

**创建校验**：`POST /tasks` 会用模型目录校验 `model` / `durationSec` / `aspectRatio`
（取值来自供应商声明的 `paramSchema`），非法请求当场 400 并返回可选值。
不传模型与时长则取目录默认值——写死默认值会在换模型那天变成错值。

---

## 扩展点地图

加任何东西之前，先在这张表里找落点。**所有落点都要求"只加文件，不改核心"。**

| 要加什么 | 加在哪 | 核心代码动几行 |
|---|---|---|
| 业务模块 | `modules/<name>/`（`app new-module`） | 0 |
| 权限点 | 模块自己的 `ModuleSpec.permissions` | 0 |
| 错误码 | 模块内 `ModuleErrors("x", slot=n)` | 0 |
| 模型供应商 | `infra/ai/<name>.py` + `AI_PROVIDERS` | 0 |
| 存储 / 队列驱动 | `infra/<kind>/<name>.py` + 改配置 | 0 |
| 生成能力（视频/图片/音乐） | 模块内 `@register_pipeline` | 0 |
| 横向功能（通知/计费/审计） | 订阅 `generation.*` 事件 | 0 |
| 定时任务 | `@register_handler("xxx")` | 0 |

### 生成任务：为什么分成「提交 / 轮询」两个 job

视频生成要几十秒到几分钟。如果在 handler 里 `await sleep(2)` 轮询，
一个并发槽位就被单个任务占住几分钟 —— 2 个并发只能跑 2 个任务。

本项目的做法是**延迟投递**：

```
dispatch → generation.submit
              ├─ provider.submit() → 写 external_id
              └─ enqueue(generation.poll, delay=2s)
                     ├─ provider.poll() → 写进度
                     ├─ enqueue(generation.poll, delay=2s)   ← 重新入队，不是 sleep
                     └─ 成功/失败 → 写终态 + 发事件
```

槽位只被"真正在调 API 的那几十毫秒"占用，同样并发可以支撑成百上千个在途任务。
`Job` 还带幂等键，重复入队只保留一条，避免同一任务重复扣费。

### 换模型供应商

`infra/ai/base.py` 只有一个协议 `ModelProvider`（`submit` / `poll` / `cancel`），
视频、图片、音频都是同一套范式。新增厂商：

```python
# infra/ai/volcengine.py
class VolcengineProvider:
    name = "volcengine"
    capabilities = frozenset({Capability.VIDEO})
    models = (ModelSpec(id="seedance-pro", label="Seedance Pro",
                        capability=Capability.VIDEO, provider="volcengine"),)
    async def submit(self, request): ...
    async def poll(self, external_id, *, model=""): ...
    async def cancel(self, external_id): ...

Driver = VolcengineProvider
```

改 `.env` 的 `AI_PROVIDERS=echo,volcengine` 即可，**多个供应商可并存**，
按 model 自动路由（`ModelRouter` 同时认 id 和中文 label）。

### 接鉴权

`api/deps.py` 的 `get_actor` 已解析 JWT 并填充 `CurrentUser(permissions=...)`，
所有业务路由都用 `require("资源:动作")` 声明权限。
接上用户表 + 登录接口后，把 `ALLOW_ANONYMOUS=false`，
权限校验自动开始生效 —— 业务模块一行都不用改。

权限管理怎么做，见 [docs/EXTENDING.md](docs/EXTENDING.md) 第 2 节。

---

## 几个设计决策

**为什么 `code` 不直接用 HTTP 状态码？**
前端需要在 `code != 0` 时统一弹提示，同时保留细分的业务错误（如"token 过期"与"token 非法"都是 401）。
用 `HTTP×100 + 序号` 兼顾两者，且不必维护两套映射表。

**为什么时间由 Python 生成而不是数据库 `now()`？**
sqlite 与 postgres 的时区行为不一致（sqlite 忽略 tzinfo），交给应用层可保证跨库格式统一、排序正确。

**为什么 service 显式 `commit()`？**
隐式事务边界在组合调用时容易出错；显式提交让"一次业务操作 = 一次提交"这条规则可见。
依赖层在异常时负责回滚（`db/session.py::get_session`）。

**为什么 mock 队列默认内存实现？**
本地克隆下来 `pip install` 完就能跑通「创建 → 排队 → 生成中 → 完成」全链路，不需要先装 Redis。
生产切换成本只是换一个 `JobQueue` 实现。

**重启后卡在"生成中"的任务怎么办？**
`worker.resume_unfinished_tasks()` 在启动时把 `queued/running` 的任务重新入队。
真实场景应区分：有 `external_id` 的继续轮询供应商（幂等），没有的才重新提交 —— 注释里已标明。

---

## 测试

```bash
pytest -q          # 全部用例
ruff check src tests
```

测试分四类：

| 文件 | 覆盖内容 |
|---|---|
| `test_health.py` | 健康检查、响应封装 |
| `test_task_api.py` | 任务 CRUD、校验、筛选排序、错误码 |
| `test_generation.py` | 延迟投递、幂等键、退避重试、事件失败隔离 |
| `test_architecture.py` | **架构约束**：依赖方向、模块自注册、权限声明、错误码一致性 |

`test_architecture.py` 用 AST 解析源码，会拦住这些改动：
底层（core/db/infra/generation）import 业务模块、业务模块之间横向 import、
新增模块忘记导出 `ModuleSpec`、错误码与 HTTP 状态码不一致。

测试用独立临时 sqlite 库 + 关闭 worker，`conftest.py` 在导入应用包前锁定环境变量
（配置是 import 期单例，这一点很关键）。

---

## 生产部署清单

- [ ] `APP_ENV=prod`、`DEBUG=false`、`ALLOW_ANONYMOUS=false`
- [ ] `DB_URL` 换 postgres：`postgresql+asyncpg://user:pass@host:5432/app`，装 `pip install -e ".[postgres]"`
- [ ] `JWT_SECRET` 用 `openssl rand -hex 32` 重新生成
- [ ] 用 `alembic upgrade head` 建表，不要依赖自动建表（`app_env=prod` 时已自动关闭）
- [ ] `STORAGE_DRIVER=s3` + `S3_*` 四项必填；桶保持私有，并按上文配好 CORS。
      继续用本地盘就把 `STORAGE_LOCAL_ROOT` 指到一个**独立于代码目录**的持久卷。
- [ ] 从本地盘切到 s3 时先跑 `app migrate-storage --dry-run` 核对文件数，再去掉
      `--dry-run`。它做两件事：按 key 搬对象、把 `image_jobs` 里 `/static/...` 的
      旧地址剥成对象 key。生成结果地址不再落库，出参时按当前驱动现拼
      （`/static` 或 `307` 预签名），所以私有桶也不会 403。
- [ ] 部署后跑一次 `app doctor`（有 FAIL 直接非零退出）：它会实测
      `If-None-Match` 条件写、预签名可达性、DB/镜像一致性与字节完整性，
      而不是等第一次并发发布或第一次裂图才暴露。
- [ ] `app prune-blobs --include-trash` 挂周期任务前先确认是单机部署；
      多实例请保持 `ARCHIVE_GC_ENABLED=false`，改由运维在单机上定时执行。
- [ ] 从旧归档盘升级时跑一次 `app import-archive-index --dry-run`，确认
      「新建 / 缺文件 / 冲突」的数量后再去掉 `--dry-run`。命令幂等，可重复执行。
- [ ] `QUEUE_DRIVER=db`（生产建议）：job 落 `queued_jobs` 表，重启不丢在途任务，
      崩溃任务由租约到期被其它实例接手。内存驱动在发版时会**静默丢掉**已入队未执行的任务。
      上规模（每秒几十个 job 以上）再实现 `infra/queue/redis.py` 并切 `QUEUE_DRIVER=redis`，
      业务代码不用改（见 `docs/EXTENDING.md` §5.2.4）。
- [ ] `METRICS_TOKEN` 配一个随机串（`openssl rand -hex 16`）。生产环境留空时
      `/metrics` 直接 403——面板会全空，而 `app doctor` 的 `runtime.metrics` 会提示。
      抓取配置用 `Authorization: Bearer <token>`，不要用 `?token=`（会进访问日志）。
- [ ] 接上 `/health/ready`（别只接 `/health`）：`down` 摘实例、`degraded` 只告警。
      后者表示「能服务但在漏」（outbox 积压、死信超阈值、缓存不可用），
      把它当 down 会让编排系统一次摘掉整批实例。
- [ ] 备份：`BACKUP_AUTO_ENABLED=true` + `BACKUP_RETENTION_DAYS=7`（`@periodic("core.backup")`
      每日一份，`leader_only` 保证多副本只跑一个）。注意它只备**数据库**，
      对象存储的字节靠存储侧版本化/跨区复制。备份要**演练**：拿一份新备份
      `app restore --force` 到临时环境，确认表行数与 `manifest.json` 一致——
      没演练过的备份不算备份。
- [ ] 全局限流按需开：`RATE_LIMIT_GLOBAL_PER_MIN` 默认 0（关闭）。它按客户端 IP 计，
      反代/NAT 后面一个出口 IP 代表很多真实用户，开之前确认部署层已正确透传真实 IP。
- [ ] 想按环境调额度（活动放宽、故障收紧、临时全关）用 `RATE_LIMIT_OVERRIDES` /
      `RATE_LIMIT_SCALE`，不用改代码（重启生效；写错 `app doctor` 会红），
      见 `docs/OPS.md` §6.1。
- [ ] `WORKER_ENABLED=false` 时单独起 worker 进程，避免 API 实例与 worker 抢队列
- [ ] 反向代理（Nginx）转发到 8080，并透传 `X-Request-Id`
- [ ] 有反代就配 `TRUSTED_PROXIES=<代理地址/网段>`（或给 uvicorn 加 `--proxy-headers
      --forwarded-allow-ips`，二选一）。不配的话所有请求的直连地址都是代理，登录限流键会退化
      成「只按用户名」，一个人就能把别人锁在外面；反过来即使不配，伪造 `X-Forwarded-For`
      也不会生效——默认不信任任何代理。**不要**配成 `0.0.0.0/0`：XFF 里的地址由客户端
      自报，全信任等于限流键可随意伪造。
- [ ] 不要在启动脚本里留 `app seed`：`APP_ENV=prod` 时它会拒绝执行（演示账号口令固定），
      生产首启只用 `app create-admin`。
- [ ] 给 `app prune-logins` 挂一条日任务（cron / systemd timer）：`login_events` 只增不减，
      不清会一直长。保留天数看审计要求，默认 90 天（`LOGIN_EVENT_RETENTION_DAYS`）。
- [ ] 多实例部署**不需要**额外配置：会话与吊销判定都在数据库里，没有进程内状态。
      两处例外要知道：限流计数在 `CACHE_DRIVER` 里（memory 时各实例各算各的，
      要精确就换共享缓存，见 `docs/EXTENDING.md` §5.2.3）；不可逆的周期任务
      （如 `asset.gc`）要配 `LEADER_ELECTOR=backend` 让全集群只有一个实例执行。

前三条**不需要靠人记**：`Settings` 上有 `model_validator`，`APP_ENV=prod` 时
匿名放开、样例/过短 JWT 密钥、`CORS_ORIGINS=*`、`DEBUG=true` 任意一条命中都会
直接启动失败并打印原因（`tests/test_config_guard.py` 覆盖）。
