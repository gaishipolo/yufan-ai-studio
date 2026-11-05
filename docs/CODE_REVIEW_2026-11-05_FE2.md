# 前端代码评审报告（第二轮 · studio/）

> 范围按前提收窄：**只审全局骨架与已接后端的扩展点**（入口 / 路由工厂 / 注册表 / 权限层 /
> API 契约与查询层 / 会话生命周期 / 构建产物）。纯展示页面、占位页、样式细节不在本轮范围内，
> 那些等接后端时随功能一起看。
>
> 评审时间：2026-11-05 17:0x。基线：`tsc -b` 静默、`eslint --max-warnings 0` 无告警、
> `vitest run` **201 passed / 29 files**、`vite build` 通过。
> 工作区状态：本轮前端改动**全部未提交**（`entities/user|role|session`、`features/*`、
> `modules/auth|user-access`、`pages/login|user-access` 仍是 untracked）。

---

## 一、结论

骨架比第一轮扎实了一个数量级。第一轮的两个 🔴（生产无身份来源、错误被吞）都真修好了，
而且修法是对的：身份不是"在某个页面里补一个 setSubject"，而是 `useLogin` 写 `signIn`、
`useMe` 回填 `setSubject`、401 由拦截器清场，三条链路各自只有一处真相；错误处理不是逐页
`try/catch`，而是 `ErrorState` + `errorHttpStatus(code // 100)` 的统一个出口，明确写了
"不按完整业务码分支"。这两条都是"以后新增页面不用再想一遍"的形状。

本轮 7 条新发现里，有 4 条是同一个模式的变体，而且这个模式在九轮后端评审里出现过三次：

> **一个不变量写在注释里，但实现只覆盖了其中一个入口。**

- 缓存必须在会话结束时清空 —— 只有手动登出清了（`use-sign-out.ts:25`），
  过期 / 401 / 跨标签页三条路径没清；
- 页面权限必须声明 —— 9 个模块只有 `user-access` 声明了；
- 注册期必须拦住指错路的配置 —— nav 拦了，cards 没拦。

另外三条独立：会话恢复存在**不可自愈的死锁**、**生产包带着整套 mock 与演示账号**、
**列表只取一页却显示总数**。

---

## 二、上轮复核

| 编号 | 上轮状态 | 本轮实测 |
|---|---|---|
| FE-01 🔴 生产无身份来源 | 已修 | ✅ 成立。`useLogin` → `signIn`（含 token/expiresIn/subject 一起落盘），`useMe` → `setSubject`，`setSubject` 全局 3 个调用点（store 定义 + 两处 hooks），没有散落 |
| FE-02 🔴 全部 query 错误被吞 | 已修 | ✅ 成立。6 个列表页接 `ErrorState`；`errorHttpStatus` 被 `ErrorState` 与 `login-feedback` 复用；任务页失败时显示「共 — 条」 |
| FE-03 🟡 `schema.ts` 零引用 | 未动 | ❌ 仍零引用。660 行，全仓唯一命中是文件头注释里的自我示例 |
| FE-04 🟡 权限骨架零使用 | 部分 | 🟡 仍 1/9：`user-access` 声明了 `identity:read`，其余 8 个模块 `permissions` 全空（见 FE2-04） |
| FE-05 及之后 | 未动 | ⏳ 不在本轮范围；`FE2-08` 收了一条（mock 目录已落后后端 2 项） |

补充说明：FE-01 修得比上轮建议更彻底——上轮只要求"有个地方写身份"，现在多了
`loading` 三态、localStorage 坏数据逐字段校验、`expiresAt` 本地到点登出、跨标签页同步、
登出原因 10 秒 TTL。这些都不是这次审查提出的，是实现时自己想到的。

---

## 三、🔴 FE2-01 会话恢复会永久卡死，且没有出口

**位置**：`shared/access/RequireAuth.tsx:26`、`app/SessionBootstrap.tsx:18`、
`entities/session/api/session.queries.ts:24-36`

**触发条件**（两个都满足即可，不需要第三个）：

1. `localStorage` 里有 `sf_token`，但 `sf_subject` 缺失或解析失败
   → `resolveInitialSession()` 返回 `status: 'loading'`；
2. 紧接着那一次 `/identity/me` 返回**非 401** 的失败（500、502、断网、超时）。

`useMe` 失败时什么都不做（`retry: false`），`status` 永远是 `loading`，
`RequireAuth` 就永远渲染 `<FullPageLoader label="正在恢复登录状态…" />`。
`handleUnauthorized` 只认 401，所以这条路径连"清场"都不会发生。

**实测**（临时用例：store 置 `status:'loading'` + `token:'stale-token'`，
mock `sessionApi.me` 抛错，渲染 `SessionBootstrap > RequireAuth`，等 300ms）：

```
[PROBE] { status: 'loading', token: 'stale-token',
          stillLoading: true, atLogin: false, protectedContent: false }
```

页面停在整页 loading，不跳登录页、不显示错误、不重试。**用户唯一的出路是手动清
localStorage**（普通用户不会）。

**为什么不是"极端情况"**：条件 1 恰好是**每次改 `Subject` 结构**时的默认结果——
`parseSubject` 校验失败一律当没有。也就是说，下一次给 `Subject` 加一个必填字段，
所有在线用户的快照全变成 `null`，此后任何一次后端抖动都会把这些人钉在 loading 页上。

**建议**：把"引导失败"当成一个需要表白的状态，而不是继续假装在加载。

```ts
// SessionBootstrap：失败且不是 401（401 已由拦截器清场）→ 交给可见的错误页
const me = useMe(Boolean(token));
if (me.isError && errorHttpStatus((me.error as ApiError).code) !== 401) {
  return <SessionRecovery onRetry={() => void me.refetch()} onSignOut={() => signOut('unauthorized')} />;
}
```

`SessionRecovery` 就是现有的 `ErrorState` + 两个按钮（重试 / 重新登录），不必新写组件。
顺手补一条用例：`/me` 失败时 `status` 必须离开 `loading`——这才是这条死锁的回归测试。

---

## 四、🟡 FE2-02 清缓存只做在"手动退出"一个入口

**位置**：`widgets/user-menu/use-sign-out.ts:25`（唯一一处 `queryClient.clear()`）

`use-sign-out.ts` 的注释把意图写得很清楚：

> 3. 最后清查询缓存，避免下个账号在首屏看到上一个账号的数据。

但会话有**四条**结束路径，只有第一条清了缓存：

| 路径 | 触发 | 清缓存 |
|---|---|---|
| `manual` | 用户点退出（`use-sign-out.ts`） | ✅ `queryClient.clear()` |
| `expired` | 到点自动登出（`use-session-watch.ts:37`） | ❌ |
| `unauthorized` | 任意接口 401（`shared/api/http.ts:50`） | ❌ |
| `external` | 另一个标签页退出（`use-session-watch.ts:61`） | ❌ |

后三条恰好是**被动发生**的，也就是用户不会预期到"该有人清缓存"的时刻。

**后果**：401 过期 → 回到登录页 → 同标签页换另一个账号登录 → `staleTime` 内命中的
旧缓存被直接当作新账号的数据渲染。按当前配置，窗口是：`images`/`roles`/`models` 30s
（`query-client.ts` 全局），`users` 10s，`tasks` 1s，`me` 5min（登录时被覆盖，无碍）。
30 秒的旧数据里包含上一个账号的生图历史与角色列表。

**建议**：不要让四条路径各自记得清缓存。把"结束会话"收成一个动作，
唯一的调用点统一做 `signOut + clear`：

```ts
// shared/access/end-session.ts
export function endSession(reason: SignOutReason) {
  useSessionStore.getState().signOut(reason);
  queryClient.clear();          // 手动退出也改成调这里，删掉 use-sign-out 里的重复
}
```

`http.ts`、`use-session-watch.ts`、`use-sign-out.ts` 全部改调它。
清缓存是幂等的，多调几次无害，所以这个改动没有顺序陷阱。

> 这条和第九轮后端 R9-02 是同一个形状：R8-02 把守卫加在"改资料"上，
> 忘了"重置口令 / 停用"这些同族入口。区别只是这里漏的是前端。

---

## 五、🟡 FE2-03 生产包里有整套 mock 和演示账号

`shared/api/http.ts:17` 的判断在构建期会被替换成常量，`if (false === 'true')` 整块被消除——
但 `adapter.ts:2` 的 `import './routes'` 是**副作用 import**，rollup 不能删它；
而 `mocks/routes/index.ts` 又用 `import.meta.glob(..., { eager: true })` 把
`mocks/routes/*.mock.ts` 与 `modules/*/mock.ts` 全部吞进来。结果：**mock 图整张进了入口包**。

**实测**（同一份代码，只把 `http.ts` 里那两行 mock 引用去掉再构建）：

```
with mock : dist/assets/index-*.js   94.38 kB │ gzip 33.53 kB
without   : dist/assets/index-*.js   72.57 kB │ gzip 25.22 kB
                                    -21.81 kB      -8.31 kB   （入口包 −23%）
```

产物里搜得到 `admin@12345` / `editor@12345`（`modules/auth/mock.ts` 的演示账号）。
这不是密钥泄露（口令本来就写在 `api/src/seed.py` 与文档里），但**生产包里不该有登录口令
字符串**——它会被任何一次静态扫描标红，也是"这套 mock 到底会不会在线上生效"这个疑问的来源。

**建议**：把 mock 变成真正可选的分包，别让主 bundle 静态引用它。

```ts
// http.ts
if (import.meta.env.VITE_USE_MOCK === 'true') {
  const { mockAdapter } = await import('@/mocks/adapter');   // 动态 import，构建期不链入
  http.defaults.adapter = mockAdapter;
}
```

拦截器改成 `async` 即可（axios 支持）。这样 mock 只在 `USE_MOCK=true` 时按需加载，
生产包里连文件名都不会出现。顺带把 `vite.config.ts` 的 `manualChunks` 补一条
`mock: ['@/mocks/adapter']`（只为 dev 分包，不影响 prod）。

---

## 六、🟡 FE2-04 权限骨架通了，但 9 个模块只有 1 个接线

`createRegistry` 收集权限、`router.tsx` 给每个 shell 路由套 `RequirePermission`、
`Sidebar` 按权限过滤、`<Can>` 管按钮——链路完整，`router.test.ts` 甚至断言了
"每个页面都被 `RequirePermission` 包住"。但那个断言只检查**包裹关系**，不检查
`permissions` 是否为空，而现实是：

```
src/modules/user-access/module.ts   permissions: ['identity:read']   ← 唯一一个
assets / auth / image / shots / storyboards / studio / task-center / templates / usage   全部未声明
```

`can(subject, [])` 返回 `true`，所以这 8 个模块的路由、侧栏入口、宫格卡片对**任何已登录用户**
全部放行。目前看不出问题（页面是展示态），但等 `生图`/`工作台` 接上提交接口，
viewer 会看到一排能点、点了 403 的按钮——第一轮 FE-04 说的就是这个，只是当时以为是"骨架没人用"。

**根因在扩展点**：`scripts/gen-module.mjs` 生成的是**注释掉**的权限声明——

```ts
// 需要门禁时打开下一行，并确认后端 / 登录接口会下发该权限点：
// permissions: ['${id}:read'],
```

"默认全开、记得打开"和"默认关闭、显式打开"在文档上只差一句，在半年后的代码里差 8 个模块。
`usage/module.ts` 里那条 `// permissions: ['usage:read'],` 就是这个约定的第一个受害者：
后端目录里**根本没有** `usage:read`（见 FE2-06），所以那行注释打开就会变成"永远进不去的页面"。

**建议**（两条都做，成本都很低）：

1. 脚手架生成**可用**的 `permissions: ['<id>:read']`，只在注释里写明"后端没有这个点就去掉"。
   默认关闭的代价是"新页面打不开"，会被立刻发现；默认全开的代价是"没人发现"。
2. `router.test.ts` 补一条：**除白名单外，每个 shell 路由的 `permissions.length > 0`**。
   白名单写成显式数组（`['/dashboard']` 之类），加模块时要先想一次"这页要不要门禁"。

---

## 七、🟡 FE2-05 宫格卡片不受注册期校验（nav 受）

`createRegistry` 的卖点是"配错了会立刻在启动时抛错，而不是运行时白屏"。这条对 nav 成立：

```ts
for (const nav of navItems) {                      // create-registry.ts:109
  if (!routeOwners.has(nav.path)) fail(`导航项「${nav.label}」(${nav.path}) 没有对应路由…`);
  if (barePaths.has(nav.path)) fail(`…指向独立页面…`);
}
```

对 cards 不成立：卡片只做 `normalizePath`，没有存在性校验，**也不检查父模块是否声明了对应路由**。
而首页宫格正是主入口（`studio` / `task-center` / `assets` / `templates` / `image` 五张卡片）。

**实测**（临时用例，给一个只有 `/demo` 路由的模块声明 `cards: [{ path: '/nope' }]`）：

```
[PROBE] 卡片指向 /nope 的注册结果： 通过，无报错
```

对比同一条用例里 nav 指向 `/nope`：`throw /导航项「不存在的页」(\/nope) 没有对应路由/`。

也就是说：**改了模块 id、删了路由、卡片没跟着改，构建、类型检查、201 条测试全绿，
用户点首页卡片才 404**。nav 那条检查证明作者知道该拦，只是漏了同族的另一处。

**建议**：把 nav 的循环扩成同时校验 cards（同一个 `routeOwners`）：

```ts
for (const card of sortedCards) {
  if (!routeOwners.has(card.path)) fail(`宫格卡片「${card.title}」(${card.path}) 没有对应路由`);
}
```

`matchPrefixes` 更宽松，可以不校验（它本来就允许指向子路径）。

---

## 八、🟡 FE2-06 权限点的单一来源在后端，前端既没校验也手抄了一份

后端已经是单一来源：`permission_catalog()` ← 各模块 `ModuleSpec.permissions`，
并通过 `GET /identity/permissions` 下发（`identity/router.py:209`）。
`identity:role:manage` 这类点的名字由后端定，前端只该消费。

前端现状是两个隐患：

1. **`declaredPermissions` 收集了但零使用**。`create-registry.ts:145` 把它塞进 `AppRegistry`，
   全仓唯一读者是 `create-registry.test.ts:69`。它能做的校验（"模块声明的每个权限点是否
   真实存在于后端目录"）没人做，于是拼错 `identity:roles:manage` 不会报错，
   只会让那个入口对所有人永久隐藏——fail-closed，但**静默**。
2. **mock 手抄了一份目录，已经落后 2 项**。

```
后端 permission_catalog()：21 项（内容生产 13 / 素材 3 / 组织管理 5）
mock PERMISSION_GROUPS ：19 项（注释也写着「19 个」）
缺：image:read:all、task:retry:any
```

两个都是 R8 之后新增的（`/images` 的跨用户开关、`task:retry:any`）。mock 缺失的直接后果：
mock 模式下角色编辑界面无法勾选这两项，而后端 `set_role_permissions` 是**整组替换**，
一旦有人在 mock 里保存一个真实环境带这两项的角色，它们会被当作"不在目录里"清掉。

**建议**：让校验跑在已有的生成物上，不要新增流程。

- `api/docs/openapi.json` 已经随接口一起生成；写一条前端测试，从它里面抽出
  `GET /identity/permissions` 的响应 schema 里能拿到的分组（或直接硬编码一份
  `PERMISSION_CODES` 常量并配一条"与 openapi 一致"的断言），再与
  `appRegistry.declaredPermissions` 求差集，差集非空就失败。
- mock 侧把 `PERMISSION_GROUPS` 换成从上述常量派生，注释里的"19 个"就不会再过期。

这条本质上和你们后端那套"三件套生成物 + `--check` 字节比对"是同一个思路：
**能自动比对的，不要靠注释里那句"与后端一致"。**

---

## 九、🟡 FE2-07 列表只取一页，但界面显示了总数

后端在 R8 给 `/images` 加了分页（`DEFAULT_PAGE_SIZE = 24`，`core/response.py:19`），
前端那一行没跟上：

```ts
// entities/image/api/image.api.ts:12
list: () => api.get<Paginated<ImageJob>>('/images'),     // 没有 page / pageSize
```

```tsx
// pages/image/ImagePage.tsx:91
{isError ? '共 — 张' : `共 ${data?.total ?? 0} 张`}      // total 走 count(*)：全部条数
…
{(data?.items ?? []).map(…)}                            // 但只渲染 24 条，且没有翻页控件
```

结果：第 25 条以后的记录在界面上**不存在**，而标题写着"共 60 张"。这不是"少显示几条"，
是**同一屏里两个数互相矛盾**，用户会当成丢数据报回来。

`TaskCenterPage.tsx:51` 是同一类问题的另一份：`pageSize: 48` 写死，`total` 照样显示，
超过 48 条同样无处可去（`total` 从 `TaskSearchRow` 显示）。

对比 `UsersSection`：它是唯一一处做对的——`page` 状态、`totalPages`、筛选变更回第一页，
分页参数进了 queryKey。

**建议**：

- 短期（改一行，今天可上）：`imageApi.list` 接受 `PageParams`，
  `ImagePage` 复用 `UsersSection` 的分页条（如果 `shared/ui` 里还没有，就先加一个最小 `Pager`，
  两个页面都要用）。
- 中期：`Paginated<T>` 的消费方统一走一个 `usePagedQuery`，把"页码状态 + 回第一页 + 拼参数
  进 key"这三件事收成一处——现在 `UsersSection` 手写了一遍，另外两页各忘了不同的一半。

---

## 十、💭 其余

**FE2-08 `retry` 的返回类型仍在说谎。** `task/api/task.api.ts:17` 写
`api.post<VideoTask>`，后端 `TaskRetryOut` 只回 `{id, status, updatedAt}`（R6-05 的改动，
`task/schema.py:135`）。目前唯一的调用点（`TaskPreviewModal.tsx:99`）不读返回值，所以不炸；
但只要有人写 `data.title` 就会是 `undefined` 且编译通过。改成
`api.post<TaskRetryOut>` 是 3 行的事（类型里定义一下即可）。

**FE2-09 前端口令规则是后端的子集，注释却写"保持一致"。**
`entities/user/model/validation.ts` 顶部注释说 `PASSWORD_MIN_LENGTH` 与后端
`_check_password_strength` 一致，实际：

| 规则 | 后端 `policy.py` | 前端 `checkPassword` |
|---|---|---|
| ≥10 位 | ✅ | ✅ |
| 非纯数字 | ✅ | ✅ |
| 不同字符 ≥4（`PASSWORD_MIN_UNIQUE_CHARS`） | ✅ | ❌ |

`aaaaaaaaa1` 前端放行、后端 422。子集本身是正确方向（前端只做"少一次往返"），
但**注释说成一致**会让下一个人以为改一处就够。改注释或补上第三条，二选一。

**FE2-10 产品名有三个来源，其中一个是死的。** `.env.*` 的 `VITE_APP_TITLE`、
`vite-env.d.ts:4` 的类型声明都在，但全仓无人读取；实际渲染用的是
`shared/config/app.ts` 的 `APP_NAME`，浏览器标签用的是 `index.html` 里硬编码的 `<title>`。
三处改名要改三个地方——目前恰好都对，所以看不出来。

**FE2-11 全局 `retry: 1` 对 4xx 也重试。** `query-client.ts:10`。401 被拦截器清场后
再失败一次、403/404 也会各多发一次请求。写法上应该是
`retry: (count, error) => errorHttpStatus((error as ApiError).code) >= 500 && count < 1`。
不是事故，但 403 密集的页面上会把 403 的数量翻倍，日志不好读。

**FE2-12 token 存 localStorage。** 这是明确的取舍（`session.ts` 的注释讨论了隐私模式、
坏数据、跨标签页，看得出是想过的），不是疏漏。代价是任何一次 XSS 都能直接取走令牌；
换成 httpOnly cookie 需要后端加 CSRF 方案，成本不低。**记录在此，作为已知取舍，不建议本轮改。**

**FE2-13 一批新代码还在工作区没提交。** `entities/user|role|session`、`features/*`、
`modules/auth|user-access`、`pages/login|user-access` 全部 untracked（`git status` 的 `??` 十余项）。
201 条用例、四五个新实体，都在没有提交记录的状态下——这时如果误删或想回退某次实验，
没有可回的基点。建议本轮结论确认后立刻提交一版（哪怕只是"前端：用户与权限接入后端"）。

---

## 十一、值得保留的做法

1. **`errorHttpStatus(code // 100)`**——把"不要按完整业务码分支"写进工具函数，
   而不是写进文档让人记住。后端 `code = http_status*100 + 模块号*10 + 序号` 的约定
   被消费在一个地方，换模块不会静默失效。
2. **`signIn` 一次性写"令牌 + 过期时刻 + 身份快照"**（`session.ts:186`），
   并在注释里写明为什么不能分两步：避免"顶栏有名字但请求没带令牌"的状态撕裂。
3. **`router.test.ts` 断言装配不变量**（路由 ⇄ 注册表一一对应、每页都被守卫包住、
   bare 页不被包），而不是断言某个页面长什么样。骨架的正确性由骨架自己的测试守。
4. **`RequireAuth` 不在拦截器里跳转**——"守卫只有一处真相"，401 只负责改状态，
   跳转交给路由。避免了拦截器与路由互相打架这类经典 bug。
5. **`eslint.config.js` 用 `no-restricted-imports` 固化分层**，并且在注释里写明了
   两个真实边界（同一规则多 config 会整体覆盖、相对路径能绕过）。本轮实测全仓
   **没有**用相对路径跨层的漏网引用。

---

## 十二、建议顺序

| 顺序 | 项 | 改动量 | 理由 |
|---|---|---|---|
| 1 | FE2-01 会话恢复死锁 | ~30 行 + 1 用例 | 唯一"用户无出路"的问题，且下一次改 `Subject` 结构就会放大 |
| 2 | FE2-02 清缓存收成 `endSession` | ~20 行 | 跨账号看到旧数据，是会被当成"数据串了"的那类反馈 |
| 3 | FE2-03 mock 改动态 import | ~5 行 | 一个 `await`，入口包 −8.3 kB gzip，顺带把演示口令移出产物 |
| 4 | FE2-05 卡片校验 + FE2-04 脚手架默认权限 | ~15 行 | 都是"扩展点教什么"，越早越省事 |
| 5 | FE2-07 分页 | ~60 行（含一个 `Pager`） | 用户可见的矛盾，但改的是展示层，可以晚一步 |
| 6 | FE2-06 权限目录比对 | ~40 行（一条测试） | 建立机制，防止下一轮又飘 |
| 7 | FE2-08 / 09 / 10 / 11 | 各 3-10 行 | 顺手收，别单独立项 |

FE2-12（localStorage）与 FE2-13（提交）不是代码问题，是决策与流程，按你们节奏来。
