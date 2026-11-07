# 扩展指南

这份文档回答一个问题：**我要加功能时，代码该写在哪，以及什么绝对不能碰。**

> 文中路径默认相对 `api/src/`（导入根）。`core/`、`api/`、`infra/`、`generation/`、`modules/`
> 都是**顶层包**，import 时不带 `src.` 前缀：`from core.config import settings`。

---

## 0. 三条规则

1. **依赖只能向下**：`api（装配）→ modules → generation → db / infra / core`。
   且 `api` 不 import 任何业务模块——它靠目录扫描发现模块，所以新增模块不用改它。
   业务模块之间同样不许互相 import。
2. **新功能 = 新增文件**。如果发现自己在改 `api/`、`core/`、`infra/`、`db/`、`generation/`，
   先停下来确认是不是设计缺口。
3. **模块的对外契约只有 `ModuleSpec`**：路由 + 权限点 + 启动钩子。
   handler 不走这里，靠模块里的 `@register_handler` 装饰器注册。
   反过来也成立：**声明了却没人消费的字段不许留** —— `ModuleSpec.jobs` 曾经声明了
   但没有任何代码读它，只会让人以为「写进去就会注册」，已于 2026-11-06 删除。

第 1 条由 `tests/test_architecture.py` 用 AST 静态检查强制执行；第 3 条的「可发现性」
（每个子包导出 `module`、路由非空、权限点已声明）也有测试兜住。
第 2 条**没有测试能拦**——工具判断不了「这算不算设计缺口」，靠 code review 和第 10 节的判断标准。

补充一条默认值：**新模块默认受权限保护**。`ModuleSpec.permissions` 为空时
`test_every_module_declares_permissions` 会失败，除非显式标 `public=True`
（只适用于健康检查这类必须匿名可访问的接口）。默认安全，想裸奔要写出来。

再补一条收尾动作：**动了接口就重跑 `app export-openapi`**，把 `docs/openapi.json`、
`docs/API.md`、`studio/src/shared/api/schema.ts`、`studio/src/shared/api/permissions.ts`
四份生成物一起提交（详见第 8 节）。
忘了跑 `pytest` 会红，但别等到红——前端同事等的就是这个 diff。

---

## 1. 加一个业务模块（5 分钟）

```bash
app new-module voice --label 配音 --perm voice --slot 9
```

`--slot` 是错误码号段，取值见第 7 节的分配表（示例占用 9，别和 `task` 的 3 撞车）。
它必填（不传会被拒），并且会真的写进 `errors.py`；号段已被别的模块占用时，生成前就报错。

生成 7 个文件：

```
modules/voice/
├── __init__.py       # ModuleSpec：路由 + 4 个权限点
├── models.py         # ORM 实体（表名 voices）
├── schema.py         # 出入参（camelCase 自动转换）
├── service.py        # 业务规则（当前抛 501）
├── dependencies.py   # 依赖装配 + 权限依赖
├── errors.py         # ModuleErrors(slot=9) + 一个示例错误码
└── router.py         # 2 个接口（列表 / 创建）
```

**重启进程后服务就能跑**：模块被自动发现（发现结果进程内缓存，不重启看不到），
`GET /api/v1/voice` 与 `POST /api/v1/voice` 都返回 `501`（文案「配音列表待实现」「配音创建待实现」），
权限点已进入全局目录。
`voices` 表也会被 `create_all` 建好——但**只在 `APP_ENV` 为 dev/test 时**，
生产环境不会自动建表（`app.py` 里写了 `if settings.app_env != "prod"`）。

接下来只有三件事：

```bash
# 1. 写业务逻辑（service.py 的 NotImplementedFeature 换成真实实现）
# 2. 生成迁移
alembic revision --autogenerate -m "voice init"
# 3. 确认被识别
app modules
```

> **迁移这一步有个坑**：`autogenerate` 是拿「当前数据库」和「ORM 元数据」做对比。
> 本地库如果是 `create_all` 建出来的（没有 `alembic_version` 记录），它会把
> **所有表**都当成新增，生成一份重建全库的迁移。正确顺序是先对齐版本再生成：
>
> ```bash
> alembic stamp head          # 本地库已由 create_all 建好，只补版本号
> alembic revision --autogenerate -m "voice init"   # 此时 diff 只剩你新加的表
> ```
>
> 拿不准就用一个空库生成：`DB_URL=sqlite+aiosqlite:///./var/tmp.db alembic upgrade head`
> 再 `revision --autogenerate`。

**没有第四步。** 不需要改路由聚合、不需要改模型注册表、不需要改依赖文件。

### 1.1 列表接口必须回答的两个问题

新模块的 `GET /xxx` 列表，除了"能查到数据"之外还有两条约定。漏掉任何一条，
都会在数据长起来之后变成事故——而且是那种"接口没报错，只是越来越慢"的事故。

**一、数据范围：谁能看到全部？** 用 `资源:read:all` 权限点决定，不要按角色名判断。
同一个工作台里若两个列表给了两个答案，用户只会当成 bug。

| 模块 | 全部可见的权限点 | 说明 |
|---|---|---|
| `task` | `task:read:all` | 内容生产要能互相看，客服要能帮别人重试 |
| `image` | `image:read:all` | 与 task 对齐：生图历史是同一批人的产出 |
| `asset` | **没有**（只有 `owner_id == actor.id`） | 刻意：素材是个人上传的原始媒体，没有"查看他人素材"的业务场景 |

`asset` 这一行是**决定**，不是遗漏。要打开它，先回答"谁需要看别人的原始上传"，
再加 `asset:read:all` 并把列表补上分页——照 `image` 的样子做（R7-05）。

**二、分页：`total` 是全量还是本页？** 统一用 `PageParams`（`page` / `pageSize`，
默认 24、上限 100）和 `repo.paginate()`，`total` 走 `count(*)`。

```python
stmt = stmt.order_by(Xxx.created_at.desc(), Xxx.id.desc())   # 排序键必须以主键收尾
rows, total = await self.repo.paginate(stmt, page)           # 不要 list() 再 len()
return Page.create([XxxOut.model_validate(r) for r in rows], total, page)
```

排序键不唯一时，同一时间戳的行在翻页边界上顺序不定，会重复出现或整行漏掉。
sqlite 按 rowid 兜底，看起来「总是有顺序」，但那是实现细节，不是查询的一部分（R8-06）。

`len(rows)` 在"有 200 条、这页 24 条"时报 24，前端翻到第二页就翻不动了；
而全量返回在数据长起来之后是"一进页面打满数据库"。`GET /images` 就是范围打开之后
才发现自己一直是全量的（R7-02）。

---

## 2. 用户与权限：已经实现，别重写

`modules/identity` 已落地：登录、用户与角色 CRUD、权限目录、**权限实时生效**。
这一节讲它的设计约定，以及你要改它时该动哪里。

### 2.1 权限从哪来（这是最容易改错的地方）

```
POST /identity/login → 校验口令 → 角色展开成权限码 → 签发 JWT（只用来识别「你是谁」）
请求带 Bearer       → get_actor 解出 sub
                    → resolve_principal(api/authn.py) 用数据库覆盖权限   ← 关键一步
                    → require("task:create") 判定
```

**权限不来自令牌。** 令牌里虽然也写了 `perms`，但那是调试用的快照；
真正的判定依据是每个请求查一次库。所以：

| 场景 | 效果 |
|---|---|
| 管理员撤了某个权限 | 下一个请求就生效，不用重新登录 |
| 账号被停用 | 立刻 401，不是等 2 小时 TTL |
| 用户改了密码 / 被管理员重置密码 | 此前签发的令牌全部失效（`iat < password_changed_at`），只保留改密后新登录的 |
| 用户改了昵称 | 立即同步（历史任务里的 `author_name` 是快照，不回填） |

代价是每个带令牌的请求多 2~3 次单表查询。后台系统这个量级完全可接受；
真要优化就加请求级或秒级缓存，**不要**退回「信任令牌快照」——那等于把停用账号的窗口
重新打开 2 小时。

> 改密失效旧令牌靠的是 `users.password_changed_at` 与令牌的 `iat` 比大小，
> 不需要黑名单存储。`iat` 只有秒级精度，所以「改密同一秒内签发的令牌」仍然有效——
> 这是有意留的 1 秒窗口，改成 `<=` 会让改密当秒的新登录也立刻失效。

> 登录失败有进程内限流（`identity/throttle.py`）。三条行为是刻意设计的，
> 改之前先看注释里的理由：
> - 同一「用户名 + 客户端 IP」允许 5 次失败，**第 6 次才 429**；正确密码永远放行。
>   若锁定先于验密，任何人用 5 个乱码就能锁死一个已知用户名（未认证的拒绝服务）。
> - 超过阈值后每次尝试先付 `penalty()` 秒等待成本（0.5s 起、封顶 5s），
>   而不是纯硬拒——延迟对爆破同样致命，但不会把记错密码的真实用户关在门外。
> - 计数表是 LRU + 滑动窗口，有 `MAX_KEYS` 上限也会回收过期键：裸 dict 可以被
>   匿名请求（换用户名）线性撑大。
>
> 客户端 IP 走 `core/net.py`：**只有直连对端在 `TRUSTED_PROXIES` 里才读
> `X-Forwarded-For`**。部署在 nginx / ingress 后面时必须配这个变量，否则键退化成
> 「只按用户名」；反过来无条件信任这个头，攻击者换个假 IP 就绕过限流。
> 取值方向是**从右往左**：XFF 是追加语义（`$proxy_add_x_forwarded_for`），最右一段
> 才是代理亲眼看到的地址，左边的每一段都可以由客户端自报。取最左 = 取攻击者可控值。
> 两条部署纪律：**不要**把 `TRUSTED_PROXIES` 配成 `0.0.0.0/0`（那时「跳过可信」等价于
> 「永远取最左」）；**不要**同时开 uvicorn `--proxy-headers`（它会先改写
> `request.client`，两条路径重叠后「谁在解析这个头」就不可推理了，只保留一条）。
> 切 Redis 时只换 `throttle.py` 这一个文件，`authenticate` 不用动。

> 为什么做成注入点而不是直接改 `get_actor`：装配层 import 业务模块会违反依赖方向，
> 而且 `identity/dependencies.py` 要 `from api.deps import require`，顶层互相 import
> 就是循环导入。`api/authn.py` 把「主体从哪来」变成和 `get_storage` /
> `get_queue` 同形态的注入点，`get_actor` 永远不认识 identity 模块。

### 2.2 表

| 表 | 关键字段 | 说明 |
|---|---|---|
| `users` | `username`(unique)、`password_hash`、`display_name`、`email`、`avatar_url`、`is_active`、`last_login_at`、`password_changed_at` | 不物理删除：历史任务的 `author_id` 还指着他 |
| `roles` | `code`(unique)、`name`、`is_superuser`、`is_builtin`、`sort_order` | `is_superuser` 展开为 `*`，**不靠角色名 == admin** |
| `role_permissions` | `role_id` + `permission`（唯一约束） | 权限码存字符串，权威在 `catalog` |
| `user_roles` | `user_id` + `role_id`（唯一约束） | |
| `user_sessions` | `user_id`、`refresh_jti`、`user_agent`、`ip`、`expires_at`、`revoked_at`、`revoked_reason` | 一行 = 一次登录。access 令牌带 `sid` 指向它，吊销即刻生效（见 2.6） |
| `login_events` | `user_id`(可空)、`username`、`success`、`ip`、`user_agent`、`reason`、`created_at` | **只追加**的审计，不挂在 `users` 上；保留期由 `app prune-logins` 控制 |

两个刻意的选择：关联用 `role_id` 而不是 `role_code`（角色改名不用级联改两张表；
拼错 code 在 sqlite 里不报错、只会静默少一批权限），以及不建外键（与项目其他模块一致，
删除前由 service 显式检查引用）。

### 2.3 权限码（5 个，`modules/identity/permissions.py`）

`identity:read` / `identity:create` / `identity:update` / `identity:delete` / `identity:role:manage`

`role:manage` 单独拆出来是有原因的：**能改昵称不等于能给自己提权**。
授角色、建角色、改角色权限全部归它，`identity:update` 只管资料和停用。

但要真正兑现这句话，光拆权限点不够——**任何能间接拿到 `*` 的操作都必须再过一道判定**。
五个入口共用同一组 helper（`service.py` 的 `_require_superuser` / `_guard_permission_grant`
/ `_guard_role_grant` / `_guard_target_not_above` / `_guard_role_not_above`）：

| 入口 | 额外要求 |
|---|---|
| `set_user_roles` / `create_user` | 目标角色是超管 → 超管；否则该角色的权限必须**是自己的子集**；目标账号本身不能高于自己 |
| `create_role` / `set_role_permissions` | 权限列表含 `*`，或含自己没有的权限码 → 超管 / 403；被改的角色不能强于自己 |
| `reset_password` | 目标账号权限高于自己 → 403；目标是超管 → 超管 |
| `update_user`（停用）/ `deactivate_user` | 同上：不能停用比自己强的账号 |
| `update_user`（改他人**展示字段**：昵称 / 头像） | 目标不是超管即可（`_guard_display_target`）。用不到「权限子集」：那是给**授出访问权**的动作设计的，套在昵称上等于要求人事管理员持有被管理者的全部业务权限（R9-02） |
| `update_user`（改他人邮箱 / 写 `is_active`） | 严格判定：目标权限不得高于自己，超管目标需超管。守卫按「要写什么」触发，**不按值变没变**（R8-04）；邮箱是「找回密码」的入口，接上之后改它就是接管账号的第一步（R7-03） |
| `delete_role` | 角色权限强于自己 → 403（角色全局共享，没有 owner 字段） |

**撤销方向必须和授予方向对等**。这是上一轮漏掉的一半：那时「给 editor 角色」被挡，
「清空 editor 的角色 / 把 editor 角色改成只读」却放行——同一件事换个入口就绕过去了。
新增任何「改别人的权限集合」的入口时，同时问两个问题：能不能**给**，能不能**撤**。

**守卫的强度要跟动作的语义走，不能跟字段走**。`_guard_target_not_above` 判的是
「目标的权限集 ⊆ 操作者」，它回答的是「这次操作会不会让对方获得/失去访问权」。
授出访问权的动作（重置口令、停用、改角色）必须用它；改一个显示字符串不是这类动作，
用的是 `_guard_display_target`（只挡超管目标）。判据写错的后果不是"不够安全"，
而是**功能悄悄失效**：委派给人事的 `identity:update` 会变成"只能编辑权限集为空的账号"，
也就是实际上谁也编辑不了（R9-02）。新增守卫前先问一句：这个动作移交的是什么——
访问权，还是只是数据？

「子集」用的是 `CurrentUser.has()`，也就是鉴权主路径那一套通配语义（`*` / `资源:*`），
不要另写一套字符串匹配。改这里的规则时，四条负向用例必须一起改
（`test_identity_api.py` 里 `test_role_manage_cannot_write_wildcard` 等四组）。

### 2.4 内置角色

| code | 权限 | 说明 |
|---|---|---|
| `admin` | `*` | `is_superuser`，新模块的权限点自动覆盖 |
| `editor` | `task:*`、`asset:*`、`image:*` + 各目录 `:read` | 内容生产全量 |
| `viewer` | 各资源的 `:read` | 只读 |

启动钩子只补「角色不存在」，**不覆盖已有角色的权限**——管理员手工调过的配置
不该在下次重启被悄悄改回去。代价是新增模块的权限点不会自动进内置角色，
这是安全的默认值（宁缺勿滥），在角色界面勾一下即可。

### 2.5 红线（已在 service 里实现，别绕过）

- 不能停用/删除自己（手滑一点就把自己锁在门外）；自助 `PATCH /me` 只接受展示字段，
  `isActive` 根本不在入参里
- 系统必须保留至少一个启用的超级管理员（否则只能改数据库了）
- 内置角色不可删、`code` 不可改（权限内容可调，运营需要）
- 登录失败对「用户不存在」和「密码错误」返回**同一个错误码和文案**
- 新口令 ≥ 10 位、不能是纯数字、不能与当前口令相同；管理员建号时直接指定
  （不做临时密码那套状态机）。策略只有一份，在 `identity/policy.py`，
  API validator 与 `app create-admin` 共用——运维命令是唯一能创建「权限最大的账号」的
  入口，它不能是唯一绕过策略的那个
- 写口令只有 `service.set_password` 一个方法（`async`）：它同时更新
  `password_changed_at`，五条路径（自助改密 / 管理员重置 / `create-admin` 轮换 / 建号 /
  演示数据）都走它，新增第六条也别绕开。哈希在线程池里算
  （`core/security.hash_password_async`）——单次 scrypt 约 150ms，
  留在事件循环里会让**整个进程**停摆，不只是「这个登录变慢」
- 凡是会算 scrypt 的 service 方法，**整段**包在 `password_slot()` 里（闸门容量 =
  哈希线程池 worker 数）。目前是 `authenticate` / `change_password` / `reset_password` /
  `create_user` 四条，新增第五条照做。
  为什么要包：线程池只收窄「同时算几个」，不收窄「同时有几个请求在等算」；而等待时那条
  数据库连接已经借出去了（session 要到请求结束才还）。不设闸门的话，几十个并发登录就
  能占满连接池、拖慢所有要查库的接口——CPU 让开了，连接池顶上（R7-01）。
  两条纪律：闸门必须在**第一次查库之前**（只包验密没有意义，连接已经拿走）、
  限流的惩罚等待必须在闸门**外面**（否则爆破者用 5 秒等待就能占满名额）。
  失败路径还要在闸门**里** `session.rollback()`：留到 teardown 才还，峰值会高于闸门容量。
  `hash_password_async` / `verify_password_async` 自己也会取名額（`_GATE_DEPTH` 标记
  防嵌套自锁），所以「忘了包」不会把线程池挤爆——但那一层不知道你手里有没有连接，
  连接维度只有 service 层包得住（R8-01）。
  四条路径的**形状要一致**：闸门打头、守卫在闸门内。`reset_password` 曾经把闸门放在
  两次查库之后（R9-01），而紧挨着的 `create_user` 是闸门打头——同一个文件里两种写法，
  读代码的人只会学到错的那一种。对照着写，别凭记忆
- 提权面统一判定：见 2.3 的表，四个入口不许各写一套

红线的实现方式统一是「**先改 → flush → 再数一遍超管**」，归零就抛错由事务回滚。
不要为每种操作手写「这次会不会锁死」的推演——间接路径（改角色标记、换角色、停用账号）
都会走到同一条判断。

### 2.6 会话、令牌与登录审计

令牌是**无状态**的，签名有效只说明「没被篡改」，不说明「还被允许使用」。
所以「退出登录」「踢下线」「续期」这三件事，靠令牌自己做不到，必须有服务端状态：

| 表 / 机制 | 解决什么 | 不这么做的后果 |
|---|---|---|
| `user_sessions`（一行 = 一次登录） | 登出、单设备踢下线、会话列表 | 登出只是前端删了本地副本，复制走的令牌还能用满 TTL |
| access 令牌带 `sid`，解析器每请求校验一次 | 吊销立刻生效 | 只能靠 TTL 兜底（最长 2 小时） |
| refresh 令牌 + 轮换（`refresh_jti`） | 用户不用每 2 小时重新登录 | 令牌到期就掉线，表单里没存的内容全丢 |
| `login_events`（只追加） | 安全追溯、异常登录 | 只剩 `last_login_at` 一个时间点，查不了「上周三凌晨那一次」 |

几条**不要改**的约定：

- **`sid` 为空一律跳过会话校验**。旧令牌、测试里直接签的令牌、匿名演示主体都没有
  会话可查，加了强校验只会让它们全部变 401，而它们本来也不是真实登录产生的。
- **refresh 只认 `typ: "refresh"`**，access 只认 `typ: "access"`。两种票同一个
  `JWT_SECRET` 签名，不区分用途就等于「一张 2 小时的票可以无限续期」。
- **刷新失败一律 40122**（`RefreshTokenInvalid`），不要细分「过期了 / 轮换过了 /
  会话被踢了」：前端的动作只有一个——清本地会话、回登录页。
- **`jti` 对不上时只拒绝，不吊销整条会话**。两个标签页同时续期，后到的那个必然拿着
  旧 jti，把它当攻击处理等于让用户因为「开了两个标签页」被迫重新登录。
- **改密要让 refresh 也失效**（比对 `iat` 与 `password_changed_at`）。只让 access
  失效的话，攻击者手里那张 refresh 还能换出新票，自救只做了一半。
- **改密 = 改口令 + 吊销全部会话**（含发起请求的这台，前端改密成功后回登录页）。
  只靠 `password_changed_at` 时，会话行的 `revoked_at` 还是 `None`、`expires_at`
  还在 7 天后，于是「我的设备」把几台已经换不出票的设备列成在线（R10-03）。
- **停用 = 置 `is_active` + 吊销全部会话**，两件事一起做，唯一入口是
  `IdentityService._cut_access`。只写 `is_active` 的话，停用只是「暂停」：
  管理员重新启用（误判纠正、临时冻结解除）时，同一张 access 与 refresh 会原地复活，
  连口令都不需要——refresh 寿命 7 天且滑动续期（R10-02）。**新增停用入口时不要
  自己写 `user.is_active = False`**，走 `_cut_access`。
- **账号停用要在验密之前判断**。放在之后，403 与 401 的差异就等于「口令猜对了」，
  而且这条路径每次都会清零失败计数，永远走不到 429（R10-04）。
- **失败审计会折叠**：同一（原因, 用户名, IP）60 秒一条，`rate_limited` 15 分钟一条。
  审计的价值是「发现有人在试」，第 2~N 次没有新增信息——不折叠的话匿名方可以
  线性地填满这张表，而且被 429 拦下的请求照样 insert + commit（R10-06）。
  成功登录不折叠。
- **无 `sid` 的令牌只在迁移窗口内被接受**。发布后最长 `ACCESS_TOKEN_TTL`（2 小时）
  内的旧令牌既不能登出、也不能被踢下线，只能靠 TTL 自然过期；窗口结束后如果
  想彻底拒绝，改 `principal.py` 里那一个分支即可，不要改别处。
- **读别人的登录审计走「可见性」档守卫**（`_guard_audit_target`：只挡超管），
  不套「目标的权限集 ⊆ 操作者」那条——那是给授出访问权用的，套在只读接口上会让
  `identity:read` 的审计员连普通操作员都查不了（R10-05）。
- **登出接口不挂 `get_actor`**：它要在令牌已失效时也返回 200（幂等）。
  挂上依赖的话，用户第二次点「退出」会看到一条 401 报错。
- **审计写在闸门里，且用 commit 结束事务**：失败登录会抛异常，`get_session` 在异常
  路径上回滚整个事务，留给请求结束写等于什么都没写。这里 commit 替代了原来的
  `rollback()`，都是「结束只读事务、连接立刻回池」，所以 `password_slot()` 的容量
  约束仍然成立（R7-01 / R9-01）。

审计保留期是配置（`LOGIN_EVENT_RETENTION_DAYS`，默认 90 天），清理靠
`app prune-logins`，**不要**塞进登录路径顺手删一批：那会把一次写变成「写 + 扫表」，
而且留多久该由运维决定，不该由某个请求的运气决定。

会话表也要清：`app prune-sessions --days 7` 删掉「过期超过 7 天」的行。
它和审计的保留期不是一回事——会话行一过 `expires_at` 就没人再读，
留着只撑表；「谁在哪台设备登录过」由 `login_events` 负责。
`GET /me/sessions` 本身也截断到最近 50 条。

**新增权限点后要跑 `app sync-roles`**。`ensure_builtin_roles` 只补「角色不存在」，
不覆盖已有权限（管理员手工调过的配置不该被重启改回去），所以老库里的
`editor` / `viewer` 不会自动长出新权限点——超管拿 `*` 全程自测发现不了，
非超管点新功能的按钮直接 403。这条命令**只加不减**、只补差集，可以放进部署脚本重复跑。

后台任务查账号状态走 `api/accounts.py` 的 `account_is_active()`（三态：
启用 / 停用 / 查不到）。它和 `PrincipalResolver` 是同一个理由的两种形态：
**请求内**的权威身份靠解析器，**没有请求**的调用方（worker、CLI）靠这个注入点。
不要为了省事在业务模块里 `from modules.identity...`——
`tests/test_architecture.py` 会拦住模块间的横向依赖，而且那种依赖一旦开口，
这个模块就没法单独删了。

### 2.7 要接 SSO / LDAP / 企业微信

不要改 `get_actor`，实现一个 `PrincipalResolver` 并注册（写法照
`modules/identity/principal.py`）。SSO 场景通常是**替换**现有解析器：
账号仍落 `users` 表，只是把「校验口令」换成「校验票据」。

注意注册只允许一个：两个解析器同时生效时「权限以谁为准」没有正确答案，
`register_principal_resolver` 会在启动期直接报错。

### 2.8 边界

| 该做 | 不该做 |
|---|---|
| 在 `identity` 模块里管用户、角色、绑定 | 在 `api/deps.py` 里写死角色名 |
| 用 `require("资源:动作")` 保护接口 | 在 service 里 `if user.role == "admin"` |
| 权限点由**拥有该资源的模块**声明 | 把所有权限码集中写在一张表里 |
| 角色配置读 `GET /identity/permissions`（即 `catalog`） | 维护一份独立的权限码清单 |

判断标准：**新模块上线要不要改你？** 不用，边界就是对的。

### 2.9 生产首启

```bash
alembic upgrade head
app create-admin --username admin      # 交互式输入密码；--password 可显式传
```

没有账号时谁也登不进去，所以「自动建一个 admin/123456」这种便利不能出现在生产。
开发环境用 `app seed` 即可（会写入 `admin/editor/viewer` 三个演示账号，见 README）。
`APP_ENV=prod` 下 `app seed` 会**直接拒绝执行**（`SystemExit(1)`）：演示账号的口令是
固定字符串，而部署脚本里「顺手跑一次 seed 初始化数据」很常见，进了生产就等于写入一个
已知口令的超管。确需写入请显式加 `--force`。

非生产环境（`APP_ENV=dev/test`）匿名请求仍回退到演示账号 `*`，方便前端联调；
`prod` 下 `allow_anonymous` 自动为 false，真去写 `ALLOW_ANONYMOUS=true` 也会在启动期被挡下来。
注意演示账号 id 固定为 `u_001`，所以 `app seed` 建的管理员也叫 `u_001`——
现有任务的 `author_id` 才不会变成悬空引用。


## 3. 案例：接入一个新的模型供应商

假设要接火山引擎。

**① 新建 `infra/ai/volcengine.py`**

```python
from infra.ai.base import (
    Capability, GenerationProgress, GenerationRequest, GenerationState, ModelSpec,
)


class VolcengineProvider:
    name = "volcengine"
    capabilities = frozenset({Capability.VIDEO, Capability.IMAGE})

    models = (
        ModelSpec(
            id="seedance-pro",
            label="Seedance Pro",
            capability=Capability.VIDEO,
            provider="volcengine",
            param_schema={
                "durationSec": {"type": "int", "min": 5, "max": 12, "default": 5},
                "aspectRatio": {"type": "enum", "options": ["9:16", "16:9"], "default": "9:16"},
            },
        ),
    )

    async def submit(self, request: GenerationRequest) -> str:
        # 调真实 HTTP 接口，返回供应商侧任务 ID。
        # 务必把 request.request_id 透传给厂商的幂等字段（client_token / request_id 等）：
        # 支持幂等键的厂商能让「超时后不敢确定是否已受理」这一整类问题直接消失，
        # 把「不能重复计费」从业务约定变成协议保证。
        ...

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        # 返回统一快照；retryable=True 表示限流/临时故障，框架会自动退避重试
        ...

    async def cancel(self, external_id: str) -> None:
        ...


Driver = VolcengineProvider
```

**② 改配置**

```dotenv
AI_PROVIDERS=echo,volcengine
```

如果这个供应商需要部署参数（base_url / api_key / 区域），**在它自己的文件里声明配置**，
不要加进 `core/config.py`：

```python
# src/infra/ai/volcengine.py
from typing import ClassVar
from infra.driver_config import DriverSettings, driver_config

class VolcengineSettings(DriverSettings):
    model_config = driver_config("VOLCENGINE")   # 前缀 → VOLCENGINE_API_KEY
    # 只有「调用供应商的那次提交」才声明；下载/后处理超时不算
    submit_budget_for: ClassVar[frozenset[str]] = frozenset({"video"})
    base_url: str = "https://ark.cn-beijing.volces.com"
    api_key: str = ""            # 留空，在使用处报错并提示配置项名
    timeout_sec: float = 30.0    # 声明了 submit_budget_for 就必须有它
```

约定：**环境变量前缀 = 驱动名大写 + `_`**，`.env.example` 里要列出来，
`tests/test_config_guard.py` 会检查「字段有没有人读」「示例有没有写全」「默认值有没有指向盘符」。
`core/config.py` 只放「所有驱动共有」的部署策略（用哪个驱动、并发多少）。

两个 `ClassVar` 是配置的**自描述**，消费方按声明筛选，不要遍历
`registered_driver_settings()` 再按字段名 `timeout_sec` 猜语义（R12-03）：

| 声明 | 谁来读 | 不声明的后果 |
|---|---|---|
| `submit_budget_for` | 模块巡检（如 `image.reconcile`）推导「多久算卡住」 | 不参与任何阈值推导；与生成无关的驱动不会把阈值拖大 |
| `prod_required_fields` | `app.py` 启动期 `ensure_prod_driver_settings()` | 允许走代码兜底；**安全相关的一定要声明**，例如 `IMAGE_ASSET_ALLOWED_HOSTS` |

`registered_submit_budgets()` 刻意不吞构造异常：某个驱动声明了预算却没配好必填项，
静默 `continue` 会让阈值回落到 90 秒，正好重新打开「巡检误伤在途任务」的窗口。
一条声明都没有时巡检**跳过本轮并打 error**，绝不拿猜出来的阈值去判死任务。

**③ 完成**

- `app models` 会列出新模型
- 前端模型选择器、时长与比例下拉会自动出现新模型（读 `GET /api/v1/models`，见第 6 节）
- 业务代码、`infra/__init__.py`、`core/config.py` 全部不动（配置写在供应商自己的文件里）

> `core/config.py` 里的 `ai_providers` 是普通 `str` 而不是 `Literal`，
> 就是为了让"加供应商"不需要改核心文件。

**同步供应商怎么写**

多数图片厂商是同步的：一个 POST 等十几秒，直接返回图片地址。这种 provider 让
`submit` 返回 `GenerationProgress` 而不是任务 ID，框架拿到终态就不再轮询
（见 `infra/ai/agnes.py`）：

```python
async def submit(self, request: GenerationRequest) -> GenerationProgress:
    body = await post(...)                      # 同步出结果
    return GenerationProgress(
        external_id=body["task_id"],            # 仍要落库，便于对账
        state=GenerationState.SUCCEEDED,
        progress=100,
        output_url=body["data"][0]["url"],
    )
```

代价是这次调用会占住一个 worker 槽位直到出图（Agnes 实测约 13 秒），
`WORKER_CONCURRENCY` 默认 2 时约 7 张/分钟。图片任务短，通常够用；
量上来再考虑业务侧改成「先落记录、后台跑」。

> **异常不要自己包装超时**：`runner` 按 httpx 的异常类别判断「供应商有没有可能
> 已经受理」。把 `httpx.ReadTimeout` 包成 `UpstreamError` 会丢掉这个语义，
> 让一次可能已经计费的提交被当成「确定没送达」而重试。

---

## 4. 案例：新增一种生成能力（音乐 / 配音 / 3D）

```bash
app new-module music --label 音乐 --perm music --slot 4 --generation
```

框架负责：提交、轮询、重试、超时、幂等、事件发布。
你要补的只有**表与 `GenerationRecord` 的映射**：

```python
# music/pipeline.py
class MusicStore:
    async def load(self, record_id) -> GenerationRecord | None: ...
    async def mark_submitting(self, record_id) -> None: ...        # 调供应商前的「提交意图」
    async def clear_submit_attempt(self, record_id) -> None: ...   # 「确定没送达」时清掉
    async def mark_submitted(self, record_id, external_id) -> None: ...
    #  ↑ 提交成功时**必须**把 submit_attempts 复位为 0（见下方口径说明）
    async def mark_progress(self, record_id, progress) -> None: ...
    async def mark_succeeded(self, record_id, *, output_url, cover_url, progress=100) -> None: ...
    async def mark_failed(self, record_id, error) -> None: ...
```

> `mark_submitting` / `clear_submit_attempt` 不是可选项：它们解决「供应商已受理、
> 但结果还没落库」的窗口。没有这两个方法，进程崩在这个窗口里时，重启只能看到
> 「没有 external_id」，就会再提交一次——**两份账单**。实现方式就是给表加一列
> `submit_attempts`（`video_tasks` 可参考）。

> 占位实现请抛 `NotImplementedFeature`（501），**不要抛 `NotImplementedError`**：
> 后者不是 `AppError`，会被 `_possibly_delivered` 归到「可能已送达」，
> 于是「一次都没提交」的任务显示「为避免重复计费请核对」，把人引向查账单。
> 框架现在把 `mark_submitting` 放在调用供应商的 `try` 之外，即使写错也只会报
> 「提交准备失败」，不会污染计费口径——但占位该抛什么仍然要写对。

`--generation` 还会在模块 `__init__.py` 里插一行 `from modules.music import pipeline`——
流水线靠「import 副作用」注册。别删它：删了代码看起来一切正常，
但 `dispatch("music", ...)` 会在运行时抛「流水线未注册」。

然后在创建记录的 service 里加一行：

```python
await get_runner(self.queue).dispatch("music", record.id)
```

**入队本身也可能失败**（队列没起、Redis 断连）。不处理的话记录会永远停在 `queued`，
用户只看到「排队中」且没有重试入口。照 `task/service.py::_dispatch` 的写法，
`dispatch` 外面套一层 `try`，失败就把记录置为 failed 并抛 502。

之后就自动具备：

| 能力 | 行为 |
|---|---|
| 幂等 | 同一记录已有 `external_id` 不会重复提交（避免重复计费） |
| 提交未知态 | 有提交意图但无 `external_id` → 置失败等人核对，**绝不自动重提** |
| 重试分类 | **确定没送达**（4xx 拒绝、根本连不上）→ 清标记按 2s、4s 退避重试（共 3 次尝试）；**可能已送达**（读/写超时、连接中途断开）→ 保留标记直接置为「结果未知」，不赌、不重提 |
| 轮询健壮性 | 单次轮询里的 DB 异常只算「这一跳失败」，链路继续，不会永久 running |
| 不占线程 | 轮询用**延迟投递**，handler 内没有 `sleep` |
| 超时 | 超过 `GENERATION_MAX_POLLS` 自动置为失败 |
| 事件 | 发出 `generation.succeeded` 等事件，供通知/计费订阅 |

两个口径说明，免得接手的人以为是 bug：

- **轮询预算按进程计**：`GENERATION_MAX_POLLS` 是「本次进程生命周期」内的计数，
  重启归零。反复重启的极端情况下单条任务可能超过 `generation_timeout_sec`
  仍在轮询；要按墙钟算就得持久化计数（当前取舍：重启是异常事件，供应商侧有自己的超时兜底）。
- **「结果未知」不是死路**：任务置为该失败后，用户点重试即可重新提交——
  `retry_task` 与 `mark_submitted` 都会把 `submit_attempts` 复位。
  所以 `mark_submitted` 的实现**必须**复位这个标记（见参考实现），
  否则提交成功后残留的标记会把之后每一次失败的重试都误杀成「结果未知」。
  **但「用户点重试」的前提是模块真的提供了 retry 入口**：R11-02 里新加的
  `image` 一个都没接，记录被判死后永久死锁。见 §5.6 与两条矩阵守卫。

除七个 Store 方法外，新模块还要接两件「平时用不上」的事：声明
`recovery_kinds`（或自己的 `startup`）与提供重试出口。两者都有 CI 守卫。

参考实现：`modules/task/pipeline.py`（约 140 行）、`modules/image/pipeline.py`。

---

## 5. 横向功能：不要 import 别人的模块

要做"生成完成发企业微信 / 写审计日志 / 扣额度"，**不要**去改 `task` 模块，也不要 import 它的 service。

订阅事件即可（放在你自己的模块里，或 `modules/notify/`）：

```python
from core.events import get_event_bus
from generation.events import EVT_GENERATION_SUCCEEDED

@get_event_bus().subscribe(EVT_GENERATION_SUCCEEDED)
async def notify_on_success(event) -> None:
    await send_im(
        owner=event.payload["owner_id"],
        text=f"你的作品已生成：{event.payload['output_url']}",
    )
```

处理器异常会被总线捕获并记日志，**不会**影响生成流程（通知失败不该让任务回滚）。

#### 投递可靠性：需要「一定会发出去」时写 outbox

`get_event_bus().publish()` 是纯进程内的：它在业务 `commit()` **之后**执行，中间那个
窗口一旦崩掉，业务成功、事件永久消失，日志里一句异常都没有。这类链路（计费、
通知、审计）改用 outbox：

```python
from db.outbox import record

async with factory() as session:
    task.status = "failed"
    await record(session, EVT_GENERATION_FAILED, record_id=task.id, owner_id=task.author_id)
    await session.commit()      # 事件与业务行同一个事务：要么都在，要么都不在
```

之后 `core.outbox` 周期任务（默认 5 秒）把未投递的行发出去；进程在 commit 之后
崩溃也不会丢，只会晚几秒。**语义是至少一次**，所以订阅方必须幂等
（按事件 id 去重，或用 `INSERT ... ON CONFLICT DO NOTHING` 写自己的幂等表）。
要做到「恰好一次」需要分布式事务，本骨架不承诺做不到的事。

选择标准很简单：

| 场景 | 用法 |
|---|---|
| 「顺便发一下」的通知、埋点 | `publish()`，丢了不心疼 |
| 计费、扣额度、对外承诺、审计 | `record()`，丢了要能查出来 |


### 5.1 跨模块**查询**：用注入口，不要 import

事件是「广播」，覆盖不了「我要问一个具体问题」——比如后台任务要问
「这个任务发起人还被启用着吗」。答案在 `modules/identity` 手里，而架构测试禁止 import。

那就开一个**注入口**（port）。它不是临时文件，是有一致形状的扩展点：

```python
# api/xxx.py —— 消费方只认协议，核心永远不认识具体模块
class XxxPort(Protocol):
    async def query(self, ...) -> ...: ...

_port: XxxPort | None = None

def register_xxx(port: XxxPort) -> None:   # 重复注册直接 RegistryError（权威来源只能有一个）
def reset_xxx() -> None:                   # 仅测试
def has_xxx() -> bool:                     # 供 app modules / 启动日志判断
async def ask_xxx(...) -> ...:             # **未注册时返回中性值，不许抛异常**
```

四条硬规则：

1. **未注册必须无害**。`ask_xxx()` 没有实现时返回值要能让调用方当「不知道」处理
   （`accounts.py` 的做法是返回 `None` + 调用方放行）。抛异常等于把可选依赖变成必选依赖。
2. **必须登记进 `api/ports.py` 的 `PORT_CATALOG`**：写清谁该实现它、没接上是什么行为。
   不登记 `test_every_port_is_catalogued` 会红。
3. **实现方在 import 期注册**（模块的 `__init__.py` 里），不要等请求来了才注册。
4. **查询逻辑薄**。注入口只转调，「三态语义」这类规则写在实现方，别在口子上分叉。

`app modules` 会列出每个注入口是否已接上，启动日志也会为未接上的留一行 INFO。
**不是所有跨模块需求都该做注入口**：纯数据查询把模型下移到 `db/` 更诚实；
只有一个调用点、且永远只可能有一个实现的，直接传参。

### 5.2 换基础设施实现：加文件 + 改一个 `*_DRIVER`

`infra/` 下每个可替换实现都是一个 kind，约定 `infra/<kind>/<name>.py` 暴露 `Driver`：

| kind | 配置项 | 现有实现 |
|---|---|---|
| `storage` | `STORAGE_DRIVER` | `local` |
| `queue` | `QUEUE_DRIVER` | `memory`（开发）/ `db`（`queued_jobs`，重启不丢） |
| `cache` | `CACHE_DRIVER` | `memory`（LRU + TTL） |
| `ai` | `AI_PROVIDERS`（可多选） | `echo` / `agnes` / `libtv` |
| `archive` | `ARCHIVE_DRIVER` | `local`（本地 / 共享盘） |

新增一个只需两步：写 `infra/<kind>/<name>.py`（`Driver = 你的类`），改 `.env`。
驱动自己的参数（base_url、api_key、根目录、连接数）放它自己的 `XxxSettings`
（`infra/driver_config.py`，前缀 = 驱动名大写），**不要**加回 `core/config.py`。
配置巡检会自动发现它，不需要改测试（`import_all_driver_settings` 扫的是文件系统，
不是手写清单——手写清单曾经让 `S3_ARCHIVE_*` 漏检半年）。

两条纪律：

- **只经工厂拿实现**。`get_storage()` / `get_queue()` / `get_cache()` /
  `get_archive_storage()` / `get_model_router()`；直接 `new` 具体实现会让
  「换驱动只改配置」这句话失真，`test_archive_storage_only_reached_through_factory`
  就是为这条加的。
- **协议只承诺能做到的事**。`ArchiveStorage` 用 **key** 表达位置（`read/write/
  copy/list_keys`），远端驱动把「取值」表达成 `materialize()`（临时本地路径）与
  `download_url()`（预签名地址），不假装自己有永久本地路径。新增驱动时如果发现
  某个协议方法只能靠抛异常实现，说明协议错了，改协议，别在驱动里绕。

### 5.2.1 调第三方：用 `get_http()`，别自己 new client

`infra/http.py` 提供进程级共享 `httpx.AsyncClient`（`get_http()`），生命周期挂在
lifespan 上。自己 `async with httpx.AsyncClient(...)` 会让三件事对不齐：超时
（各写各的）、连接池（keep-alive 用不上）、SSRF 校验（只有部分路径做了）。

- 下载产物用 `fetch_bytes(url, max_bytes=...)`：逐跳校验重定向、流式限大小、只对
  429/5xx 重试；
- 调 JSON 接口用 `get_http().post(...)`，重试自己判断——POST 重复提交等于重复计费；
- 安全校验用 `validate_public_url()`，生产必须配 `IMAGE_ASSET_ALLOWED_HOSTS`
  （黑名单兜底不解析 DNS，拦不住指向内网的域名）。

### 5.2.2 缓存：只放「丢了也不会错」的数据

`get_cache()` 返回 `Cache` 协议（`get/set/delete/exists/incr/clear`），默认进程内。
放之前先问一句：**整块缓存丢失后业务是否仍然正确？**

- 可以放：模型清单、热点只读聚合、限流计数；
- 不许放：会话、令牌、余额、任务状态、归档索引——多实例下「A 登录 B 不认」，
  重启一台机器等于全体掉线。

缓存没有 `get_or_set`，回源策略（要不要加锁、失败是否返回旧值）由调用方写清楚；
防击穿需要 singleflight，那是消费方的事，不是存储语义。

### 5.2.3 限流：`rate_limit()`，计数落缓存

```python
from api.deps import rate_limit

@router.post("")
async def create(
    ...,
    _: None = Depends(rate_limit("task.create", limit=settings.rate_limit_default_per_min)),
): ...
```

- `scope` 是**动作**（`task.create`），不是 URL：换个路径前缀不该重置额度；
- 计数主体是当前登录用户；`cost` 让「一次请求算几次」可配（上传大文件常见 5 次）；
- 超限抛 429，`details.retryAfter` 给前端倒计时；
- 计数在 `CACHE_DRIVER` 里。**memory 驱动下多实例各算各的**，要精确就换共享缓存；
- 缓存故障时**放行**（并打一条限流日志）。限流是保护措施不是鉴权：拦错的代价是
  全站不可用，放过的代价是可能被打一下——和 `core/ports/assets.py` 的 fail-closed
  是两套判断标准，别混用。

刻意**没有**全局中间件兜底：全局额度容易把健康检查、静态资源、预检请求一起限掉，
而「哪个接口贵」只有模块自己知道。

### 5.2.4 队列：`memory → db → redis` 是同一条升级路径

| 阶段 | 配置 | 得到什么 | 代价 |
|---|---|---|---|
| 开发 | `QUEUE_DRIVER=memory` | 零依赖，跑通链路 | 重启丢在途任务，跨实例不可见 |
| 单机/小规模生产 | `QUEUE_DRIVER=db` | job 落 `queued_jobs`，重启不丢；租约到期别人能接手崩溃任务；死信留档 | 每秒一次轮询查询（见 `QUEUE_POLL_INTERVAL_SEC`），吞吐不高 |
| 大规模 | 新增 `infra/queue/redis.py` | 高吞吐、跨实例即时唤醒 | 多一个中间件、多一个故障域 |

从 db 到 redis **只需要一个文件**：`infra/queue/redis.py` 里 `Driver = RedisJobQueue`，
实现 `JobQueue` 的 6 个方法（映射表见 `infra/queue/base.py` 的模块文档），
连接参数放它自己的 `RedisQueueSettings`（`REDIS_*` 前缀，`infra/driver_config.py`），
然后改 `QUEUE_DRIVER=redis`。业务代码、协议、测试都不用动。

两条容易踩的：

- **`attempts` 的语义是「已失败次数」**，不是「已执行次数」。handler 里 `job.attempts + 1`
  就是第几次尝试；新驱动自己另算一套，日志与告警会全部错位。
- **领取必须原子且可接管**。`db` 驱动用「条件 UPDATE + 租约」，Redis 用
  `ZRANGEBYSCORE` + `ZREM`（或 `BRPOPLPUSH` + processing 列表）。只 `ZREM` 不留
  processing 列表，进程一崩那条 job 就**永远消失**——这比报错难查得多。

### 5.2.5 可观测：指标、trace、功能开关

三件事各管一段，别混用：

| 问题 | 用什么 | 入口 |
|---|---|---|
| 现在积压多少 / 成功率多少 | 指标 | `core.metrics.METRICS` → `GET /metrics` |
| 这一条请求为什么失败 | 结构化日志 + request id | 自动带；异步段靠 `trace_id` 列恢复 |
| 这个功能现在该不该开 | 功能开关 | `core.flags.is_on("领域.动作")` |

#### 加指标

在**导入期**声明一次（同名同标签会返回同一实例，但没必要每请求都走注册表）：

```python
from core.metrics import METRICS

REJECTED = METRICS.counter("asset_uploads_rejected_total", "上传被拒次数", ("reason",))

async def upload(...) -> None:
    REJECTED.inc(reason="quota")
```

四条纪律：

1. **标签只用低基数字段**（method / route / status / outcome / name）。
   把用户 id、资产 id、URL 当标签，时间序列会随流量线性增长——应用侧只看得到
   「内存慢涨」，Prometheus 侧表现为查询超时，两边都难定位。`max_series`
   （默认 500）是兜底：撞上会丢**新**序列并告警，保留既有曲线，不是许可证。
2. **命名按 Prometheus 约定**：`_total` 计数、`_seconds` 耗时、`_bytes` 字节。
   单位写进名字，面板上才不用猜。
3. **抓取时才成立的值用 gauge**，并在 `api/instrumentation.py` 的
   `refresh_runtime_gauges()` 里取真实来源（队列表、outbox 表）。靠打点维护的
   积压量在「刚重启、还没跑过任务」时是错的。
4. **多进程各暴露一份**，聚合交给 Prometheus（`sum by (le)`）。本骨架不做
   跨进程聚合，也不落盘。

`/metrics` 的访问控制：`METRICS_ENABLED` 开关 + `METRICS_TOKEN`（Bearer）。
生产不配 token 时返回 403（`app doctor` 会提示）：路由模板、队列积压、驱动名
对内部人员是信息，对扫描器是地图。关闭时返回 404，不暴露端点存在性。

#### trace：异步之后还能接回原请求

`X-Request-Id` 在 contextvar 里，跨过队列/outbox 就断了。框架在**入队与
`outbox.record()` 时快照** `request_id`，在 **worker / 投递器执行时恢复**，
所以 handler 里的日志天然带着触发它的那个请求 id（`queued_jobs.trace_id` /
`outbox_events.trace_id` 两列，空串表示「非请求触发」）。

自己起一条链路时要成对使用：

```python
from core import trace

previous = trace.adopt(job_trace_id)   # 进入某条链路
try:
    ...
finally:
    trace.release(previous)            # 必须还原，否则下一条任务会继承这条的 id
```

#### 功能开关

```python
from core.flags import is_on

if is_on("upload.multipart"):
    ...
```

配置是 `FEATURE_FLAGS`（JSON 或逗号列表）。开关**只决定分支**，不决定装配：
`WORKER_ENABLED`、`METRICS_ENABLED` 这类仍留在 `Settings` 里，因为它们影响
「有没有这个能力」，而不只是「走哪条路」。

刻意不做两件事：**不做灰度**（按用户/比例分流属于网关或实验平台，写进领域逻辑
既污染代码也没法运营）、**不做热更新**（值在启动时读入，否则「这个请求到底走了
哪条分支」就没有日志之外的证据）。`app doctor` 会列出全部已声明开关，
「系统里有哪些开关」不需要靠 grep 回答。

### 5.3 定期发生的事：`@periodic`，别写进 `app.py`

请求驱动和队列驱动都表达不了「没人触发、但每 N 秒要发生一次」。典型场景：

- 进程内队列重启即丢，`queued` 的记录会永远挂在页面上；
- 孤儿文件按天数清理、「每日重置计数」这类运营动作。

```python
# modules/<你的模块>/maintenance.py
from core.scheduler import periodic

@periodic("image.reconcile", every=300, immediate=True)
async def reconcile() -> None:
    ...  # 自己开会话：async with get_session_factory()() as session: ...
```

四条硬规则：

1. **名字 = `<模块>.<动作>`**，`app modules` 按前缀分组，`test_periodic_jobs_are_namespaced_by_module` 会红；
2. **handler 自己开会话**，不要在闭包里持有 session；
3. **必须幂等**：多副本部署时每个进程各跑一份。确实「全集群只能跑一次」的
   （不可逆删除、对外发券）标 `leader_only=True`，调度器会向 `LeaderElector`
   抢集群租约，抢不到就跳过这一轮；租约策略由 `LEADER_ELECTOR` 选（默认 `local`，
   多实例用 `backend`，它是用归档存储的条件写实现的，**不需要 Redis**）；
4. **`every` 是最小间隔**，不是精确定时：某个 handler 跑久了会推后别人的下一轮。

`SCHEDULER_ENABLED=false` 可以整体关掉。什么时候用 job handler、什么时候用周期任务：
**有明确终点、由事件触发 → job；没有终点、到点就该跑 → periodic。**

---

## 5.4 模块自带的另外三样东西：种子 / 留痕 / 产物落盘

七维对账（`docs/EXTENSIBILITY_GAPS.md`）里剩下的三个维度，恰好都是「每个模块都要，
但以前没地方放」的东西。约定如下，都是可选项，不写就是没有这个行为。

**① 演示数据：`ModuleSpec.seed`**

```python
async def seed(session: AsyncSession) -> int:      # 自己 commit，返回行数
    session.add_all([...]); await session.commit(); return len(rows)

module = ModuleSpec(name="image", router=router, permissions=SPECS, seed=seed)
```

`app seed` 会遍历所有模块的 `seed` 钩子，**不需要再改 `src/seed.py`**。
演示数据必须是**终态**：`queued` 会被周期任务当成「卡住的活」重新投递到真实供应商。

**② 操作留痕：`core.audit.record`**

```python
await audit.record("image.generate", actor_id=actor.id,
                   target_type="image_job", target_id=job.id,
                   detail={"model": spec.id, "count": payload.count})
```

- 写失败**不会**影响业务（内部吞异常只记日志）—— 为了一条日志让用户报错不值得；
- `detail` 不许放 api_key / 令牌；
- 与身份模块的 `login_events` 是两张表：那是安全审计，这是操作留痕。

**③ 产物落盘：`pipeline.persist_output`（可选方法）**

```python
async def persist_output(self, record_id: str, url: str) -> str:
    ...  # 下载 → get_storage().put(...) → 返回自己的地址
```

供应商 URL 多是**签名地址**，过期后历史页全是裂图。实现这个方法，
`generation.runner` 在落结果前会先搬一次；不实现就存原 URL（视频直链长期有效的场景）。
**下载失败不会把成功的生成改成失败**，只退回原 URL 并记一条 warning。

---

## 5.5 `param_schema` 是契约，不是注释

`ModelSpec.param_schema` 有两个消费者，别只当它是给前端渲染表单的元数据：

1. **前端**拿它渲染下拉与输入范围（`GET /models`）；
2. **受理方**（`ImageService._validate_params` 这一类）拿它在**建记录之前**校验入参。

所以它必须等于供应商的**真实能力**。写宽了不是「兼容性更好」，是承诺一个做不到的事：
`agnes` 的 `count` 曾经声明 `max: 4`，供应商只接受 `n=1`，于是「选 4 张」先 201 受理、
再异步失败——用户看到的是转圈，失败原因躺在 `errorMessage` 里没人渲染。

三条硬规则：

- **声明 = 实测**。新接供应商时真的传一次越界值，看它是 400 还是默默改小；
  拿不准就声明得**保守**（宁可少给前端一个选项）。
- **字面量格式保持一致**。尺寸统一半角 `x`（`1024x1536`）；`echo.py` 曾经用全角 `×`
  而 `agnes.py` 用半角，同一套前端不可能同时满足两边。
- **校验放在受理时**，不要指望供应商兜底：异步失败对用户是「生成中」，
  对排查是「日志里有、页面上没有」。报错要把可选值一起给出来。

---

## 5.6 出事才用得上的插槽：启动恢复、重试出口、落盘校验

R11 的两条 🔴 是同一个形状：新模块接齐了「让它跑起来」的插槽，
「出事后能恢复」的一个没接，而 292 个用例、ruff、mypy、契约检查全绿。
现在这两件事都变成**必须写下来的声明**，并由架构守卫兜底。

**① 启动恢复：`ModuleSpec.recovery_kinds`**

```python
# 新模块：交给框架统一恢复，不要写 startup
module = ModuleSpec(name="image", router=router, ..., recovery_kinds=(KIND_IMAGE,))

# 老模块：保留自己的恢复器，仍要声明负责的 kind；有 startup 时框架跳过
module = ModuleSpec(name="task", ..., startup=_startup, recovery_kinds=(KIND_VIDEO,))
```

`app.py` 在 lifespan 里调 `generation/recovery.py::recover_declared()`，
按 kind 捞未终态记录并分成四类：续轮询 / 同步供应商孤儿 / 提交结果未知 /
建了记录没进队列。框架只认 pipeline 上的可选钩子
`list_unfinished(session, limit)`（SQL 仍由 Store 写），不认识任何 ORM 实体。

**供应商形态要写进 provider，也要写进记录**（R12-05）：

- `supports_poll` **缺省按 False 处理**。异步供应商必须显式写 `True`
  （`echo` / `libtv` 已写），同步供应商写 `False` 或干脆不写。下一个同步
  供应商忘了声明也不会被 `resume()` 到恒返回 FAILED 的 `poll()` 上判死。
- `mark_submitted` 把 `"sync"` / `"async"` 快照落到记录（`submit_mode`）。
  恢复分类**优先读快照**，不拿当前模型目录重新解释历史：供应商下线、
  换配置、测试环境只加载 echo 都会让后者翻转。老记录无快照时严格
  `resolve()`，解析不到按不支持处理，绝不走 `provider_for()` 的单供应商兜底。
- 同步供应商留下的 `running` 孤儿置为 `failed`，但**保留 `external_id`**，
  文案明确提示「可能已产生费用，请凭任务号核对」，而不是简单说「请重试」。
- 模块同时声明 `startup` 与 `recovery_kinds` 时框架跳过并打 warning：
  这些 kind 由模块自己的恢复器负责，别以为框架接管了（R12-07）。

**② 重试出口：`POST /.../{id}/retry`**

`SUBMIT_UNKNOWN_MESSAGE` 写着「请核对后手动重试」。runner 把结果未知的记录
判成 `failed` 之后，`handle_submit` 的终态早退让它再也动不了——**没有 retry
接口，这句文案就是空头支票**。入口必须清掉 `submit_attempts` / `external_id` /
`error_message` / `finished_at`，改回 `queued` 再 dispatch
（参考 `ImageService.retry` 与 `TaskService.retry_task`）。返回值别带 prompt：
`retry:any` 不等于 `read:all`。

**两种 `failed` 不能共用一个出口**（R12-01）：

| 形状 | 含义 | 重试 |
|---|---|---|
| `attempts>0`、无 `external_id` | 没确认受理 | 安全，不带参数直接点 |
| 有 `external_id` | 供应商已受理、可能已扣费 | 默认 409；带 `?confirmCharged=true` 才放行 |

清 `external_id` **之前**先把旧值搬进 `previous_external_id`，并写进
`audit.record("image.retry")` 的 detail。重试一次就少一份对账凭证，
比第二次计费更难事后解释。

`tests/test_architecture.py` 有两条矩阵守卫：每个 pipeline kind 都必须有人声明
负责恢复，且归属模块必须有重试出口。另有
`tests/test_generation_state_machine.py` 把「恢复 → retry → handle_submit」
串成一张表——组件级测试各自绿，不代表交接处没洞，R12-01 正是这样漏掉的。

**③ 产物落盘拿到的是外部输入**

`persist_output` 的 URL 来自**供应商响应体**，而 `AGNES_BASE_URL` 本身可配。
host 白名单、每一跳重定向重新校验、流式字节上限、content-type 白名单与文件
魔数确认缺一不可；`modules/image/pipeline.py` 是参考实现，
`tests/test_image_persist.py` 用真实 `httpx.MockTransport` 覆盖这些分支。

**白名单默认不开时兜底只挡非公网 IP 字面量，且不解析 DNS**（域名解析到内网
拦不住）。所以 `ImageAssetSettings.prod_required_fields = ("allowed_hosts",)`：
生产启动期 `ensure_prod_driver_settings()` 直接报错，部署必须配
`IMAGE_ASSET_ALLOWED_HOSTS`（Agnes 实测 CDN 为 `cos-platform-outputs.agnes-ai.cn`）。
`tests/test_image_policy.py` 把这条兜底的真实边界也钉住了，别把注释当实现（R12-04）。

**④ 巡检也要认「已受理」**（R12-06）

`image.reconcile` 捞 `queued` 与 `running`，但一律排除有 `external_id` 的记录：
供应商侧任务已在跑，重投就是第二份账单。恢复是启动期一次性、每批 100 条，
周期巡检补上这层兜底；`worker_enabled=false` 时启动恢复干脆跳过并告警，
否则入队的 job 没人消费，还会污染测试库（R12-09）。

---

## 5.7 三个「加模块时不显眼、加错了会丢数据」的扩展点

### ① 只要把 `assets.id` 存进了自己的表或文件，就必须声明引用来源

`core/ports/assets.py` 在每次删除前聚合所有来源。漏登记的表现是「删除成功、
引用方裂图」，**不可逆且没有告警**，所以这条不是可选约定：

```python
# modules/<name>/__init__.py
from core.ports.assets import AssetReferenceSource, bind_asset_reference_sources

SOURCES = (
    AssetReferenceSource(
        name="<模块>.<字段>",              # 全局唯一、稳定
        checker=MyAssetReferences(),       # 实现 async references(ids) -> {id: [描述]}
        location="modules/<name>/models.py:MyModel.field",  # 静态守卫拿它对账
    ),
)
bind_asset_reference_sources("<模块>", SOURCES)

module = ModuleSpec(..., asset_reference_sources=SOURCES)
```

三层保证：`ModuleSpec` 声明进框架期望集合 → `discover_modules()` 对账（声明了却没
注册就 FAIL）→ `tests/test_architecture.py` 用 AST 扫所有 `reference_*_ids` 字段
声明，没被任何来源 `location` 覆盖就 CI 红。运行时来源不齐抛 `Unavailable`(503)，
**不会**退化成「没人引用、可以删」。

### ② 写入 catalog JSON 前先问「DB 是不是唯一事实来源」

替换模板的同步 worker 读 `catalog/assets/*.json`，所以镜像必须存在，但它是
`modules/asset/mirror.py` 的**派生投影**：事务先 `commit`，之后才投影；镜像带
`stateVersion`，`@periodic("asset.mirror")` 与 `app rebuild-mirrors` 按 DB 幂等对账。
新增写路径不要自己拼 `catalog/assets/{id}.json`，也不要把「镜像写失败」当成
整次业务失败——DB 已提交时抛 500 只会让用户重试出重复记录。

### ③ 不可逆的周期任务标 `leader_only`

```python
@periodic("asset.gc", every=86400, leader_only=True)
```

`Scheduler` 会向 `LEADER_ELECTOR` 选出的策略抢租约。`local`（单实例，默认）
恒通过；`backend` 用 `ArchiveStorage.create()` 的条件写抢带 TTL 的租约，
全集群只有一个实例执行。幂等任务（例如镜像对账）**不要**标，多跑一份结果相同。
新增介质后端时，两条 S3 通路共用 `infra/blob.py` 的 `s3_client()`，别再各建一份
boto3 client；`S3_BUCKET_MODE=shared` 下用 `archive/` 前缀隔离。

---

## 6. 模型目录：已经就绪，别重复实现

`GET /api/v1/models?capability=video` 已经由 `modules/catalog` 提供（权限点 `catalog:read`），
返回结构是分页包（`items` + `total`），不是裸数组：

```json
{ "code": 0, "message": "OK",
  "data": { "items": [
      { "id": "seedance-2.5", "label": "Seedance 2.5", "capability": "video",
        "provider": "echo", "description": "通用短视频生成，支持首尾帧参考",
        "isDefault": true,
        "paramSchema": {
          "durationSec": { "type": "int", "min": 5, "max": 12, "default": 5 },
          "aspectRatio": { "type": "enum", "options": ["9:16", "16:9", "1:1"], "default": "9:16" }
        } }
    ], "total": 1 } }
```

前端 `entities/model/` 已经在消费它（`useModels` / `durationOptionsFor` / `aspectOptionsFor`），
所以**接新厂商、新模型，前端一行都不用改**：后端 `ModelSpec` 多一条，
选择器、时长、比例自动跟着变。

要给模型加一个可调参数，只需在 provider 的 `param_schema` 里加一个字段；
校验读的是同一份 schema（`modules/task/params.py` 的 `resolve_*`），
非法值当场 400 并回可用值——**不要在 service 里再抄一份白名单**。

---

## 7. 错误码号段分配表

规则：`code = HTTP状态码 × 100 + slot × 10 + 序号`，`code // 100` 恒等于 HTTP 状态码。

| slot | 归属 | 说明 |
|---|---|---|
| 0 | 框架 | `core/exceptions.py` 的 `ErrorCode`，已占用 |
| 1 | catalog / 通用业务 | 模型目录等跨模块只读资源 |
| 2 | identity | 用户 / 角色 / 登录 |
| 3 | task | 任务 |
| 4 | music | 音乐（示例） |
| 5 | asset | 素材 |
| 6 | image | 生图 |
| 7 | template / storyboard / shot | 模板类 |
| 8 | notify / billing | 横向能力 |
| 9 | voice（示例）/ 预留 | 第 1 节的脚手架示例占用 |

新增模块时先在这张表里占一个空格，再写进 `app new-module --slot n`。

模块内这样用（**不需要改 `core/exceptions.py`**）：

```python
# modules/music/errors.py
from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("music", slot=4)


class TrackBusy(AppError):
    code = errors.code(409, 1)      # -> 40941
    message = "该音轨正在生成中"
    http_status = 409
```

两个限制（`ModuleErrors` 会当场报错，不是约定）：

- `seq` 只能取 1–9，因此**每个模块每个 HTTP 状态码最多 9 个错误码**，超了要换 slot。
- 同一个 `(状态码, seq)` 在一个模块内不能重复定义，否则 `RegistryError`。

---

## 8. 加完功能要更新这四份生成物

加功能几乎必然会动接口。后端只写了一遍，前端却要三样东西（还要一份权限清单），
所以这四份**由同一条命令、从同一份 `build_spec()` 生成**，内容不可能互相对不上：

| 文件 | 面向 | 内容 | 谁在用 |
|---|---|---|---|
| `api/docs/openapi.json` | 机器 | 完整 OpenAPI 3.1 | Apifox / Postman / IDE 插件直接导入 |
| `api/docs/API.md` | 人 | 约定、权限点分组、错误码总表、按模块的接口清单与字段表 | 前端对接、后端自查 |
| `studio/src/shared/api/schema.ts` | TypeScript | 全部请求 / 响应 / 枚举的 TS 类型 | 前端 `import type` 直接用 |
| `studio/src/shared/api/permissions.ts` | TypeScript | 权限点分组、权限码元组与 `PermissionCode` 类型 | 前端权限勾选、菜单与按钮判定 |

四份的路径是 `src/openapi_export.py` 里的 `DEFAULT_JSON` / `DEFAULT_MD` /
`DEFAULT_TS` / `DEFAULT_PERMS`，也可以用 `--out-json` / `--out-md` /
`--out-ts` / `--out-perms` 覆盖（比如往另一份前端仓库输出）。

> 命令要用项目 CLI：`cd api && app export-openapi`。
> `python -m app ...` 只会静默退出 0，看起来成功其实什么都没做。

```bash
cd api
app export-openapi          # 重新生成四份
app export-openapi --check  # 只比对不写盘：有差异就以非 0 退出（CI 用）
```

### 8.1 什么时候必须跑

只要下面任意一条发生变化，四份生成物里至少有一份会变：

| 改动 | 体现在哪里 |
|---|---|
| 新增 / 删除接口，改路径、改方法 | `openapi.json` 的 `paths`、`API.md` 的接口清单 |
| 改 tag（模块标签、接口分组） | `API.md` 的分组标题、`openapi.json` 的 `tags` |
| 改请求体 / 查询参数 / 响应模型 | `schema.ts` 的类型、`openapi.json` 的 `components`、`API.md` 的字段表 |
| 改字段名、类型、必填、默认值、**改 `description`** | `API.md` 的字段说明列、`schema.ts` 的注释 |
| 新权限点（`ModuleSpec.permissions`） | `API.md` 的权限点分组 |
| 新错误码（`errors.py`） | `API.md` 的错误码总表 |
| 新模块（被 `api/v1/router.py` 扫到） | 四份都会有变化 |
| 新增 / 删除权限点 | `permissions.ts` 的 `PERMISSION_CODES`、`API.md` 的权限点分组 |

**不用跑**的情况：只改 service 内部实现，没碰 `schema.py` / `router.py` / `permissions.py` / `errors.py`。
不确定就跑 `--check`，它不写盘。

### 8.2 两条硬约束

1. **不要手工改这四个文件。** 它们是生成物，手改的部分下次一跑就被覆盖；
   真想让文档变，去改 `schema.py` 的 `Field(description=...)`，那才是源头。
2. **忘了跑也会红，跑不掉。** `tests/test_openapi_contract.py` 里
   `test_openapi_json_is_up_to_date` / `test_api_markdown_is_up_to_date` /
   `test_typescript_schema_is_up_to_date` 会拿磁盘文件和 `build_spec()` 做字节比对，
   失败信息直接点名哪一份落后。所以「改完顺手跑一次」不是自觉，是测试逼着你跑。

> 为什么不用「前端自己从 `/openapi.json` 拉」：拉取需要跑起服务、还要处理跨仓库缓存，
> 而把文件提交进仓库，前端同事 `git pull` 就能看到最新契约，code review 里也能直接 diff 出
> 「这个接口的返回字段从 3 个变成 4 个」。生成的 markdown 里刻意不写生成时间，
> 否则 `--check` 每天都会误报「已过期」。

> 现状提醒：`schema.ts` 目前和手写的 `studio/src/entities/*/model/types.ts` 并存。
> **新代码一律用 `schema.ts`**，手写类型按实体逐个迁移过去，别再新增第二份定义。

> 职责（2026-11-07 起）：接口变动同步**这四份生成物**，前端以它们为准。
> 前后端由同一负责人维护，手写 `types.ts` / `*.api.ts` 里与 `schema.ts` 漂移的旧签名
> 不再是「交给别人的迁移债」——跑完 `export-openapi` 就要顺着 `schema.ts` 的 diff
> 把消费端一起改掉，并跑一次 `cd studio && npm run verify`。
> 历史评审报告里的「按分工未动」是当时的记录，不是现行约束。

---

## 9. 提交前检查清单

- [ ] 没有修改 `core/`、`api/`、`infra/`、`db/`、`generation/`（若要改，先确认是不是所有模块都受益）
- [ ] 新权限点在 `ModuleSpec.permissions` 里声明，权限码用 `资源:动作`
- [ ] 只要把 `assets.id` 存进了自己的表/文件，就在 `ModuleSpec.asset_reference_sources`
      声明来源并 `bind_asset_reference_sources()`；否则 `test_architecture` 的 AST
      守卫会红，删除也会 fail-closed（见 §5.7 ①）
- [ ] 新增不可逆周期任务标 `leader_only=True`；幂等任务不要标（见 §5.7 ③）
- [ ] 新表有 Alembic 迁移，且迁移基于最新 head 生成
- [ ] 没有 import 其他业务模块（`test_architecture.py` 会拦）
- [ ] `app modules` 能看到新模块，权限点数量符合预期
- [ ] `app export-openapi` 已重新跑过，四份生成物一起提交（见第 8 节）
- [ ] 契约有破坏性变更时前端消费点已同步，`npm run verify` 通过
- [ ] 改了安全约束（守卫 / 闸门 / 限流 / 字段白名单）时，**先数一遍"还有哪些入口做同一件事"**：
      改密的守卫只在停用方向写了？闸门只装在 `authenticate` 上？白名单只收了一个字段？
      这三次都是同一个模式——修一个入口，漏掉同类的另一个（R5-03 / R6-04 / R8-01 / R8-02）。
      数法：`grep -n "scrypt\|hash_password_async\|verify_password_async" src`（闸门）、
      `grep -n "_guard_target_not_above" src`（守卫）、`grep -n "setattr" src/modules/*/service.py`（白名单）
- [ ] 守卫 / 闸门的**位置和形状**跟同类方法对齐：闸门打头、守卫在闸门内。
      同一文件里一个方法这样写、另一个那样写，读代码的人只会学到错的那一种（R9-01）
- [ ] 守卫 / 闸门的用例，**目标账号别图省事用 `*`**。`*` 只会走「目标是超管」那一支，
      另一支（委派角色 × 普通账号）一个断言都没有——R9-02 的功能回归就是这样躲过测试的。
      判定有几档就写几条用例，而且「能改 / 不能改」必须是**有意选的**，不是被断言顺手固定下来的（R9-03）
- [ ] `pytest` 全绿（含架构约束测试）
- [ ] `ruff check .` 与 `mypy src` 干净、`app doctor` 无 FAIL

---

## 10. 什么时候**应该**改核心

有些需求确实是通用能力，这时候改核心是对的 —— 但要按"提升抽象"的方式改，而不是加 if：

| 需求 | 正确做法 |
|---|---|
| 需要一种新的事件语义（可靠投递） | 给 `EventBus` 换实现（接口不变），不动订阅方 |
| 需要新的基础设施类别（如短信） | 在 `infra/` 加一类 + `load_driver("sms", ...)`，不动工厂 |
| 需要新的跨模块共享枚举 | 上提到 `core/enums.py`，不要 import 别的模块 |
| 需要新的统一响应形态（如 SSE） | 在 `core/response.py` 加，不改现有接口 |
| 需要一个「框架反过来调用业务」的钩子 | 加**协议 + 注册表**（如 `api/authn.py` 的 `PrincipalResolver`），业务侧注册，核心不认识它 |

判断标准：**改完之后，下次加同类功能要不要再改一次？** 要 → 抽象不够；不要 → 这次改对了。

一个已经发生过的例子：接入账号体系时发现 `get_actor` 把「权限从哪来」写死成「信令牌」，
导致停用账号后令牌仍能用满 TTL、改角色要重登。当时的修法不是加 `if identity`，
而是把主体解析抽成 `api/authn.py` 的注入点——`get_actor` 从「有 1 个实现」变成
「有 0 或 1 个扩展」。判断标准在这里体现为：**再接 SSO / LDAP / API Key 时，不用再改 `api/`。**

