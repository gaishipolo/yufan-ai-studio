# 天火工作台 · 代码规范

> 版本 v1.0 ｜ 适用：`api`（FastAPI / Python 3.11）、`studio`（React 18 / TS 5）
> 配套文件：[`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 这份文档怎么用

规范分两级，写代码前请先分清哪条能被 CI 拦住：

| 级别 | 含义 | 违反后果 |
|---|---|---|
| **硬性（MUST）** | 结构、契约、安全、事务边界 | CI 报错或评审直接打回 |
| **建议（SHOULD）** | 可读性、组织方式、性能习惯 | 评审提 🟡，不阻塞合并 |

已经被工具管住的事（缩进、引号、import 排序、行宽）本文不重复讲，交给 `ruff` / `tsc`。
本文只写**工具判断不了、但会让人踩坑**的部分。

---

## 0. 三条底线

先记住这三条，它们覆盖了本仓库 80% 的返工：

1. **依赖只能单向流动**：`modules → db / core / infra`，前端 `pages → widgets → features → entities → shared`。反向 import 一律不允许，绕路也属于违规。
2. **契约只有一份定义**：后端 `schema.py` 是接口的唯一真相，前端 `model/types.ts` 是它的镜像。改字段必须同一次改动里两边都改。
3. **一次业务操作 = 一次事务提交**，提交发生在 `service`，不在 `router`，也不在 `repository`。

---

## 1. 工程结构与依赖方向

### 1.1 后端：垂直切片

```
src/
├── core/      与业务无关：配置 / 日志 / 上下文 / 异常 / 响应 / 安全
├── db/        数据访问基座：Base / session / 通用仓储
├── infra/     可替换实现：storage / queue / ai（全部面向 Protocol）
├── api/       HTTP 层：deps / middleware / errors / 模块注册表
└── modules/   业务：每个模块自带 models / schema / service / router
```

单个模块内四层职责，边界要硬：

| 文件 | 可以做的事 | 绝对不做的事 |
|---|---|---|
| `models.py` | ORM 字段、索引、约束、关系 | 业务判断、`select` 查询、序列化逻辑 |
| `schema.py` | 出入参定义、字段级校验（`Field(ge=...)`） | 碰数据库、发起 IO |
| `service.py` | 业务规则、编排、事务提交、抛业务异常 | 读写 `Request`/`Response`、拼 SQL 字符串 |
| `router.py` | 声明路径与参数、调 service、包 `ApiResponse` | 任何 `if` 业务分支、任何 `commit()` |
| `worker.py` | 消费队列、幂等重试 | 依赖请求上下文（`CurrentUser`） |

**判断口诀**：写在 `router` 里的 `if`，八成该下沉到 `service`；写在 `service` 里的 `select()`，八成该上提到 `repository`。

### 1.2 前端：分层单向

```
app/       组装：providers、router（由注册表生成）、错误兜底
modules/   功能切片：一个功能一个目录，声明路由 / 导航 / 插槽 / 权限
pages/     路由级页面（被 module 的 load() 懒加载）
widgets/   跨页面布局块：app-shell、sidebar、top-bar、module-nav
features/  用户动作：create-task、task-filter、task-preview
entities/  领域实体：task、asset、image、shot、storyboard、template
shared/    ui / api / lib / hooks / config / registry / access
```

- 依赖只能**向下**：`pages` 可以用 `features`，`features` 不能 import `pages`。
- `modules` 是最上层纵向切片，只能向下依赖；**不能 import `@/app/*`**（app 单向发现模块），**模块之间不能互相 import**（要复用就下沉）。
- 跨 `entities` 相互引用要谨慎：`features/task-preview` 依赖 `entities/task` 可以；`entities/task` import `entities/asset` 就该考虑把共性类型提到 `shared`。
- 一个实体对外只暴露三样东西：`model/types.ts`（类型）、`api/*.ts`（请求）、`ui/*`（展示组件）。
- **新增功能的标准姿势是「新增文件」，不是改骨架**：详见 `docs/FRONTEND_EXTENSION.md`。

| 想做的事 | 声明位置 |
|---|---|
| 新页面 / 新导航 / 顶部宫格 | `src/modules/<id>/module.ts` |
| 页面级权限、导航按权限隐藏 | `module.ts` 的 `permissions` |
| 按钮级权限 | `<Can permission="...">` |
| 新接口 | `entities/<id>/api/*.api.ts` |
| 功能 mock | `src/modules/<id>/mock.ts` |
| 外壳插槽内容 | `module.ts` 的 `slots` |

---

## 2. 后端规范（Python）

### 2.1 命名

| 对象 | 规则 | 例子 |
|---|---|---|
| 模块目录 | 单数、小写 | `modules/task/`，不是 `tasks/` |
| Service 类 | `<实体>Service` | `TaskService` |
| 入参 Schema | `<动作><实体>In` | `CreateTaskIn`、`UpdateTaskIn` |
| 出参 Schema | `<实体>Out` | `TaskOut`、`TaskListOut` |
| 查询参数 | `<实体>ListQuery` / `<实体>ListParams` | `TaskListQuery` |
| service 方法 | 动词开头，命令式 | `create_task`、`retry_task`、`toggle_favorite` |
| 内部方法 | 单下划线前缀 | `_placeholder_cover` |
| 常量 | 全大写 | `JOB_GENERATE`、`MAX_PAGE_SIZE` |

**一个细节**：`list_tasks` 返回 `TaskListOut`（含 `stats`），`stats()` 返回 `TaskStats`。同一资源有多种形态时，用不同 Schema 表达，不要给一个类加一堆 `Optional` 字段。

### 2.2 类型注解

- 每个文件顶部 `from __future__ import annotations`。
- 所有函数**完整注解**（参数 + 返回值），包括 `-> None`。
- 禁止用 `Any` 逃逸。确实动态的地方收窄到 `object` 或定义 `Protocol`。
- 集合类型用内建泛型：`list[str]`、`dict[str, int]`，不用 `List`/`Dict`（ruff `UP` 会拦）。
- `typing.Sequence` / `Mapping` 从 `collections.abc` 导入（ruff `UP035`）。

```python
# ✅
async def paginate(
    self, stmt: Select[Any], params: PageParams
) -> tuple[Sequence[ModelT], int]: ...

# ❌ 返回值缺失，调用方无法推断
async def paginate(self, stmt, params): ...
```

### 2.3 异步纪律

`async def` 内部**不允许出现同步阻塞调用**——它会卡住整个事件循环，比单线程还慢。

| 禁止 | 替代 |
|---|---|
| `requests.get()` | `httpx.AsyncClient` |
| `time.sleep()` | `asyncio.sleep()` |
| 大文件 `open().read()` | `anyio.to_thread.run_sync` / 异步库 |
| CPU 密集（视频转码、大图处理） | 丢到 worker 或线程池，不在请求线程做 |
| 同步 ORM 调用 | `await session.execute()` |

同一个请求内的独立 IO 用 `asyncio.gather` 并发，串行 `await` 是隐性性能债。

### 2.4 事务

- **提交点在 service**，一个方法最多一次 `commit()`。
- `repository` 只 `flush()`，不 `commit()`——它不知道业务边界。
- 读操作不需要 `commit()`。
- 副作用（入队、发消息、调第三方）放在 `commit()` **之后**；如果副作用失败会导致状态悬挂，必须写补偿。

```python
# ✅ service 显式提交，语义清晰
await self.repo.add(task)
await self.session.commit()

# 副作用在提交之后；失败时至少留下可恢复的痕迹
await self.queue.enqueue(Job(name=JOB_GENERATE, payload={"task_id": task.id}))
```

> 注意：这种写法下「提交成功、入队失败」会让任务停在 `queued` 没人处理。
> 目前靠 `worker.resume_unfinished_tasks()` 启动时兜底。若要更严谨，用 outbox 表或「先入队后提交 + 幂等消费」二选一，不要两头都不做。

### 2.5 错误处理与错误码

**业务代码不写 `try/except`**。可预期错误直接 `raise`，由 `api/errors.py` 全局兜底转成统一响应。

```python
from core.exceptions import Forbidden, InvalidState, NotFound

task = await self.repo.get_or_404(task_id, "任务不存在")
if TaskStatus(task.status) is not TaskStatus.FAILED:
    raise InvalidState("仅失败的任务可以重试")
```

- 优先用语义化子类（`NotFound` / `Forbidden` / `InvalidState` / `RateLimited` / `UpstreamError`），不要一律 `AppError`。
- 需要模块私有错误码时用 `ModuleErrors("voice", slot=3)` 申请号段，**不要改 `core/exceptions.py`**。

错误码结构：`HTTP状态码 × 100 + slot × 10 + seq`，保证 `code // 100 == http_status`。

| 段 | 用途 |
|---|---|
| `000-009` | 框架级通用（`BAD_REQUEST`、`VALIDATION_FAILED`…） |
| `010-090` | 业务模块，按 slot 分配 |

`except` 的三条禁令：

```python
# ❌ 吞异常：故障静默，线上无法定位
try:
    await self.queue.enqueue(job)
except Exception:
    pass

# ❌ 裸 Exception 且不重抛，把可恢复错误变成成功
except Exception:
    return None

# ✅ 只在能真正处理时捕获，且记录上下文
except httpx.TimeoutException as exc:
    logger.warning("供应商超时 task=%s", task_id, exc_info=exc)
    raise UpstreamError("模型服务响应超时") from exc
```

### 2.6 接口契约

| 规则 | 说明 |
|---|---|
| 响应体 | 一律 `ApiResponse.ok(data, message)` → `{code, message, data}` |
| 字段命名 | Python 侧 snake_case，序列化自动 camelCase（`CamelModel`） |
| 时间 | 一律 `UtcDateTime`，输出 `...Z`；**禁止**直接返回 naive `datetime` |
| 分页 | 入参 `page` / `pageSize`，出参 `{items, total, page, pageSize}` |
| 资源不存在 | 404 + `NOT_FOUND`，不要返回 `200 + data:null` |
| 状态冲突 | 409 + `INVALID_STATE`（如「仅失败任务可重试」） |
| 未实现 | 501 + `NotImplementedFeature`，并留 `TODO(owner)` |

**新增路由必须同时**：在 `router.py` 声明 → 在 `api/registry.py` 注册 → 更新 `README.md` 的路由表。三步缺一，前端就会对着一份不存在的文档写代码。

### 2.7 日志

```python
logger = get_logger(__name__)

# ✅ 惰性格式化：level 关闭时不产生字符串开销
logger.info("任务已创建并入队 id=%s author=%s", task.id, actor.id)

# ❌ f-string：即使日志被丢弃也已经拼好了
logger.info(f"任务已创建 id={task.id}")
```

- 禁止 `print`。
- 禁止记录 `password`、`token`、`Authorization`、完整 `prompt`（用户隐私）、完整手机号。
- 错误日志带 `exc_info=`，不要只写 `str(exc)`。
- `request_id` 已由 `middleware.py` 注入 context，日志里不必手动传。

### 2.8 配置

- 只通过 `core.config.settings` 读取，**禁止**业务代码里 `os.environ.get(...)`。
- 新增配置项要同步三处：`core/config.py` 字段、`.env.example`、README 的部署清单。
- 需要默认值就走 `Settings` 默认值，不要在调用处写 `or "xxx"`。

### 2.9 数据访问

- 禁止字符串拼接 SQL：

```python
# ❌ 注入风险 + 无法复用执行计划
await session.execute(text(f"SELECT * FROM task WHERE title LIKE '%{kw}%'"))

# ✅ 参数绑定
await session.execute(select(VideoTask).where(VideoTask.title.ilike(f"%{kw}%")))
```

- 列表接口先确认没有 N+1：访问关联属性前用 `selectinload` / `joinedload` 预加载，或改成一次性聚合查询。
- 排序、筛选的合法值用枚举收口（`TaskSort` / `TaskStatusFilter`），不要接受任意字符串当 `ORDER BY`。
- 迁移用 Alembic；`app_env=prod` 时自动建表已关闭，这是刻意的。

### 2.10 依赖注入

- 横切依赖从 `api/deps.py` 取（`SessionDep` / `ActorDep` / `PageDep` / `StorageDep`…）。
- 模块自己的 service 装配放 `modules/<name>/dependencies.py`，用 `Annotated[T, Depends(...)]` 定义类型别名。
- 路由签名里不要出现 `Depends(get_xxx())` 的内联写法，统一走类型别名。

### 2.11 注释与 TODO

- 注释写 **why**，不写 **what**。代码已经说清做了什么。
- 模块顶部 docstring 用一两句话说明这个文件的定位（现有代码都这么写，保持）。
- TODO 必须可追踪：

```python
# TODO(zhangsan): 接真实供应商后删除 echo 实现，见 TASK-142
```

无 owner、无条件的 `TODO` 等于没有。
- 语言：注释、docstring、错误提示统一中文；标识符、日志字段名统一英文。

### 2.12 风格基线

以 `pyproject.toml` 为准，改动需团队确认：

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC"]
ignore = ["B008"]   # FastAPI 的 Depends() 默认值属于框架惯例
```

- 行宽 100。超了先想是不是嵌套太深，而不是直接换行。
- 函数超过 ~50 行、嵌套超过 3 层，基本该拆了。
- 导入顺序交给 `ruff --fix`，不要手排。

---

## 3. 前端规范（TypeScript / React）

### 3.1 类型

- `tsconfig` 已开 `strict`。**禁止** `any`；确需未知类型用 `unknown` + 收窄。
- `as` 断言只在收窄确实安全时用，并加一行理由注释。`as SomeType` 绕过的是编译器，不是运行时。

```ts
// ❌ enabled 已经保证 id 非空，但断言掩盖了「id 可能为 null」这个事实
queryFn: () => taskApi.detail(id as string)

// ✅ 用局部收窄表达意图
queryFn: () => taskApi.detail(id!)

// ✅ 更稳：把 queryFn 定义在 if (id) 之后，类型自动收窄
```

- 对外契约用 `interface`（可扩展、报错信息友好），联合类型用 `type`。
- 后端字段名照抄 camelCase，**不要**在前端再套一层命名转换。

### 3.2 状态边界

这是前端最容易腐化的地方，规则只有一条：

| 数据来源 | 放哪 | 例子 |
|---|---|---|
| 服务端数据（列表、详情、统计） | **React Query** | `useTaskList`、`useTaskStats` |
| 纯客户端 UI 状态（弹窗开关、选中项、筛选草稿） | `zustand` / 组件 state | `CreateTaskModal` 的输入 |
| URL 可表达的状态（分页、筛选、tab） | **URL searchParams** | 任务中心的分页 |

**禁止**把服务端数据 `set` 进 zustand 或 `useEffect` 里镜像一份——那是两份真相，必然不同步。

`queryKey` 一律从工厂派生，不要手写字符串：

```ts
export const taskKeys = {
  all: ['tasks'] as const,
  lists: () => [...taskKeys.all, 'list'] as const,
  list: (params: TaskListParams) => [...taskKeys.lists(), params] as const,
  detail: (id: string) => [...taskKeys.all, 'detail', id] as const,
};
```

轮询要有**停止条件**，不能无条件 `refetchInterval`：

```ts
refetchInterval: (query) =>
  query.state.data?.items.some(isTaskInFlight) ? POLL_INTERVAL : false,
```

### 3.3 API 层

- 组件里**禁止**直接 `import { http }`。请求只写在 `entities/*/api/*.api.ts`，hook 写在 `*.queries.ts`。
- 响应解包、错误转换已在 `shared/api/http.ts` 拦截器统一处理，业务层只拿 `data`。
- 新增接口：先加 `model/types.ts` 类型 → 再加 `api/xxx.api.ts` 方法 → 最后写 `queries.ts` hook。顺序反了就会出现裸 `any`。
- Mock 与真实后端只靠 `VITE_USE_MOCK` 一个开关切换，业务代码不得感知数据源。

### 3.4 组件

- 一个文件一个组件，文件名 = 组件名 = `PascalCase.tsx`。
- Props 用 `interface XxxProps`，**不要**内联 `({ a, b }: { a: string })`（超过 3 个字段时）。
- 组件超过 200 行或 JSX 嵌套超过 4 层，拆子组件或抽 hook。
- 派生数据在渲染期算，不要用 `useEffect` + `setState` 同步（会多一次渲染且容易死循环）。
- `useEffect` 只用于「与外部系统同步」：订阅、定时器、focus、第三方实例。依赖数组必须完整。
- 列表 `key` 用稳定 ID，禁止用数组下标（删除/排序会错位）。

### 3.5 样式

- Tailwind 原子类 + `cn()` 合并（`shared/lib/cn.ts`，clsx + tailwind-merge）。条件类名一律走 `cn`，不要字符串拼 `+`。
- 颜色、圆角、阴影从 `tailwind.config.js` 的 token 取（`bg-panel`、`text-ink-soft`、`border-line`），**禁止**新增裸十六进制色值。个别需要 `rgba(...)` 阴影时写成任意值并加注释说明为何不能 token 化。
- 禁止内联 `style`，除非是运行时计算值（进度条宽度、动态渐变）。
- 同一视觉效果出现第三次，抽到 `shared/ui`。

### 3.6 性能

- 路由级页面用 `lazy()` + `Suspense`（页面组件体积大，首屏收益明显）。
- `useMemo` / `useCallback` 只加在**确实传递给 memo 子组件**或**依赖昂贵计算**处，无脑包裹反而增加开销。
- 列表图片加 `loading="lazy"`、固定宽高比占位，避免 CLS。
- 输入搜索走 `useDebouncedValue`，不要每次按键都发请求。

### 3.7 可访问性

- 可点击元素用 `<button>` / `<a>`，不要给 `<div onClick>`。
- 仅图标的按钮必须有 `aria-label`（`IconButton` 已支持，别漏传）。
- 弹窗：`role="dialog"` + `aria-modal`，打开时焦点进入，`Esc` 关闭，关闭后焦点归还触发元素。
- 表单 `label` 与输入框用 `htmlFor` / `id` 关联；错误提示用 `aria-describedby`。
- 焦点样式不得 `outline: none` 后不给替代。

### 3.8 文件命名速查

| 类型 | 规则 | 例 |
|---|---|---|
| 组件 | `PascalCase.tsx` | `TaskCard.tsx` |
| hook | `useXxx.ts` | `useDebouncedValue.ts` |
| API 方法 | `<entity>.api.ts` | `task.api.ts` |
| Query hook | `<entity>.queries.ts` | `task.queries.ts` |
| 类型 | `types.ts` | `entities/task/model/types.ts` |
| 工具 | `camelCase.ts` | `format.ts` |
| 常量表 | `UPPER_SNAKE` | `TASK_STATUS_LABEL` |

---

## 4. 跨端契约（两边都要遵守）

后端 `schema.py` 与前端 `model/types.ts` 是同一份契约的两个副本。改契约的**唯一合法流程**：

1. 先改后端 `schema.py`（含 `Field` 校验与默认值）。
2. 同一 PR 内改前端 `types.ts` 与 mock 数据（`mocks/db.ts`、`mocks/adapter.ts`）。
3. 若字段改名，后端保留旧字段一个版本做兼容，或明确公告破坏性变更。
4. 更新 README 的接口表。

**破坏性变更清单**（必须评审显式确认）：字段改名/删除、枚举值增删、必填变可选或反之、状态机新增状态、错误码语义变化、分页结构变化。

---

## 5. Git 与提交

**分支**：`feat/<模块>-<短描述>`、`fix/<问题>-<短描述>`、`chore/...`、`docs/...`

**Commit**（Conventional Commits，中文正文可以）：

```
feat(task): 支持按模型筛选任务列表
fix(asset): 修复上传凭证过期未刷新
refactor(api): 把分页逻辑收敛到 BaseRepository
docs(readme): 补充生产部署清单
```

- 一次提交只做一件事。出现「顺便改了格式」的 diff，说明该拆。
- 禁止提交：`.env`、`var/`、`node_modules`、`dist/`、调试用 `console.log`、注释掉的死代码。
- PR 控制在 **400 行以内**（不含 lock 文件与自动生成代码）。超过就拆，评审质量会断崖式下降。

---

## 6. 让规范自动生效

规范不落到工具上就只是一篇作文。建议补齐以下四件事。

**① 后端 pre-commit（`.pre-commit-config.yaml`）**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: .venv/Scripts/python.exe -m mypy src
        language: system
        pass_filenames: false
```

**② 前端 ESLint（已配置，2026-11-05）**

落点是 `studio/eslint.config.js`（flat config）。三层效力：

| 层 | 规则 | 说明 |
|---|---|---|
| 正确性 | `no-console`（保留 warn/error）、`no-debugger`、`eqeqeq`、`prefer-const`、`no-explicit-any`、`no-unused-vars` | 存量已是 0 error，故按 error 卡死 |
| Hooks | `react-hooks/recommended` | `exhaustive-deps` 是这里最值钱的一条 |
| 架构 | `no-restricted-imports` 按层设 zone | `modules → pages → widgets → features → entities → shared` 反向 import 直接报错；`modules` 额外禁止 import `@/app/*` 与 `@/modules/*`；`pages/features/widgets` 与 `entities/*/ui` 禁止 import `@/shared/api/http` |
| 可访问性 | `jsx-a11y/recommended` | 已修掉存量：`TaskCard` 改「铺满卡片的 button」、`ShotsPage` 卡片改 `button`、`Modal` 补 `aria-labelledby` + 焦点进出 |

```bash
npm run lint        # eslint . --max-warnings 0
npm run lint:fix
```

已知边界：zone 只拦 `@/xxx` 别名，相对路径 `../../features/...` 仍可绕过。

**③ 前端测试（已接入 Vitest，2026-11-05）**

```bash
npm run test            # vitest run
npm run test:coverage   # 带 v8 覆盖率（当前 18.6% stmts，先建网再充血）
npm run verify          # typecheck + lint + test + build，一条命令跑完门禁
```

覆盖优先级（谁最容易被改坏就先测谁）：

1. `shared/lib`、`shared/hooks` 纯函数（`format`、`useDebouncedValue`）
2. `entities/*/model` 与 `*/api/*.queries.ts` 的派生逻辑（时长/比例选项、状态判定）
3. `shared/ui` 交互组件（`Modal` 的焦点与滚动锁、`TaskCard` 的键盘可达）
4. `mocks/adapter.ts` 的 REST 语义（越权过滤、分页、stats 口径）——它就是前端的"假后端契约"
5. `shared/registry` 的装配与校验（重复路由 / 导航指向空路由 / 排序）、`shared/access` 的权限判定、`app/registry` 的骨架不变量（导航必须有页面）

**④ CI 门禁（五个都必须绿）**

| 检查 | 命令 | 阻断条件 |
|---|---|---|
| 后端静态 | `ruff check src tests` | 任一 error |
| 后端类型 | `mypy src` | 新增错误（存量先忽略，逐步收敛） |
| 后端测试 | `pytest -q` | 任一失败 |
| 前端类型 | `npm run typecheck` | 任一 error |
| 前端静态 | `npm run lint` | 任一 error / warning |
| 前端测试 | `npm run test` | 任一失败 |
| 前端构建 | `npm run build` | 失败 |

前端四条可用 `npm run verify` 一次跑完；CI 未接入时，提交前手动跑这一条。

**⑤ 契约守卫**：CI 里跑一次「后端路由表 ↔ 前端 api 文件」的对比脚本（或人工 checklist），防止两边漂移。

---

## 附录 A：反例对照表

| ❌ 反例 | ✅ 写法 | 原因 |
|---|---|---|
| `router.py` 里判断 `if task.status != "failed"` | `service.retry_task()` 里 `raise InvalidState` | 规则换入口（worker/CLI）就失效 |
| `repository` 里 `commit()` | `service` 里 `commit()` | 仓储不知道事务边界 |
| `except Exception: pass` | 让异常冒泡，由全局处理器兜底 | 静默故障比崩溃更难查 |
| `logger.info(f"...{x}")` | `logger.info("...%s", x)` | 惰性求值 |
| 组件里 `await http.get(...)` | `useQuery` + `taskApi.list()` | 缓存、重试、轮询、卸载竞态全丢 |
| `useEffect(() => setList(data), [data])` | 直接渲染期派生 | 多一次渲染 + 易死循环 |
| `style={{ color: '#2b7cff' }}` | `className="text-primary"` | 主题不可换、暗色模式失效 |
| `as any` 让报错消失 | 补类型或 `unknown` + 收窄 | 把编译期错误推迟成线上错误 |
| 前后端各定义一份字段名 | 后端 alias 转换 + 前端照抄 | 双份真相必然漂移 |
| `def get_task(...)` 同步函数里 `requests.get` | `async` + `httpx.AsyncClient` | 阻塞事件循环 |
