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
- **契约单一来源**：`docs/openapi.json`、`docs/API.md`、`studio/src/shared/api/schema.ts`
  三份文件由 `app export-openapi` 从同一份 OpenAPI 生成，前端不会拿到过期接口。

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

## 文档导航

人物 / 产品 / 场景替换与 Z 盘素材归档，见 [替换模板使用与开发说明](docs/REPLACEMENT_TEMPLATES.md)。

| 文档 | 读它是因为 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **新同事先读这个**：全局形状、逐文件职责、请求/登录/生成三条主线、常见坑 |
| [docs/CHAIN_REPLACEMENT.md](docs/CHAIN_REPLACEMENT.md) | 产品替换从点击到成片的完整链路：12 跳逐文件、状态机、共享盘产物、失败恢复 |
| [docs/AI_TEMPLATE_PACKAGER_V2.md](docs/AI_TEMPLATE_PACKAGER_V2.md) | AI 会话封装视频卡片：复用工作台素材包、按用途绑定编号、完整上传辅助参考图 |
| [docs/EXTENSIBILITY_GAPS.md](docs/EXTENSIBILITY_GAPS.md) | 扩展框架的问题清单（PR #1 合并暴露）：13 条缺口、优先级、以及暂缓的配置分层提案 |
| [api/README.md](api/README.md) | 后端目录、命令表、接口约定、演示数据 |
| [api/docs/EXTENDING.md](api/docs/EXTENDING.md) | 加模块 / 加供应商 / 加生成能力的标准做法，以及「什么绝对不能碰」 |
| [api/docs/API.md](api/docs/API.md) | 接口清单与字段表（前端对接主入口） |
| [api/docs/PARALLEL_WORK.md](api/docs/PARALLEL_WORK.md) | 多人并行时怎么切分、怎么避免迁移冲突 |
| [docs/FRONTEND_EXTENSION.md](docs/FRONTEND_EXTENSION.md) | 前端目录职责、模块注册、新增页面 |
| [docs/BACKEND_DELIVERY_IMAGE.md](docs/BACKEND_DELIVERY_IMAGE.md) | **前端接生图先读这个**：两个实测会 100% 失败的写法、异步语义与轮询、权限点改用生成物 |
| [docs/CODE_STYLE.md](docs/CODE_STYLE.md) | 命名、分层、异常与日志约定 |
| [docs/BACKEND_REQUIREMENTS_IDENTITY.md](docs/BACKEND_REQUIREMENTS_IDENTITY.md) | 身份与会话还缺什么：续期、登出吊销、审计、密码策略（**给后端的需求单**） |

## 质量门禁

```bash
cd api    && pytest -q && ruff check src tests && mypy src
cd studio && npm run verify        # typecheck + lint + test + build
```

> 任何接口改动都要重跑 `app export-openapi`，否则 `pytest` 会红着提醒你（见 EXTENDING §8）。
