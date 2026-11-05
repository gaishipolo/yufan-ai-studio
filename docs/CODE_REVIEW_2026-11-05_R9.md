# 代码评审报告（第九轮 · 仅后端 · R8 修复复核）

> 评审时间：2026-11-05 16:48 ｜ 范围：`1fb2613`（R8 的 5 项修复）及其直接影响面；前端不在范围内
> 关联：[`R8`](./CODE_REVIEW_2026-11-05_R8.md)｜[`R7`](./CODE_REVIEW_2026-11-05_R7.md)｜[`R6`](./CODE_REVIEW_2026-11-05_R6.md)｜[`R5`](./CODE_REVIEW_2026-11-05_R5.md)｜[`R4`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

**R8 的 5 条都动了，写法也从「按入口打补丁」换成了「按机制改」**——这一轮的思路是对的：闸门下沉到 `hash_password_async` / `verify_password_async`，用 `_GATE_DEPTH` 防嵌套自锁，于是「新开发者忘了包」不会退化成 CPU 抢占。R8-01 的核心症状确实消失了：限流不介入时（正确旧口令、每次 `reset` 计数），20/40 并发改密期间 `select 1` 稳定在 **1–4 ms**、连接峰值 **5**（4 个名额 + 1 个观察者）。

但**文档和实现还是对不上**，而且这次是文档自己立的规矩：

`EXTENDING.md` §2.5 现在写着「闸门必须在**第一次查库之前**」，并把 `reset_password` 列进了「已包装的四条路径」。实际上 `reset_password` 的闸门在 `_require_user` 与 `_guard_target_not_above` **之后**——先借连接、再排队，正是 R7-01 那条纪律禁止的形状。实测 20 并发 → 连接峰值 **15**、`select 1` **296 ms**；40 并发 → **997 ms**。同一份代码里的 `create_user` 却是闸门打头，两个方法紧挨着，写法相反。

第二个问题更值得停下来看：R8-02 的守卫**用错了比较基础**。`_guard_target_not_above` 比的是「目标的权限集 ⊆ 操作者」——这规则是给重置口令、停用、改角色这类**授出访问权**的动作设计的。拿它守昵称和头像，等于要求「人事管理员必须持有被管理者的一切业务权限」。实测：一个只有 `identity:read` + `identity:update` 的委派角色，连 viewer 的昵称都改不了（40323）。**R8-02 之前这是 200**——这一轮把一个"可用的功能"换成了"看起来安全但没人能用"。

### R8 逐条复核（全部实测）

| 编号 | 结论 | 证据 |
|---|---|---|
| R8-01 闸门只装 login | ✅ 修好（`change_password` / `create_user`）| 正确旧口令下 20/40 并发改密 → `select 1` **1–4 ms**、连接峰值 **5**；闸门在 `_require_user` 之前 |
| R8-02 资料字段只守 email | 🟡 修好了超管那一半 | 非超管改超管 `displayName` / `avatarUrl` → **403 / 40322**；但目标为普通账号时守卫过宽（见 R9-02） |
| R8-03 `X-Request-Id` 无校验 | ✅ 修好 | `abcdefgh`（8 位）原样回显；`abc` / 含空格 / 100 位 → 全部替换成 32 位 uuid；首字符 `-` 放行（无害） |
| R8-04 写原值免检 | ✅ 修好 | 守卫按「要写什么」触发；`{"isActive": true}`（原值）、`{"displayName": 原值}` 均 403 / 40322 |
| R8-06 排序键不唯一 | ✅ 修好 | `image` 编译出的 SQL 结尾是 `created_at DESC, id DESC`；`asset` 同；`task` 三个分支都补了主键 |

### 质量基线（实测）

```
pytest         187 passed, 1 warning (35.00s)
ruff           All checks passed
mypy           Success: no issues found in 110 source files
export-openapi 无变化（json / md / ts 三件套）
```

---

## 二、🟡 R9-01 `reset_password` 的闸门装在守卫之后，与同一文件里的 `create_user` 相反

**位置**：`src/modules/identity/service.py:427-441`（`reset_password`）、`:329-332`（`create_user` 的闸门打头）、`docs/EXTENDING.md:247-255`

```python
# create_user：闸门打头（正确）
async with password_slot():
    if await self.users.get_by_username(...) is not None: ...

# reset_password：闸门在两次查库之后（R9-01）
user = await self._require_user(user_id)              # ← 借走连接
await self._guard_target_not_above(user, actor)       # ← 再借（内部 resolve_identity 是 3 条查询）
async with password_slot():                           # ← 才排队
    await self.set_password(user, new_password)
    await self.session.commit()
```

代码里的注释给的理是「守卫要读权限，本来就得有连接」——但 `create_user` 的 `_guard_role_grant` 同样要读权限，它就在闸门**里面**。所以这不是能力限制，是两处写法不一致。

### 实测（超管调用，20 / 40 并发重置同一账号口令）

```
并发重置口令 n= 0 → select 1     1 ms，连接峰值  1
并发重置口令 n=20 → select 1   296 ms，连接峰值 15   ← 池上限
并发重置口令 n=40 → select 1   997 ms，连接峰值 15

对照（R8-01 修好之后，change_password，正确旧口令）：
并发 n=20 → select 1     4 ms，连接峰值  5
并发 n=40 → select 1     1 ms，连接峰值  5
```

门槛比 R8-01 高（需要 `identity:update`），所以严重度不是同一档。但**文档已经承诺了这条不变式**，而且 §2.5 把 `reset_password` 明确列为已包装的四条之一。守则写了、实现没守，比不写更糟——下一个人会照着文档以为它是安全的。

### 建议

把闸门提到 `_require_user` 之前，与 `create_user` 对齐。守卫在闸门内跑只多占一个名额（`resolve_identity` 是 3 条短查询，不涉及 scrypt），峰值仍是 4：

```python
async with password_slot():
    user = await self._require_user(user_id)
    await self._guard_target_not_above(user, actor)
    await self.set_password(user, new_password)
    await self.session.commit()
```

顺手在 `tests/test_login_gate.py` 补一条 reset 的峰值用例——现在只有 login 和 change_password 两条，`reset_password` 是唯一没被断言过的不变式，正好是唯一破了的那条。

---

## 三、🟡 R9-02 资料守卫用「权限子集」判定，委派角色连改昵称都做不到

**位置**：`src/modules/identity/service.py:373-376`（守卫）、`:757-773`（`_guard_target_not_above`）

R8-02 的修法是把三个展示字段整段交给 `_guard_target_not_above`。这个守卫的内部逻辑是：

```python
if identity.is_superuser:            # 目标是超管 → 只有超管能动
    self._require_superuser(actor, ...)
    return
missing = self._missing_grants(actor, identity.permissions)   # 目标有、操作者没有的权限码
if missing:
    raise PermissionEscalation(...)                            # 40323
```

「目标有、操作者没有 → 拒绝」对**授权动作**是对的：重置口令等于拿到对方身份，停用等于拿走对方访问权，改角色等于改对方权限。但对昵称和头像，这条规则的后果是：**操作者必须持有被管理者的一切业务权限，才能改一个显示字符串**。

### 实测（`hr01`：角色权限只有 `identity:read` + `identity:update`）

```
改 admin  昵称 → 403 40322 操作超级管理员账号 admin：只有超级管理员可以操作
改 editor 昵称 → 403 40323 不能操作权限高于自己的账号 editor
改 viewer 昵称 → 403 40323 不能操作权限高于自己的账号 viewer
改自己    昵称 → 200      （走 /me，不经过这里）

对照：同一角色重置 viewer 口令 → 403 40323；停用 viewer → 403 40300（缺 identity:delete）
```

viewer 拥有 `asset:read` / `task:read` 之类，`hr01` 没有 → 判定"权限高于自己"。于是这个角色**只能编辑权限集为空的账号**，也就是实际上谁也编辑不了。R8-02 的提交说明写的是"改他人昵称/邮箱/头像 → 目标不能高于自己"——从「能不能冒充」的角度，一个 viewer 的昵称并不值得冒充，真正需要挡的是**比操作者更有权威的目标**。

这是本轮唯一的功能回归：R8-02 之前 `PATCH /users/{viewer} {"displayName": ...}` 是 200，现在是 403。

### 建议

展示字段需要的是「不能冒充比自己更有权威的人」，而不是「权限必须覆盖对方」。两个方向，选一个并写进 §2.3 的表：

1. **轻守卫**（推荐，改动最小）：展示字段只判「目标是不是超管 / 角色标记是否高于自己」，不判权限全集。这样 `hr01` 能改 viewer、不能改 admin，语义正好对上"防冒充"。
2. **保持严格**：那就把 `identity:update` 的说明改成"只能编辑权限严格低于自己的账号"，并在权限目录里写清楚——否则这个权限点在界面上承诺的功能和实际能力不是一回事，运营会当作 bug 报上来。

我个人倾向前者：`_missing_grants` 是为"授出访问权"设计的，套在展示字段上属于语义漂移，而这条路径上还有 `identity:update` 这个权限点在做第一道控制。

---

## 四、💭 R9-03 新用例只走了「目标含 `*`」的分支，正好绕开了上面的回归

`tests/test_identity_api.py` 新增的两条用例用的是：

```python
target, _, _ = _make_user(client, admin, "probe_target", ["*"])
```

而 `is_superuser` 的判定就是 `"*" in permissions`（`service.py:109-110`）。所以这两条用例走的全是 `_guard_target_not_above` 的第一分支（`is_superuser → _require_superuser`），断言 `40322`。**第二分支（`_missing_grants → 40323`）一个断言都没有**——R9-02 就在这里。

这不是"测试写错了"，是测试目标选得太顺手：`["*"]` 是最省事的"比我高"的账号。建议补一条「委派角色改普通账号」的用例，并明确预期——能改还是不能改，必须是**有意选的**，而不是被断言顺手固定下来的。前几轮的教训是「测试把漏洞写成断言」（R2 的 `scope=all`、R6 的 XFF），这一轮是更隐蔽的版本：**测试选的目标恰好让分支不进问题区**。

---

## 五、💭 R9-04 `test_change_password_does_not_exhaust_the_connection_pool` 的绿灯有一半是限流给的

那条用例让 20 个并发全都传**错**旧口令，键都是 `pw:u_001`。第 5 次失败之后 `throttle.reject()` 抛 `RateLimited`，剩下的请求**在进入闸门之前就退出了**（用例里 `contextlib.suppress(OldPasswordMismatch, RateLimited)` 把它们吞掉了）。所以"峰值 ≤ 4"这个断言，可能是限流给的，不一定是闸门给的。

我用「攻击者知道自己口令」的路径复测了一遍（每次都能过校验 → `throttle.reset` → 限流不介入）：

```
n=20 → select 1  4 ms，连接峰值 5
n=40 → select 1  1 ms，连接峰值 5
```

**实现是对的**，闸门真的兜住了。但用例本身可以更利：把旧口令改成正确的（交替 `A→B→A→B`），或者在批次开始时清一次 `throttle._failures`，让断言只依赖闸门。否则将来有人把 `throttle.reject` 从这条路径上拿掉（比如觉得"改密不该限流"），用例会**从绿变红**，而实现其实没变——反过来也一样：闸门退化 + 限流在位，用例仍然是绿的。一个断言最好只测一件事。

---

## 六、值得保留的做法（这轮新增）

1. **闸门下沉到原语，`_GATE_DEPTH` 防嵌套自锁**（`security.py:70`、`:113-136`）。这是这一轮真正的进步：把「谁负责排队」从约定变成了机制。注释里那句「业务方法包一层是为了把『先排队、再拿连接』写进代码，这一条安全层兜不了底——它不知道调用方手里有没有连接」是这个设计的关键说明，建议保留。
2. **`ContextVar` 而不是 `threading.local`**，并写明「在闸门里 `create_task` 出去的协程会继承这个标记」。这个副作用是真的（会绕过名额），写下来比藏着好。
3. **`_PROFILE_FIELDS` 提成常量**（`service.py:78`）。`update_me` 与 `update_user` 从此共用一份清单，新增展示字段时不会只改一处——这是"按机制改"的同一个思路。
4. **`_resolve_request_id()` 单独成函数**（`middleware.py:33-38`）。校验逻辑从 `__call__` 里提出来，既能单测，也不会在下一次改中间件时被顺手删掉。
5. **`paginate` 的 docstring 把约定写成约束**（`db/repository.py:57-65`）："传进来的 `stmt` 必须自己带 `order_by`，并且以主键收尾"。在公共入口写约束，比在每个调用点写注释更容易被读到。

---

## 七、建议顺序

| 顺序 | 事项 | 理由 |
|---|---|---|
| 1 | **R9-02** 展示字段换轻守卫（或改文档与权限说明） | 唯一的功能回归，且越晚改越会被当成"设计如此" |
| 2 | **R9-01** `reset_password` 闸门提到查库之前 + 补峰值用例 | 修法两行；不修就得改 §2.5 的措辞，否则文档在撒谎 |
| 3 | **R9-03** 补「委派角色改普通账号」用例，把预期固定下来 | 与 1 同一次提交，用例即是决策记录 |
| 4 | **R9-04** 让那条峰值用例不依赖限流 | 十分钟，防止将来"改实现 → 用例乱红/乱绿" |

---

## 八、验证方式（可复现）

```bash
# 基线
cd api && ./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy src
./.venv/Scripts/python.exe -m cli export-openapi --check
```

**R9-01 重置口令的连接占用**（需要超管主体，否则守卫先拦掉）：

```python
admin = CurrentUser(id="u_001", name="老大", permissions=frozenset({"*"}))
# 20 / 40 个并发 reset_password("u_003", ...)，同时测 select 1 与 checkout/checkin 峰值
# n=20 → 296 ms / 峰值 15；n=40 → 997 ms / 峰值 15
```

**R9-02 委派角色改资料**（需要一个只有 `identity:read` + `identity:update` 的账号，seed 里没有）：

```python
role = POST /identity/roles {"code":"hr","permissions":["identity:read","identity:update"]}
usr  = POST /identity/users {"username":"hr01","roleIds":[role_id], ...}
PATCH /identity/users/{admin_id}  {"displayName":"改名"}  → 403 40322
PATCH /identity/users/{editor_id} {"displayName":"改名"}  → 403 40323   ← R9-02
PATCH /identity/users/{viewer_id} {"displayName":"改名"}  → 403 40323   ← R9-02（R8 之前是 200）
PATCH /identity/me                {"displayName":"改名"}  → 200
```

**R8-03 请求头**：`abcdefgh` 原样回显；`abc`、含空格、100 位 `a` → 换成 32 位 uuid。

**R9-04 闸门独立于限流**：用**正确**旧口令交替改密（`admin@12345 ↔ admin@123456`），每次 `throttle.reset` 生效，限流不介入 → 峰值 5、`select 1` 1–4 ms。
