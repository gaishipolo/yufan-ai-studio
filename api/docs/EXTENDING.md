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
   （`ModuleSpec.jobs` 只是给 `app modules` 看的展示信息，**不会**注册 handler；
   handler 靠模块里的 `@register_handler` 装饰器。）

第 1 条由 `tests/test_architecture.py` 用 AST 静态检查强制执行；第 3 条的「可发现性」
（每个子包导出 `module`、路由非空、权限点已声明）也有测试兜住。
第 2 条**没有测试能拦**——工具判断不了「这算不算设计缺口」，靠 code review 和第 10 节的判断标准。

补充一条默认值：**新模块默认受权限保护**。`ModuleSpec.permissions` 为空时
`test_every_module_declares_permissions` 会失败，除非显式标 `public=True`
（只适用于健康检查这类必须匿名可访问的接口）。默认安全，想裸奔要写出来。

再补一条收尾动作：**动了接口就重跑 `app export-openapi`**，把 `docs/openapi.json`、
`docs/API.md`、`studio/src/shared/api/schema.ts` 三份生成物一起提交（详见第 8 节）。
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

**③ 完成**

- `app models` 会列出新模型
- 前端模型选择器、时长与比例下拉会自动出现新模型（读 `GET /api/v1/models`，见第 6 节）
- 业务代码、`infra/__init__.py`、`core/config.py` 全部不动

> `core/config.py` 里的 `ai_providers` 是普通 `str` 而不是 `Literal`，
> 就是为了让"加供应商"不需要改核心文件。

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

参考实现：`modules/task/pipeline.py`（约 140 行）。

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

> 已知边界：这是进程内尽力而为的事件，不重试、不持久化。
> 需要可靠投递（如计费）时，改成"写 outbox 表 + 独立消费者"，订阅方代码不变。

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

## 8. 加完功能要更新这三份文件

加功能几乎必然会动接口。后端只写了一遍，前端却要三样东西，所以这三份**由同一条命令、
从同一份 `build_spec()` 生成**，内容不可能互相对不上：

| 文件 | 面向 | 内容 | 谁在用 |
|---|---|---|---|
| `api/docs/openapi.json` | 机器 | 完整 OpenAPI 3.1 | Apifox / Postman / IDE 插件直接导入 |
| `api/docs/API.md` | 人 | 约定、权限点分组、错误码总表、按模块的接口清单与字段表 | 前端对接、后端自查 |
| `studio/src/shared/api/schema.ts` | TypeScript | 全部请求 / 响应 / 枚举的 TS 类型 | 前端 `import type` 直接用 |

三份文件的路径是 `src/openapi_export.py` 里的 `DEFAULT_JSON` / `DEFAULT_MD` / `DEFAULT_TS`，
也可以用 `--out-json` / `--out-md` / `--out-ts` 覆盖（比如往另一份前端仓库输出）。

```bash
cd api
app export-openapi          # 重新生成三份
app export-openapi --check  # 只比对不写盘：有差异就以非 0 退出（CI 用）
```

### 8.1 什么时候必须跑

只要下面任意一条发生变化，三份文件都会变：

| 改动 | 体现在哪里 |
|---|---|
| 新增 / 删除接口，改路径、改方法 | `openapi.json` 的 `paths`、`API.md` 的接口清单 |
| 改 tag（模块标签、接口分组） | `API.md` 的分组标题、`openapi.json` 的 `tags` |
| 改请求体 / 查询参数 / 响应模型 | `schema.ts` 的类型、`openapi.json` 的 `components`、`API.md` 的字段表 |
| 改字段名、类型、必填、默认值、**改 `description`** | `API.md` 的字段说明列、`schema.ts` 的注释 |
| 新权限点（`ModuleSpec.permissions`） | `API.md` 的权限点分组 |
| 新错误码（`errors.py`） | `API.md` 的错误码总表 |
| 新模块（被 `api/v1/router.py` 扫到） | 三份都多出一整个分组 |

**不用跑**的情况：只改 service 内部实现，没碰 `schema.py` / `router.py` / `permissions.py` / `errors.py`。
不确定就跑 `--check`，它不写盘。

### 8.2 两条硬约束

1. **不要手工改这三个文件。** 它们是生成物，手改的部分下次一跑就被覆盖；
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

> 分工：**接口变动只需要同步这三份文件**，前端以它们为准。后端不代改前端手写类型——
> 手写 `types.ts` / `*.api.ts` 里的旧签名是前端自己的迁移债，跑完 `export-openapi` 就意味着
> 「接口已经变了」，剩下的在 code review 里对着 `schema.ts` 的 diff 处理即可。

---

## 9. 提交前检查清单

- [ ] 没有修改 `core/`、`api/`、`infra/`、`db/`、`generation/`（若要改，先确认是不是所有模块都受益）
- [ ] 新权限点在 `ModuleSpec.permissions` 里声明，权限码用 `资源:动作`
- [ ] 新表有 Alembic 迁移，且迁移基于最新 head 生成
- [ ] 没有 import 其他业务模块（`test_architecture.py` 会拦）
- [ ] `app modules` 能看到新模块，权限点数量符合预期
- [ ] `app export-openapi` 已重新跑过，三份生成物一起提交（见第 8 节）
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
- [ ] `ruff check src tests` 干净

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

