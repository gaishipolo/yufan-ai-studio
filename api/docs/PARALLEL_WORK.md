# 多人并行开发约定

目标：**两个人同时开工，git 冲突应该接近零。** 冲突多的根因通常不是 git，而是设计里有共享可变点。

---

## 1. 分工原则：一个模块 = 一个目录 = 一个人

按业务模块切人，不要按技术层切（"你写 service，我写 router"必然冲突）。

| 角色 A | 角色 B |
|---|---|
| `modules/task/` 全部 | `modules/identity/` 全部 |
| 需要新权限点 → 自己模块声明 | 需要新表 → 自己模块建 |
| 需要调生成 → `runner.dispatch()` | 需要通知 → 订阅事件 |

**共享代码的修改必须走评审**，且只能以"提升抽象"的方式改（见 `EXTENDING.md` 第 10 节）。

---

## 2. 已经消除的冲突热点

这些地方历史上需要"两人各加一行"，现在都不用了：

| 位置 | 过去 | 现在 |
|---|---|---|
| `api/registry.py` | 手写 `MODULES` 列表 | 目录自动发现 |
| `db/registry.py` | 手写模型 import 列表 | 扫描 `modules.*.models` |
| `infra/__init__.py` | `if/elif` 选驱动 | `load_driver()` 按名解析 |
| `core/config.py` | `Literal["echo","s3"]` | 普通 `str` |
| `api/deps.py` | 每加权限改这里 | 模块内声明 + `require()` |
| `core/exceptions.py` | 每加错误码改这里 | `ModuleErrors(slot=)` |
| `workers.py` | 手动 import handler | 模块被加载即注册 |

**验收标准**：新增一个模块后，`git status` 里不该出现任何 `core/`、`api/`、`infra/`、`db/` 的改动。

---

## 3. 仍需人工协调的四件事

### 3.1 数据库迁移文件（最常见的冲突源）

`migrations/versions/` 下的文件名带随机后缀，但 `down_revision` 形成链，两人同时生成会出现两个 head。

约定：

```bash
# 1. 开工前先拉最新并升级
git pull && alembic upgrade head

# 2. 自己分支上生成迁移
alembic revision --autogenerate -m "voice init"

# 3. 合并前检查是否出现多 head
alembic heads
```

如果出现两个 head（`alembic heads` 输出两行），**不要**手改 `down_revision` 猜顺序，
用 merge 迁移显式声明：

```bash
alembic merge -m "merge voice and identity" <head_a> <head_b>
```

更稳的做法：**迁移文件由一个人统一生成**，其他人只在 PR 里说明表结构变更。

### 3.2 错误码号段

按 `EXTENDING.md` 第 7 节的 slot 表分配。新模块申请一个 slot，
不要"借"别人的号段 —— 分段重复会在运行时抛 `RegistryError`（这是故意的）。

### 3.3 权限码前缀

一个模块独占一个资源前缀：`task:*` / `identity:*` / `music:*`。
不要出现两个模块都声明 `task:approve` 这种情况 —— 启动时 `catalog.declare` 会直接报冲突。

### 3.4 路由前缀

模块独占前缀，避免通配路径互相吞：

```python
router = APIRouter(prefix="/voice")     # 好
router = APIRouter(prefix="")           # 危险，会抢别人的路径
```

确实需要挂在根上的模块（如 `/health`、`/models`），把 `ModuleSpec.order` 调大（后注册），
避免吃掉静态路径。

---

## 4. 接口契约：先定，再各写各的

前后端 / 两个人之间的接口，先写进 `schema.py` 再实现：

```python
class CreateVoiceIn(CamelModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str = "female-warm"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
```

因为 `CamelModel` 自动做 camelCase 转换，**契约写一次，前后端各自照着写**，
不需要中间维护一份接口文档 —— `/openapi.json` 就是文档。

约定：

- 路径用复数名词：`/voices`，不用 `/getVoices`
- 状态变更用 POST + 动词子路径：`POST /voices/{id}/retry`
- 分页统一 `page` / `pageSize`，返回 `items / total / page / pageSize`
- 时间统一 `UtcDateTime`（输出 `...Z`）
- 未实现的接口**抛 `NotImplementedFeature`**，返回 501 + 中文文案，
  前端能区分"没做"和"崩了"

---

## 5. 前后端联调流程

```
前端：VITE_USE_MOCK=true   → 自己跑，不依赖后端进度
后端：mock 数据与前端本地数据字段一致
     ↓ 后端接口可用
前端：VITE_USE_MOCK=false  → 直连 /api/v1（vite proxy 转发）
```

后端接口没写完时，返回 501 是**正确行为**，前端按 501 显示"功能建设中"，
不要为了"看起来能跑"返回假数据 —— 那会让前端误以为联调完成。

---

## 6. 典型场景演练

**场景**：A 做任务重试优化，B 同时做用户体系。

| 步骤 | A | B |
|---|---|---|
| 1 | 拉最新，`alembic upgrade head` | 同 |
| 2 | 改 `modules/task/service.py` | `app new-module identity` |
| 3 | 加权限点 → 改 `task/__init__.py` | 建用户/角色表 |
| 4 | 生成迁移 `task retry` | 生成迁移 `identity init` |
| 5 | 加错误码 → `ModuleErrors("task", slot=3)` | 改 `get_actor` 查库（**唯一共享点**） |
| 6 | 提交 PR | 提交 PR |

第 5 步是唯一可能冲突的地方（`api/deps.py` 的 `get_actor`）。处理方式：

- B 先提 PR 合并；
- A rebase 后重新跑测试（`get_actor` 变了，但 A 的业务代码不用改）；
- 如果 A 也要改 `get_actor`，**先沟通**，由一个人改完另一个 rebase。

除此之外零冲突：文件不重叠、迁移可 merge、权限码前缀不同、错误码号段不同。

---

## 7. Code Review 检查清单

评审时优先看这几条，比看代码风格更重要：

- [ ] 有没有为了图快在 `modules/` 里 import 另一个模块？
- [ ] 新增的权限点是否在 `ModuleSpec` 里声明了？
- [ ] 新增的接口是否声明了 `require(...)`？（漏了就是裸奔）
- [ ] 长耗时调用是否走了队列，而不是在请求线程里同步等？
- [ ] 错误是否用 `raise` 业务异常，而不是自己拼 `JSONResponse`？
- [ ] 改表有没有迁移，迁移是否基于最新 head？
- [ ] 有没有硬编码角色名 / 供应商名 / 模型清单？
- [ ] `pytest` 与 `ruff` 是否都过？（架构测试会拦住依赖违规）

---

## 8. 提交信息约定

```
feat(task): 支持批量重试
fix(generation): 轮询超时未置终态
refactor(infra): 驱动解析改为按名加载
docs(extending): 补权限管理案例
test(architecture): 增加模块自注册检查
```

scope 用模块名或层名，方便按模块追溯变更。
