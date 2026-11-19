# 雨范 · AI 短视频工作台

面向 AI 短视频生产的工作台：任务编排、素材/分镜/模板/形象预设管理、模型目录与账号权限体系。
前后端分离，后端提供契约、前端按模块注册，**目标是「加功能时知道该改哪个文件」**，而不是能跑就行。

| 层 | 技术栈 | 目录 |
|---|---|---|
| 后端 | FastAPI · SQLAlchemy 2（async）· Pydantic v2 · Alembic · PyJWT | [`api/`](api/) |
| 前端 | Vite 5 · React 18 · TypeScript 5 · TanStack Query 5 · Tailwind 3 | [`studio/`](studio/) |
| 文档 | 扩展指南 / 接口契约 / 前端规范 / 评审清单 | [`docs/`](docs/) |

## 架构要点

- **垂直切片**：`api/src/modules/<name>/` 自带 router / schema / service / permissions / errors，
  丢进目录即被发现，`api/` 不需要任何注册代码（`pkgutil` 自动扫描 + `ModuleSpec` 契约）。
- **依赖单向**：`api（装配）→ modules → generation → db / infra / core`，业务模块之间禁止互相 import，
  由 `tests/test_architecture.py` 用 AST 静态检查兜住。
- **可替换基础设施**：存储 / 队列 / 模型供应商都是 `Protocol + 注册表`，换实现只改 `.env`。
- **契约单一来源**：`api/docs/openapi.json`、`api/docs/API.md`、`studio/src/shared/api/schema.ts`、
  `studio/src/shared/api/permissions.ts` **四份**文件由 `app export-openapi` 从同一份 OpenAPI 生成，
  前端不会拿到过期接口。

## 快速开始

后端（Python 3.11+）：

```bash
cd api
python -m venv .venv && source .venv/Scripts/activate   # Windows；macOS/Linux 用 .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
app seed --reset        # 建表 + 演示数据
app serve --reload      # http://127.0.0.1:8080/docs
```

前端（Node 20+）：

```bash
cd studio
npm install
npm run dev             # http://localhost:5173，/api 已代理到 8080
```

演示账号（`app seed` 写入，仅开发环境）：`admin / admin@12345`、`editor / editor@12345`、`viewer / viewer@12345`。

### 局域网访问

在服务器电脑的 `studio` 目录运行 `npm run dev:lan`，然后在同一局域网的设备上打开
`http://服务器的局域网IP:5173`。当前电脑地址可用 PowerShell 的 `Get-NetIPConfiguration` 查看。
该命令固定监听 `0.0.0.0:5173`，端口被占用时直接报错，不会悄悄切到另一个端口。

前端保持 `VITE_API_BASE_URL=/api/v1`、`VITE_API_PROXY_TARGET=http://127.0.0.1:8080`，
API 运行在服务器本机的 `127.0.0.1:8080`，由工作台代理请求。客户端无需访问 8080，
也无需把前端 API 地址改成客户端的 localhost。LibTV 的登录浏览器仍运行在服务器电脑。
素材与成片若使用 S3 预签名链接，`S3_ENDPOINT` 也必须是其他局域网设备能访问的地址。

Windows 防火墙需允许工作台的 TCP 5173 入站；将远端范围限制为 `LocalSubnet`。
若页面能打开但 API 不通，检查 API 服务及 Vite 代理；若整页无法打开，先检查 IP、
5173 监听、防火墙及 Wi-Fi 客户端隔离。各设备首次访问该 IP 时需要单独登录。

## 生产环境部署

生产与开发的差别集中在一点：**`APP_ENV=prod` 时后端不自动建表、不灌演示账号、不允许匿名回退**。
所以首次部署顺序是固定的：配 env → `alembic upgrade head` → `create-admin` → 启动。跳过前两步，
表现为接口 500（缺表）或没有任何账号能登录。

### 1. 配置 `api/.env`

从 `.env.example` 复制后，下面这些是**生产必须改**的（其余保持默认，逐项说明见 `api/.env.example`）：

```ini
APP_ENV=prod
DEBUG=false
JWT_SECRET=<openssl rand -hex 32 生成>     # 不用示例值
CORS_ORIGINS=https://studio.example.com   # 实际域名，不带尾斜杠

DB_URL=postgresql+asyncpg://user:pass@db-host:5432/yufan_studio
TRUSTED_PROXIES=127.0.0.1                  # 反代后面必配，否则登录限流键退化成代理地址

STORAGE_DRIVER=s3
S3_ENDPOINT=http://minio-host:9000
S3_BUCKET=yufan-assets                     # 桶保持私有，靠预签名读
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_USE_SSL=false                           # 内网 http endpoint 时关

QUEUE_DRIVER=db                            # 内存队列会让已入队任务在发版时静默消失
BACKUP_AUTO_ENABLED=true
IMAGE_ASSET_ALLOWED_HOSTS=<成片 CDN 域名>   # 两项不填，prod 启动即断言失败
LIBRARY_INGEST_ALLOWED_HOSTS=<素材来源域名>
METRICS_TOKEN=<openssl rand -hex 32 生成>   # 留空则 /metrics 返回 403
```

`STORAGE_DRIVER=s3` 而缺 `S3_ENDPOINT / BUCKET / ACCESS_KEY / SECRET_KEY` 会在启动时直接失败，
不会等到第一次上传才报错。

### 2. 初始化数据库

```bash
cd api
alembic upgrade head                              # prod 不会自动跑，必须手动
app create-admin --username admin --password '<强口令>'
app doctor --strict                               # 部署自检，含 schema 是否停在 head
app serve --host 127.0.0.1 --port 8080            # 同机反代用 127.0.0.1
```

`app serve` 没有 `--workers` 参数；要多副本就起多个进程，并先看下面的多实例约束。
不要用 `app seed --reset`——那是开发用的演示数据。

### 3. 构建前端

```bash
cd studio
npm ci
npm run build        # 产物在 studio/dist，纯静态
```

`studio/.env.production` 里 `VITE_API_BASE_URL=/api/v1` 是**相对路径**，不要改成后端的绝对地址。
让反向代理和页面同域，浏览器便无需处理跨域，`VITE_USE_MOCK` 保持 `false`。

nginx 同机反代示例：

```nginx
server {
    listen 443 ssl;
    server_name studio.example.com;
    root /srv/yufan/studio/dist;

    location / { try_files $uri $uri/ /index.html; }

    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 512m;   # 按实际素材大小调整
    }
}
```

`proxy_set_header X-Forwarded-For` 与 `TRUSTED_PROXIES` 要配套：前者传真实客户端 IP，
后者告诉应用该信任哪一段，两者缺一就会把全站请求算进同一个限流桶。

### 4. 从旧实例迁移数据（可选）

首次部署要接手旧环境的数据时，PG 与对象存储是两件独立的事，都要做：

| 对象 | 做法 | 容易踩的坑 |
|---|---|---|
| PostgreSQL | 用 `app backup` 或 `pg_dump` 导出，目标库 `pg_restore` / `app restore` | `pg_dump` 客户端大版本不要高于目标服务（PG17 的 dump 灌进 PG16 会因 `transaction_timeout` 报错）；先断掉目标库的现有连接；`alembic_version` 必须落在当前代码的版本链里，孤儿 revision 会让 `alembic upgrade` 直接卡死 |
| 对象存储 | `app migrate-storage` / `app migrate-archive`，或直接同步桶对象 | 切驱动后库里存的是对象 key、出参才按当前驱动签名；桶对象用内容校验和比对，不要用 ETag——多段上传的对象 ETag 格式与单段不同 |

迁完用 `app doctor --strict` 复核，并实际下载一两个素材确认字节可读。

### 5. 发版升级

```bash
git pull
cd api    && pip install -e ".[dev]" && alembic upgrade head && app doctor --strict
cd studio && npm ci && npm run build
```

**`alembic upgrade head` 是发版最容易漏的一步**——prod 不会替你跑。接口有改动时还要
`app export-openapi`（四份一起提交）。

### 6. 多实例部署

单实例⇄多实例不是只改进程数，下面几项要一起改，否则任务会丢或被重复执行：

- `QUEUE_DRIVER=db`：内存队列随进程消失，已入队任务在重启时静默丢失。
- `LEADER_ELECTOR=backend`：让 GC 这类不可逆任务全集群只有一个实例执行。
- `CACHE_DRIVER` 目前只有 `memory`：多实例时「每分钟 N 次」会变成 N × 实例数，
  共享缓存驱动尚未落地，要么维持单实例，要么在反代层做限流。
- `VIDEO_TEMPLATE_CATALOG` / `TEMPLATE_PACKAGER_STATE_DIR` 必须指向共享盘上的同一份。
  它们靠「读全量→改一条→写全量 + 同盘原子重命名」保证并发安全，**不能放对象存储**（没有跨进程 CAS）。

## 文档导航

| 文档 | 读它是因为 |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | **接手先读这个**：当前状态、这一轮改了什么、还差什么、本机坑与不变量 |
| [docs/REVIEW_2026-11-15.md](docs/REVIEW_2026-11-15.md) | 2026-11-15 代码审查报告：P1×3 · P2×15 · P3×15（修完一条删一条） |
| [docs/REVIEW_2026-11-19_USER_PERMISSIONS.md](docs/REVIEW_2026-11-19_USER_PERMISSIONS.md) | 2026-11-19 用户与权限专项审查：P1×1 · P2×5 · P3×4，含「权限变更无审计」「超管两套判定口径」等 |
| [docs/PERMISSION_PAGES.md](docs/PERMISSION_PAGES.md) | 页面权限（`page:*`）与接口权限为什么分两层、门控规则、新增一页要改哪三处、403 排障 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 全局形状、逐文件职责、请求/登录/生成三条主线、常见坑 |
| [docs/TEMPLATE_LIBRARY_V2.md](docs/TEMPLATE_LIBRARY_V2.md) | 模板库 v2：五向 I/O 契约、复用/删除清单、待拍板项 |
| [docs/AI_TEMPLATE_PACKAGER_V2.md](docs/AI_TEMPLATE_PACKAGER_V2.md) | AI 会话封装视频卡片：复用工作台素材包、按用途绑定编号、完整上传辅助参考图 |
| [docs/EXTENSIBILITY_GAPS.md](docs/EXTENSIBILITY_GAPS.md) | 扩展框架的问题清单（PR #1 合并暴露）：13 条缺口、优先级、以及暂缓的配置分层提案 |
| [api/README.md](api/README.md) | 后端目录、命令表、接口约定、演示数据 |
| [api/docs/EXTENDING.md](api/docs/EXTENDING.md) | 加模块 / 加供应商 / 加生成能力的标准做法，以及「什么绝对不能碰」 |
| [api/docs/API.md](api/docs/API.md) | 接口清单与字段表（前端对接主入口） |
| [api/docs/LIBTV_API.md](api/docs/LIBTV_API.md) | LibTV 上游接口（逆向实抓）：账号额度、排队位次、消费明细、批量估价 |
| [api/docs/PARALLEL_WORK.md](api/docs/PARALLEL_WORK.md) | 多人并行时怎么切分、怎么避免迁移冲突 |
| [docs/FRONTEND_EXTENSION.md](docs/FRONTEND_EXTENSION.md) | 前端目录职责、模块注册、新增页面 |
| [docs/CODE_STYLE.md](docs/CODE_STYLE.md) | 命名、分层、异常与日志约定 |

## 质量门禁

```bash
cd api    && pytest -q && ruff check src tests && mypy --platform win32 src
cd studio && npm run verify        # typecheck + lint + test + build
```

> CI 的 mypy 用 `--platform win32 src`（项目依赖 Win32 API），本地也照这个跑，别用裸 `mypy src`
> 自证清白。任何接口改动都要重跑 `app export-openapi`（四份一起提交），否则 `pytest`
> 会红着提醒你（见 EXTENDING §8）。
