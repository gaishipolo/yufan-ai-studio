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
所以顺序是固定的：配 env → 装依赖 → `alembic upgrade head` → `create-admin` → 构建前端 → 启动。
跳过前三步的表现分别是：守卫拒绝启动、接口 500（缺表）、没有任何账号能登录。

部署形态是**单端口**：后端在 8080 上同时发页面和接口，不再需要另起 5173，也不要装 nginx。
想改回「nginx 发静态 + 反代」的老方案，看 §3 的 B。

### 1. 配置 `api/.env`

从 `.env.example` 复制后，下面这些是**生产必须改**的（其余保持默认，逐项说明见 `api/.env.example`）；
仓库里另有一份填好真实值的 `api/.env.production.local`（被 `.gitignore` 忽略，不进版本库），
部署时可以直接拷成 `api/.env`，再核对下面加注释的那几行。

```ini
APP_ENV=prod
DEBUG=false
JWT_SECRET=<openssl rand -hex 32 生成>     # 不用示例值
CORS_ORIGINS=https://studio.example.com   # 页面地址栏那个 origin（同源托管时不参与），不带尾斜杠
WEB_DIST_DIR=studio/dist                 # 后端同时发页面；留空 = 只发接口（交给 nginx）

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

`MEDIA_ARCHIVE_ROOT` / `TASK_RUNS_ROOT` 是**真实目录**（归档工作区、生成任务的 inputs/outputs），
不能放对象存储；单机留空就用 `api/var`，一旦换机器或上多实例必须指向共享盘上的同一份。

### 2. 首次部署：照顺序做一遍

下面的命令以 Windows 生产机为例（仓库在 `D:\PythonFile\yufan-ai-studio`，按实际路径替换；
后端统一用 `api\.venv` 里的解释器）。目标是**只开 8080 一个端口**：页面和接口都在这上面。

#### 2.1 拉代码、装依赖

```powershell
cd D:\PythonFile\yufan-ai-studio
git status                      # 生产上不该有本地改动；有就说明有人手改过文件
git pull

cd api
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

#### 2.2 迁库、建超管、自检

```powershell
# prod 不会自动建表：这步漏了，接口全 500（缺表 / 缺列），日志里看不出是"忘了迁移"
.venv\Scripts\python.exe -m alembic upgrade head

# 首次部署跑一次，之后改密走界面
.venv\Scripts\app.exe create-admin --username admin --password '<强口令>'

# 自检：schema 是否停在 head、S3 四项是否齐、预签名能不能读、归档是否完整
.venv\Scripts\app.exe doctor --strict
```

`doctor --strict` 红了就别急着起服务——它拦的都是「起来了也用不了」的问题。

#### 2.3 构建前端

```powershell
cd ..\studio
npm.cmd ci          # 用 .cmd 垫片，见 §7 第 1 条（PowerShell 会拦 npm.ps1）
npm.cmd run build   # 产物在 studio\dist
```

`studio/.env.production` 里 `VITE_API_BASE_URL=/api/v1` 是**相对路径**，不要改成后端的绝对地址；
`VITE_USE_MOCK` 保持 `false`。`dist` 不进版本库，每台生产机都要自己构建一次。

#### 2.4 启动

```powershell
cd ..\api
.venv\Scripts\python.exe -m cli serve --host 0.0.0.0 --port 8080
```

- **`--host 0.0.0.0` 才能被局域网访问**；写 `127.0.0.1` 只有本机打得开，症状是「我这台明明是好的」。
- 用计划任务跑 `api\start-backend.cmd` 的：把里面那行 `--host 127.0.0.1` 改成 `0.0.0.0`。
  它是仓库文件，改完 `git status` 会显示 modified——下次 `git pull` 前先改回去，或让计划任务直接调 `cli serve`。
- 防火墙只对内网放行：

```powershell
New-NetFirewallRule -DisplayName "Yufan Studio 8080" -Direction Inbound -Protocol TCP `
  -LocalPort 8080 -RemoteAddress LocalSubnet -Action Allow
```

`app serve` 没有 `--workers` 参数；要多副本先看 §8。不要用 `app seed --reset`——那是开发用的演示数据。

#### 2.5 验收（别只看端口起来了）

1. `curl http://<内网IP>:8080/api/v1/health` → 200
2. 浏览器打开 `http://<内网IP>:8080` → 出现**登录页**。若拿到的是一段 JSON，说明 `WEB_DIST_DIR` 没配或 `dist` 没构建
3. admin 登录、进资产库能看到列表 —— 接口与库都通
4. 打开一张素材、预览图能显示 —— S3 预签名通
5. 跑一条最小的图任务直到出图 —— worker / 队列 / 供应商链路通
6. 刷新任意前端路由（如 `/tasks`）不 404 —— SPA 回退生效
7. 用到 LibTV 替换模型时，再跑一条替换任务直到出片 —— 浏览器、CDP、上游都通（见 §2.6）

#### 2.6 LibTV 登录浏览器（用到替换模型才需要）

后端自己不登录 LibTV，它通过 CDP 连一个**已经登录好的 Chrome**。这台机器上常驻两个，
端口与 `api\.env` 里 `LIBTV_BROWSER_URLS=http://127.0.0.1:9411,http://127.0.0.1:9412`
一一对应：9411 是免费队列账号，9412 是积分队列账号（`LIBTV_CREDITS_BROWSER_URL` 指向它）。

**它与后端是两个独立进程**：后端已经在跑时，只补这一个即可（脚本只管浏览器，不碰 8080/5173）：

```powershell
cd D:\PythonFile\yufan-ai-studio
powershell -ExecutionPolicy Bypass -File .\start-services.ps1 -BrowsersOnly
```

- **登录态存在 `--user-data-dir` 指向的目录里，没别的地方**。两个账号必须用**不同**目录
  （同一个目录等于两个账号其实是同一个账号）。默认落在 `api\var\libtv-chrome-profile`
  与 `api\var\libtv-chrome-profile-2`；沿用已经登录过的目录就显式传
  `-Account1Profile` / `-Account2Profile`，别让它新建一个空的。
- 目录是新的 → 两个窗口首次都要人工扫码登录一次（脚本会提示 `Profile ... is new`）。
  登录完这个目录就别删、别换，之后重启直接复用。
- **注销 Windows 会连带结束这两个 Chrome**，之后所有 LibTV 任务报「执行器浏览器未连接
  （CDP …）」。RDP 断开（不是注销）不受影响。要无人值守就建成计划任务、触发器选
  **「用户登录时」**——Chrome 需要交互式桌面会话，别用「不管用户是否登录都运行」。
- 验证：`curl http://127.0.0.1:9411/json/version` 返回 JSON 就说明 CDP 活着；
  账号本身的状态用 `app libtv-account --account http://127.0.0.1:9412`。

### 3. 前端产物由谁发

`studio/dist` 有两种发法，**内网 / 单机部署推荐第一种**。

**A. 后端一起发（一个端口，不用 nginx）**

`.env` 里加一行，然后照常 `app serve`，直接开 `http://<host>:8080`：

```
WEB_DIST_DIR=studio/dist          # 相对路径按仓库根解析，仓库放哪个盘都不用改
```

页面、`/assets/*`、`/api/v1/*` 全在 8080 上，同源所以不会再出现跨域问题；
前端路由（如 `/tasks/T001`）刷新会回退到 `index.html`，接口路径写错仍是 404 JSON。
留空则退回「只发接口」的老行为。

只改前端时重新 `npm run build` 即可，**后端不用重启**（产物按请求读盘：
首页带 `no-cache` 每次回源，带 hash 的资源靠文件名区分版本）。

**B. nginx 发静态、反代 `/api`**

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

两种发法都别在服务器上跑 `npm run dev`：那是开发服务器（带热更新、按需编译、
单进程），拿它当生产服务器会慢，而且 `CORS_ORIGINS` 得为它额外开口子。


### 4. 从旧实例迁移数据（可选）

首次部署要接手旧环境的数据时，PG 与对象存储是两件独立的事，都要做：

| 对象 | 做法 | 容易踩的坑 |
|---|---|---|
| PostgreSQL | 用 `app backup` 或 `pg_dump` 导出，目标库 `pg_restore` / `app restore` | `pg_dump` 客户端大版本不要高于目标服务（PG17 的 dump 灌进 PG16 会因 `transaction_timeout` 报错）；先断掉目标库的现有连接；`alembic_version` 必须落在当前代码的版本链里，孤儿 revision 会让 `alembic upgrade` 直接卡死 |
| 对象存储 | `app migrate-storage` / `app migrate-archive`，或直接同步桶对象 | 切驱动后库里存的是对象 key、出参才按当前驱动签名；桶对象用内容校验和比对，不要用 ETag——多段上传的对象 ETag 格式与单段不同 |

迁完用 `app doctor --strict` 复核，并实际下载一两个素材确认字节可读。

### 5. 每次发版

```powershell
# 1. 停服务（任务名以本机为准：schtasks /Query /FO LIST | findstr Yufan）
schtasks /End /TN "YufanWorkbench"

# 2. 拉代码
cd D:\PythonFile\yufan-ai-studio
git status          # 应该干净；start-backend.cmd 的 host 改过就先改回去
git pull

# 3. 依赖 → 迁库 → 自检（顺序别换：先迁库，doctor 才验得到 schema 在 head）
cd api
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\app.exe doctor --strict

# 4. 重建前端（只改前端时，1–3 可以跳过）
cd ..\studio
npm.cmd ci
npm.cmd run build

# 5. 起服务
cd ..\api
.venv\Scripts\python.exe -m cli serve --host 0.0.0.0 --port 8080
```

- **`alembic upgrade head` 是发版最容易漏的一步**——prod 不会替你跑。
- 只改前端：`npm.cmd run build` 就够了，**后端不用重启**（产物按请求读盘）。
- **LibTV 那两个 Chrome 不用重启**（§2.6）：它们与后端进程无关，关了就得重新扫码登录。
- 接口有改动时还要 `app export-openapi`（四份一起提交）。
- 发版后照 §2.5 走一遍验收，至少第 1、2、5 条。

### 6. 回滚

```powershell
git log --oneline -8                     # 找到上一个可用提交
git reset --hard <commit>

cd studio; npm.cmd run build             # 页面也要跟着回退，别只回后端

cd ..\api
.venv\Scripts\app.exe restore --from-dir var\backups\<时间戳目录> --force
```

- `--force` 是「覆盖现有数据」的显式确认；`restore` 会先把现场另存一份，不是直接抹掉。
- 备份默认每 24 小时一次、保留 7 天（`BACKUP_AUTO_ENABLED` / `BACKUP_RETENTION_DAYS`）。
- **标了不可逆的迁移别指望 `alembic downgrade`**：`downgrade()` 会直接抛错，只有备份能回。
  先看 `api/migrations/versions/` 里 `irreversible = True` 的那几个。

### 7. 常见故障对照

| 现象 | 最可能的原因 | 处置 |
|---|---|---|
| `npm` 报「因为在此系统上禁止运行脚本」 | PowerShell 执行策略拦的是 `npm.ps1` 垫片 | 用 `npm.cmd`（或 `cmd /c "npm ..."`）；要长期解决：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| 本机能开、同事打不开 | `--host 127.0.0.1`，或防火墙没放行 | 改 `0.0.0.0` + 放行 8080 入站（§2.4） |
| 接口 500，日志提缺列 / 缺表 | 忘了 `alembic upgrade head` | 补跑（§2.2） |
| 打开 8080 是一段 JSON | `WEB_DIST_DIR` 没配，或 `dist` 没构建 | 配 `studio/dist` 并 `npm.cmd run build` |
| 白屏、控制台 404 某个 `index-*.js` | 浏览器缓存了旧的 `index.html` | 强刷即可；首页已带 `no-cache`，之后不会再出现 |
| 所有 LibTV 替换任务在 upload 阶段报 `RELEASE_INVALID`，而 `git diff` 是空的 | vendor 被检出成 CRLF | 删 `api\src\modules\model_driver\libtv\runtime\vendor` 再 `git checkout --` 该目录，跑 `pytest tests/test_libtv_vendor_manifest.py -q` |
| LibTV 任务立刻失败，报「执行器浏览器未连接（CDP http://127.0.0.1:9411…）」 | 那两个 Chrome 没在跑（重启机器 / 注销过 / 窗口被关） | 按 §2.6 起；`curl 127.0.0.1:9411/json/version` 先确认 CDP 活着 |
| 页面能开、所有接口报 CORS | 页面被挪到了另一个源（别的端口 / 域名 / 主机名） | 把**那个页面地址**加进 `CORS_ORIGINS`——是浏览器地址栏，不是服务器 IP |
| `npm ci` 卡在下载 | 生产机不通外网 | 在有网机器上 `npm ci`，整包拷 `node_modules`（同 OS / 架构、Node 大版本一致） |
| 用户列表里一堆 `smoke_xxx`，界面上删不掉 | 接口只有「停用」，没有物理删除（历史任务的作者还指着它） | `app purge-users --pattern 'smoke_*'` 先预演，再 `--delete`（见下） |

**清理冒烟测试留下的账号**：`purge-users` 默认只预演，不动库。

```powershell
cd D:\PythonFile\yufan-ai-studio\api
.venv\Scripts\app.exe purge-users --pattern 'smoke_*'                 # 预演：列出账号与关联行
.venv\Scripts\app.exe purge-users --pattern 'smoke_*' --deactivate    # 停用 + 吊销会话（可再启用）
.venv\Scripts\app.exe purge-users --pattern 'smoke_*' --delete        # 物理删除（不可恢复）
```

`*` 是任意串、`?` 是单字符，其余字符按字面（`smoke_` 里的 `_` 不会被当成通配）。
`--delete` 的三道闸门：名下有任务 / 素材等内容时默认拒绝（加 `--allow-orphans`
才继续，那些行不会删，只会失去作者）；命中超级管理员要 `--include-admins`；
模式写成 `*` 直接拒绝。执行后会写一条 `identity.user.purge` 审计。

### 8. 多实例部署

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
