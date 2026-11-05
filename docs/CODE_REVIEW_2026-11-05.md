# 代码评审报告 · 天火 AI 短视频工作台

> 评审日期：2026-11-05 ｜ 范围：`api/`（6.3k 行 Python）+ `studio/`（3.4k 行 TS/TSX）
> 评审人：Code Reviewer ｜ 依据：[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、总体印象

这是一份**明显高于平均水准**的骨架代码。分层不是画出来的，是守住的：`modules → db/core/infra` 的依赖方向没有一处反向；扩展点（模块发现、驱动加载、生成流水线、事件总线、权限点声明）各自收敛，加功能确实不需要改核心文件。

关键的工程判断也对：
- **轮询用延迟重投而不是 handler 内 `sleep`** —— 注释里把「并发槽位是稀缺资源」讲清楚了，这是很多团队要踩过坑才明白的事。
- **基础设施未实现时启动即抛错**，而不是静默降级到本地磁盘。
- **重复注册默认抛错**，把并行开发的撞名变成编译期问题。

问题集中在**边界处**：枚举与字符串的边界、事务与副作用的边界、示例配置与生产环境的边界、前端硬编码与后端目录的边界。四类边界各有一个 🔴。

### 质量基线（实测）

| 检查项 | 结果 |
|---|---|
| `ruff check src tests` | ✅ All checks passed |
| `pytest -q` | ✅ 37 passed |
| `mypy src` | ⚠️ 4 errors（88 文件） |
| `tsc -b`（studio） | ✅ 通过，`strict` 全开 |
| 前端 `any` / `tsc-ignore` | ✅ 0 处 |
| 前端 ESLint | ❌ 未配置 |
| 前端测试 | ❌ 无 |
| 测试覆盖缺口 | 跨用户隔离、鉴权拒绝、worker 流转、重启恢复 |

---

## 二、🔴 必须修复（4 项）

### 🔴 B-01 跨用户数据越权：`scope` 过滤完全失效

**位置**：`api/src/modules/task/repository.py:19-27`
**关联**：`api/src/core/schema.py:53`（`use_enum_values=True`）、`api/src/modules/task/service.py:66-68`

```python
if query.scope is TaskScope.MINE:
    stmt = stmt.where(VideoTask.author_id == actor.id)
elif query.scope is TaskScope.TEAM:
    stmt = stmt.where(VideoTask.author_id == actor.id)
```

**为什么是阻塞级**

`CamelModel` 配置了 `use_enum_values=True`，Pydantic 在校验**显式传入的枚举**时会把它转成纯 `str`；只有未传值走默认值时才保留枚举实例。而路由层永远显式传：

```python
query = TaskListQuery(status=task_status, ..., scope=scope)   # 永远显式
```

于是 `query.scope` 是 `'mine'` 而不是 `TaskScope.MINE`，`is` 判断恒为 `False`，**author 过滤一次都没生效过**。

实测（两个不同用户，`scope=mine`）：

```
HTTP 200
bob 看到的任务数: 12
涉及作者: ['老大']          ← 全是别人的任务
stats: {'all': 12, ...}
```

`stats()` 同样中招（`TaskListQuery(scope="mine")`），侧边栏角标统计的也是全站数据。当前没暴露，只因为种子数据都属于同一个作者，且 `ActorDep` 在 dev 下回退到 admin 演示账号。

**建议**

判断改为值语义，并显式收窄到枚举：

```python
scope = TaskScope(query.scope)          # 无论传入 str 还是枚举，先归一
if scope is TaskScope.MINE:
    stmt = stmt.where(VideoTask.author_id == actor.id)
elif scope is TaskScope.TEAM:
    stmt = stmt.where(VideoTask.author_id == actor.id)   # TODO: 团队成员集合
```

同时全文排查同类写法：`grep -rn " is Task\| is Capability\| is Generation" src`。目前只剩 `generation/runner.py:171,190`（见 S-06）和 `seed.py`（种子数据传的是真枚举，安全）。

**建议同时**审视 `use_enum_values=True` 的必要性：它让「出参序列化省事」和「内部逻辑拿到 str」耦合在一起，是本类 bug 的温床。收益不明确时，直接删掉更安全——`StrEnum` 本身序列化就是值。

**验收**：新增回归测试，bob 查 `scope=mine` 必须为 0 条。

```python
def test_scope_isolation(client, api):
    bob = create_access_token("u_002", name="bob", permissions=["*"])
    data = client.get(f"{api}/tasks", params={"scope": "mine"},
                      headers={"Authorization": f"Bearer {bob}"}).json()["data"]
    assert data["total"] == 0 and data["stats"]["all"] == 0
```

---

### 🔴 B-02 生产环境可能以「匿名超级权限」启动

**位置**：`api/.env.example:11-13`、`api/src/api/deps.py:47-50`、`api/src/core/config.py:79-84`

```ini
# .env.example
ALLOW_ANONYMOUS=true
JWT_SECRET=change-me-in-production-please-use-openssl-rand-hex-32
```

```python
DEMO_USER = CurrentUser(id="u_001", name="老大", roles=("admin",), permissions=frozenset({"*"}))
```

**为什么是阻塞级**

README 的第一步就是 `cp .env.example .env`。这份样例把 `ALLOW_ANONYMOUS` 设成了 `true`，而匿名用户直接拿到 `permissions={"*"}`。`config.py` 的逻辑是「显式配置优先，否则非生产才放开」——只要 `.env` 里写了 `true`，**prod 也会放开**，且没有任何启动期拦截。JWT 默认密钥同理（实测 PyJWT 已经告警 `InsecureKeyLengthWarning: 25 bytes, below minimum 32`）。

这类问题的典型剧本是：运维照着示例部署，接口全裸奔，且日志里看不出任何异常。

**建议**：在 `Settings` 上加生产期断言，让配置错误在启动时就炸。

```python
from pydantic import model_validator

@model_validator(mode="after")
def _guard_prod(self) -> Settings:
    if self.app_env != "prod":
        return self
    if self.allow_anonymous:
        raise ValueError("生产环境禁止 ALLOW_ANONYMOUS=true")
    if self.jwt_secret.startswith("dev-only") or len(self.jwt_secret) < 32:
        raise ValueError("生产环境 JWT_SECRET 必须 >=32 字节，用 openssl rand -hex 32 生成")
    if "*" in self.cors_origins:
        raise ValueError("生产环境不允许 CORS_ORIGINS=*")
    return self
```

`.env.example` 里的 `ALLOW_ANONYMOUS` 改成注释掉的 `# ALLOW_ANONYMOUS=true`，让开发者显式选择。

---

### 🔴 B-03 进程重启会重复提交、重复计费

**位置**：`api/src/modules/task/worker.py:41-54`

```python
for task in rows:
    task.status = TaskStatus.QUEUED.value
    task.progress = 0
    task.external_id = None      # ← 把供应商侧任务 ID 抹掉了
...
await runner.dispatch(KIND_VIDEO, task_id)   # ← 重新走 submit
```

**为什么是阻塞级**

文件头注释已经写明「真实生产应区分两种情形」，但代码没实现。`generation/runner.py:84-87` 本来有幂等保护：

```python
if record.submitted:
    logger.info("记录已有 external_id，跳过提交 id=%s", record_id)
```

可 `resume` 先把 `external_id` 清空了，幂等保护形同虚设。后果：**每次发布/重启，所有在途任务都会向供应商重新提交一次** —— 重复扣费，且用户会收到两个不同视频。当前用 echo 供应商看不出来，接入火山/可灵当天就会收到账单异常。

**建议**：按「有无 external_id」分流，保留 ID 只续轮询。

```python
resume_poll: list[str] = []
resubmit: list[str] = []

for task in rows:
    if task.external_id:
        task.status = TaskStatus.QUEUED.value        # 不改 external_id
        resume_poll.append(task.id)
    else:
        task.status = TaskStatus.QUEUED.value
        task.progress = 0
        task.error_message = None
        resubmit.append(task.id)
```

`GenerationRunner` 补一个只排轮询的入口：

```python
async def resume(self, kind: str, record_id: str, *, delay: float = 0) -> None:
    get_pipeline(kind)
    await self._schedule_poll(kind, record_id, polls=0, delay=delay)
```

顺带注意：`running` 任务若已超过供应商侧有效期，轮询会返回 `FAILED`，走正常失败路径即可，不要特判。

**验收**：起一个任务 → `external_id` 落库后杀进程 → 重启 → 断言供应商 `submit` 调用次数仍为 1（用 echo 可断言 `external_id` 未变）。

---

### 🔴 B-04 `LocalStorage` 目录穿越防护可绕过

**位置**：`api/src/infra/storage/local.py:18-23`

```python
target = (self.root / key.lstrip("/")).resolve()
if not str(target).startswith(str(self.root)):     # ← 字符串前缀，不是路径前缀
    raise StorageError("非法的对象路径")
```

**为什么是阻塞级**

`/var/storage-evil/x` 与 `/var/storage` 有共同字符串前缀，检查通过。实测：

```
'../storage-evil/pwn.txt' -> C:\...\Temp\tmppfxqlwat\storage-evil\pwn.txt
```

目前上传接口还是 501，所以尚未可被利用；但这是**一次实现上传就立刻变成任意文件读写**的地基问题，必须在接入 S3/OSS 之前修掉（S3 实现通常直接信任 key，问题会换个形式复现）。

**建议**

```python
target = (self.root / key.lstrip("/")).resolve()
if not target.is_relative_to(self.root):          # Python 3.9+，语义精确
    raise StorageError("非法的对象路径")
```

更彻底的做法是在入口处校验 key 的字符集（`^[A-Za-z0-9._/-]+$` 且不含 `..` 段），把非法 key 挡在业务层之外。

**验收**：`put("../storage-evil/x", b"1")` 抛 `StorageError`。

---

## 三、🟡 建议修复（10 项）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| S-01 | `commit()` 后 `dispatch()`，入队失败任务悬挂 | `modules/task/service.py:95-99` | 任务永远停在 `queued`，只能靠重启兜底 |
| S-02 | `toggle_favorite` 读-改-写，并发丢更新 | `modules/task/service.py:121-125` | 双击收藏结果不确定 |
| S-03 | PATCH 无法清空标题 | `modules/task/service.py:131-132` | `"".strip() or task.title` 静默忽略用户意图 |
| S-04 | 模型契约前后端漂移 | `studio/.../CreateTaskModal.tsx:9-21` vs `api/.../echo.py:42-73` | 前端可提交后端不存在的模型 |
| S-05 | 预览弹窗不轮询，进度冻结 | `studio/.../task.queries.ts:39-45` | 用户盯着不动的 62% |
| S-06 | provider 返回字符串状态会静默死循环 | `generation/runner.py:171,190` | 轮询到 `max_polls` 才失败，白烧 150 次调用 |
| S-07 | 权限模型只覆盖 task 模块 | `modules/{asset,image,shot,...}/router.py` | 半套 RBAC，后续难统一 |
| S-08 | `resume` 与 retry 存在重复提交竞态 | `service.py:103-119` | 极小概率双提交 |
| S-09 | 前端零测试 + 零 lint | `studio/` | hooks 依赖错误无任何拦截 |
| S-10 | 占位图依赖公网 `picsum.photos` | `task/service.py:155`、`echo.py:116`、`mocks/adapter.ts:240` | 内网/离线环境全站空白 |

### S-01 提交后副作用失败 → 任务悬挂

```python
await self.repo.add(task)
await self.session.commit()                       # ① 已提交
await get_runner(self.queue).dispatch(KIND_VIDEO, task.id)   # ② 抛异常 → 任务停在 queued
```

①成功②失败，用户看到「一直排队中」。有了幂等键能防重复提交，但防不了「从未提交」。两条路选一条：**outbox 表**（提交与投递同事务，worker 轮询 outbox）或**先投递后提交 + 消费端幂等**。至少要 `try/except` 把任务标成 `failed`，让用户能重试：

```python
try:
    await get_runner(self.queue).dispatch(KIND_VIDEO, task.id)
except Exception:
    logger.exception("任务入队失败 id=%s", task.id)
    task.status = TaskStatus.FAILED.value
    task.error_message = "任务调度失败，请重试"
    await self.session.commit()
    raise UpstreamError("任务调度失败，请稍后重试") from None
```

### S-02 收藏切换的并发窗口

`task.favorited = not task.favorited` 是典型的读-改-写。两个请求各自读到 `False`，都写 `True`，用户以为取消了其实没有。改成单语句：

```python
await self.session.execute(
    update(VideoTask)
    .where(VideoTask.id == task.id)
    .values(favorited=~VideoTask.favorited)
)
await self.session.commit()
await self.session.refresh(task)
```

### S-04 模型契约漂移（这条最容易在接真实供应商时爆）

前端硬编码 4 个模型，后端 echo 只声明了 `seedance-2.5 / kling-2.1 / seedream-3`：

```ts
const MODEL_OPTIONS = [
  { value: 'Seedance 2.5', ... },   // 后端是 label，能靠别名兜住
  { value: 'Seedance 2.0', ... },   // ❌ 后端不存在
  { value: '可灵 2.1', ... },        // 后端 label 恰好一致
  { value: 'Vidu Q1', ... },        // ❌ 后端不存在
];
```

后端 `provider_for` 在「只有一个 provider」时兜底放过，所以现在不报错；一旦 `AI_PROVIDERS=echo,volcengine`，未声明模型立刻 400。时长同理：前端提供 30 秒，echo 的 `param_schema` 上限是 12 秒。

`ModelRouter` 已经有完整的目录能力（`models()`/`spec_for()`/参数 schema），但**没有暴露成接口**，前端只能猜。建议加一个只读接口，前端改成拉取：

```
GET /api/v1/models?capability=video
→ { items: [{ id, label, capability, provider, paramSchema }] }
```

这同时解决了「新增模型要改前端」的问题——本来这正是你们架构想避免的事。

### S-05 详情弹窗不刷新

```ts
export function useTaskDetail(id: string | null) {
  return useQuery({ ..., enabled: Boolean(id) });   // 没有 refetchInterval
}
```

列表在轮询，详情不轮询。从列表点进一个 62% 的任务，弹窗会一直显示 62%。加与列表一致的条件轮询：

```ts
refetchInterval: (q) => (q.state.data && isTaskInFlight(q.state.data) ? 2_500 : false),
```

### S-06 状态判断依赖枚举身份

```python
if state is GenerationState.SUCCEEDED:
```

`GenerationProgress.state` 声明为 `GenerationState`，但 `@dataclass` 不做校验。第三方 provider 写 `return GenerationProgress(..., state="succeeded")` 时，`is` 判断失败，三个分支全不命中，落进 `else` 分支当作「进行中」无限轮询，直到 150 次超时。改成 `==`（StrEnum 与 str 相等）或在 dataclass 里 `__post_init__` 强制 `GenerationState(self.state)`，把错误挡在 provider 边界上。

### S-07 权限体系只落地了四分之一

只有 task 模块声明了 `PermissionSpec` 并把路由挂在 `require(...)` 上；asset / image / storyboard / shot / template 全部直接用 `ActorDep`。这意味着「能登录 = 能访问」，删素材、看模板都不需要权限点。趁只有 4 个权限点，成本最低的时机统一掉——每个模块 `__init__.py` 补 `PermissionSpec`，路由换 `require("asset:read")` 这类依赖。

---

## 四、💭 细节（不阻塞）

- `modules/task/service.py:152` 函数体内 `import hashlib`，提到模块顶部。
- `modules/task/service.py:155` 占位封面用 `sha1(prompt)` 拼 URL，会把 prompt 的哈希泄漏给第三方图片站（弱信息泄漏，配合 S-10 一起改掉）。
- `modules/storyboard|template|asset|image/service.py` 的 `__init__(self, session)` 无类型注解 + `# noqa: ANN001`，与 task 模块的 `AsyncSession` 风格不一致（ruff 没启 ANN 规则所以不报）。
- `api/v1/router.py:17` 的模块级 `v1_router = build_v1_router()` 是死代码，还让「模块发现」在 import 期就跑一次；同时是 mypy 报错点（`tags` 的 `list` 不变型，改 `tuple(module.tags)` 即可）。
- `repository.apply_status(stmt, status: str)` 用裸字符串比较 `"all"`，签名收窄成 `TaskStatusFilter` 更安全。
- `mocks/adapter.ts:123` 详情路由 `/^\/tasks\/([\w-]+)$/` 会把 `/tasks/stats` 吃掉，返回「任务不存在」。mock 模式下加 stats 会踩坑。
- `infra/storage/local.py:47-51` 的 `presigned_upload_url` 指向 `/api/v1/storage/upload/{key}`，该端点不存在，是死链接。
- `studio/src/shared/ui/Modal.tsx:22-29` 直接改 `document.body.style.overflow`，两层弹窗叠加时关闭内层会提前解锁滚动；用引用计数或 `:has()` 更稳。
- `studio/src/features/create-task/CreateTaskModal.tsx:128` 把 `POST /tasks` 作为 Chip 展示给终端用户，像内部调试信息。
- `tests/test_task_api.py:78` 对布尔值用 `is not`，`assert first != second` 更贴语义。
- `mypy` 4 处存量错误：`seed.py:184`（`list + list` 类型不兼容）、`api/v1/router.py:13`、`cli.py:74,78`（`object` 不可迭代，因为 `summary["models"]` 是 `object`）。建议 `summary()` 返回 `TypedDict` 或具名 dataclass，一次解决 3 处。

---

## 五、✅ 值得保留的做法（别在重构中丢掉）

1. **`ModuleSpec` + 目录扫描**：加模块零注册、零 git 冲突，比手写路由清单高明一档。
2. **延迟重投轮询**：注释解释了「为什么不能 sleep」，这是团队知识沉淀的正确写法。
3. **`ModuleErrors` 号段生成器**：错误码重复在注册期就抛错，把纪律变成机制。
4. **驱动未实现即启动失败**：宁可起不来，不要静默降级。
5. **`UtcDateTime` 统一补 UTC**：sqlite naive 时间这个坑被正确处理了。
6. **迁移与模型是同步的**：核对 6 张表 20 个字段一致，`started_at` / `external_id` 都在——很多项目在这里悄悄漂移。
7. **前端 `queryKey` 工厂 + 条件轮询**：缓存失效范围可控，轮询会自己停。
8. **`strict` + 零 `any`**：3.4k 行守住这条不容易。

---

## 六、建议的修复顺序

| 顺序 | 内容 | 理由 |
|---|---|---|
| 1 | B-01 + 回归测试 | 唯一的真实数据越权，1 行改动 + 1 个测试 |
| 2 | B-02 生产断言 | 部署前必须挡住，否则整套鉴权白做 |
| 3 | B-04 `is_relative_to` | 1 行，接上传前必须修 |
| 4 | B-03 重启分流 | 接真实供应商前必须修，涉及计费 |
| 5 | S-01 入队失败标记 failed | 用户可感知的悬挂问题 |
| 6 | S-04 `/models` 接口 + 前端拉取 | 解除前后端硬编码耦合 |
| 7 | ESLint + 前端测试脚手架 | 防止后续 3 个月腐化 |
| 8 | S-07 权限统一、mypy 清零 | 架构债，趁模块少 |

前 4 项都是小改动，但每一项都在「现在不疼、接真实供应商当天剧痛」的位置上。

---

## 七、复核结论与修复记录（同日 12:20）

逐条核对过：**4 项 🔴 全部属实并且已经复现**，🟡 里 8 条属实（已修 7 条），
2 条保留并说明理由。另有 2 条描述与代码实际不符，下面单独标注。

### 7.1 阻塞项

| # | 结论 | 复现证据 | 修复 |
|---|---|---|---|
| B-01 | ✅ 属实 | 回到 `is` 比较后回归测试直接失败：bob 的 `scope=mine` 返回 `{'u_001','u_002'}` | `TaskScope(query.scope)` 归一后比较；新增 2 个隔离测试 |
| B-02 | ✅ 属实 | `.env.example` 里 `ALLOW_ANONYMOUS=true`，`Settings` 无任何 prod 断言 | 新增 `model_validator`：匿名放开 / 弱密钥 / `CORS=*` / `DEBUG` 任一命中即启动失败；样例改为注释掉 |
| B-03 | ✅ 属实 | `resume` 里 `external_id = None` 后再 `dispatch` | 按有无 `external_id` 分流，新增 `runner.resume()` 只续轮询 |
| B-04 | ✅ 属实 | `put('../storage-evil/x')` 实际写到了 root 同级目录 | `is_relative_to` + key 字符集校验 + 拒绝 `.`/`..` 段 |

B-03 做了真机验证（不是单测）：起任务 → 25% 时 `taskkill /F` → 查库确认
`running + external_id=echo-video-77c729ec-...` → 重启 → 日志 `恢复未完成任务 1 个（重新提交 0 / 续轮询 1）`
→ 任务完成且 **external_id 一字未变**。这正是「重启不重复提交」的要求。

### 7.2 建议项

| # | 结论 | 处理 |
|---|---|---|
| S-01 | ✅ 属实 | `_dispatch()` 包 try/except：标 `failed` + 返回 502，用户可重试 |
| S-02 | ✅ 属实 | 改单语句 `update(...).values(favorited=~VideoTask.favorited)` |
| S-03 | ✅ 属实 | 空标题落成「未命名任务」，不再静默忽略 |
| S-04 | ✅ 属实 | 新增 `modules/catalog` 模块 → `GET /models?capability=video`；前端 `entities/model` 拉取，时长选项由 `paramSchema.durationSec` 生成 |
| S-05 | ✅ 属实 | 详情查询补 `refetchInterval`（与列表同条件） |
| S-06 | ✅ 属实（潜在） | `GenerationProgress.__post_init__` 强制 `GenerationState(...)`，并顺带把 progress 夹到 0-100；比较改 `==` |
| S-07 | ✅ 属实 | asset / image / storyboard / shot / template / catalog 全部补 `PermissionSpec` 并挂 `require()`，权限点 4 → 13；新增架构测试拦住「无权限点的模块」 |
| S-08 | ⚠️ 部分成立 | 窗口极小（只在启动瞬间与 retry 撞车）；B-03 修好后「启动重置」这条主要路径已消失，暂不加分布式锁，已在 `worker.py` 注明 |
| S-09 | ✅ 属实 | 未处理：前端 lint / 测试脚手架属于工具链改造，与本次修复无关，建议单独一轮 |
| S-10 | ✅ 属实 | 保留：占位图只用于 dev（真实封面由供应商首帧回写）。只把种子从 `sha1(prompt)` 改成随机值，不再把画面描述摘要带给第三方图片站 |

### 7.3 细节项

全部处理：`hashlib` 提到模块顶部、`__init__` 补 `AsyncSession` 注解、
删掉 `v1_router` 死代码（顺带解决 mypy 报错）、`apply_status` 收窄到
`TaskStatusFilter`、`ModelRouter.models()` 改值比较、`mypy` 4 处清零、
测试里的 `is not` 改 `!=`。

两条需要更正原描述：

- **`mocks/adapter.ts` 的 `/tasks/stats`**：正则确实会吃掉它，但前端从来没有调用
  `/tasks/stats`（角标走的是列表接口），属于「潜在坑未触发」。已按后端契约补上
  stats 路由并放在详情路由之前。
- **`Modal.tsx` 的滚动锁**：现象成立，但成因不是「内层关闭提前解锁」，而是
  `useEffect` 依赖了 `onClose`——父组件传内联箭头函数时引用每次渲染都变，
  effect 反复 cleanup + 重跑，第二次就把**自己刚设的 `hidden`** 当成原值记下来，
  最终关弹窗后页面永久无法滚动。改为 `onClose` 存 ref + 引用计数。

### 7.4 修复后的基线

```
pytest        69 passed（原 37，新增 32 条：scope 隔离、dispatch 失败、重启恢复、存储穿越、配置守卫、模型目录、状态归一化）
ruff check    All checks passed
mypy src      Success: no issues found in 92 source files   ← 原来 4 errors
tsc -b        clean
npm run build ok
```

真机验证：`/models` 返回 2 个视频模型 + 参数 schema；新建任务
`running 32% → 82% → completed 100%`；重启恢复 `external_id` 不变；
`app` 启动日志 `模块 8 个 | 权限点 13 个`。
