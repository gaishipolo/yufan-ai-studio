# 前端代码评审报告（第三轮 · studio/）

> 范围沿用前提：**只审全局骨架与已接后端的扩展点**（入口 / 路由工厂 / 注册表 / 权限层 /
> API 契约与查询层 / 会话生命周期 / 构建产物）。纯展示页面、占位页、样式细节不在本轮范围内。
>
> 评审时间：2026-11-05 18:2x。基线：`tsc -b` 静默、`eslint --max-warnings 0` 无告警、
> `vitest run` **221 passed / 33 files**（上轮 201）、`vite build` 通过（入口包 76.22 kB /
> gzip 26.28 kB），产物内**搜不到** `admin@12345`。
> 工作区状态：前端改动仍未提交；后端 `openapi_export.py` / `identity/*` 有未提交改动
> （R10-01 的修复），`export-openapi --check` 三件套当前一致。

---

## 一、结论

上轮 7 条**全部真修好**，而且修法都对：会话恢复加了第三种结局、清缓存收成 `endSession`
一个原子出口、mock 改成动态 import、宫格卡片纳入注册期校验、权限点目录变成「能自动比对
的镜像」、分页做成公共件 `Pager`。这一轮的骨架已经不太需要"再加固一层"，问题转移到
**它和已经跑在前面的后端之间对不齐**。

本轮只有 1 条真问题，但它比前两轮的都更"结构性"：

> **后端的会话层在 17:23 长出了一代新能力（refresh 轮换 / 登出吊销 / 会话列表 / 踢下线），
> 前端的会话层停在上一代，而且三处注释把"没有"写成了事实。**

代码本身没错，注释也是诚实的（`use-sign-out.ts` 甚至主动写明"登出链路尚未接，免得被当成
已实现"）。问题是**注释迟早会变成谎话，而读注释的人不会去核对后端**。这一条的实际代价
已经在产生了：

```
登录出参：accessToken / expiresIn=7200s / refreshToken / refreshExpiresIn=604800s
前端 TokenResult：accessToken / tokenType / expiresIn / user      ← 后两个字段被类型层丢掉
```

`expiresIn=7200` 落成 `expiresAt`，到点 `endSession('expired')` —— 于是**每个用户工作满
2 小时被强制下线**，而服务端本来愿意让他待 7 天并滑动续期。

---

## 二、上轮复核（逐条实测）

| 上轮条目 | 状态 | 证据 |
| --- | --- | --- |
| FE2-01 会话恢复永久卡死 | ✅ 修好 | 新增 `SessionRecovery`：`status==='loading' && isError && !isUnauthorized` 时接管渲染，给「重试 / 重新登录」两个出口；`SessionBootstrap.test.tsx` 覆盖了 500 与断网 |
| FE2-02 清缓存只做手动退出 | ✅ 修好 | `end-session.ts` 成为唯一出口（`signOut` + `queryClient.clear()`），`manual / expired / unauthorized / external` 四条路径都调它；`end-session.test.ts` 断言四条路径都清 |
| FE2-03 生产包含 mock | ✅ 修好 | `http.ts` 改 `await import('@/mocks/adapter')`；重新构建后 `grep admin@12345 dist/assets/*.js` 无命中，入口包 94.38 → **76.22 kB**（gzip 33.53 → 26.28） |
| FE2-04 只有 1/9 模块声明权限 | ✅ 修好 | 9 个模块里 8 个声明了 `permissions`，`usage` 是唯一豁免且在模块注释里写了「后端无 `usage:read`」；`router.test.ts:37` 的 `NO_PERMISSION_PATHS` 还带反向断言（补了权限就必须从名单移除） |
| FE2-05 卡片不受注册期校验 | ✅ 修好 | `create-registry.ts` 对 `cards` 校验「有对应路由 / 不是 bare 页」，注释解释了为什么不校验 `matchPrefixes` |
| FE2-06 权限目录手抄且落后 | ✅ 修好 | `entities/role/model/permission-catalog.ts` 变成镜像，`permission-catalog.test.ts` **解析 `api/src/modules/**` 的 `PermissionSpec`** 逐条比对（缺 `image:read:all` / `task:retry:any` 的历史不会再重演）；`router.test.ts:106` 用 `declaredPermissions` 校验模块拼写 |
| FE2-07 列表只取一页 | ✅ 修好 | `shared/ui/Pager.tsx` + 三处接入（`ImagePage` / `TaskCenterPage`（页码进 URL）/ `UsersSection`），且都处理了「删到最后一页空了把页码收边界」 |

另外顺手记一条：R10-01 修的生成器（`RevokedSessionsOut` + 叶子名校验 + 内联回退）已经在
工作区生效，`schema.ts` 用修好的生成器重新产出了 749 行，前端那 13 行手改已经撤掉，
`export-openapi --check` 三件套一致。上一轮那条「两头堵死」现在解开了 —— **但改动还没提交**。

---

## 三、🔴 FE3-01 会话层停在上一代：没有 refresh，登出不吊销

**位置**：`entities/session/api/session.api.ts:8-9`、`entities/session/model/types.ts:19-26`、
`widgets/user-menu/use-sign-out.ts:19-21`、`shared/access/use-session-watch.ts:8`

三处注释各自断言了一件已经不再成立的事：

```ts
// session.api.ts:8
* 没有 logout 接口：JWT 无状态，登出 = 前端丢弃令牌（见 session store）。
* 也没有 refresh：令牌过期即 401，由拦截器统一清场后回登录页。

// use-session-watch.ts:8
* 后端没有 refresh 接口（JWT 无状态，见 entities/session/api），
```

后端 `2fc0fc6`（17:23）已经提供 `POST /identity/logout`、`POST /identity/refresh`、
`GET /identity/me/sessions`、`DELETE /identity/me/sessions`、
`POST /identity/users/{id}/revoke-sessions`。前端一个都没接。

**实测**（真实后端 + `TestClient`，取一张 editor 的票，模拟"用户点了退出登录"）：

```
A. 登录出参：expiresIn=7200s（2.0 小时）  refreshExpiresIn=604800s（7 天）
             refreshToken 前缀 eyJhbGciOiJIUzI1NiIsInR5...

B. 前端「退出登录」（endSession 只清本地）之后，用同一张票：
   /identity/me            → 200
   /tasks?page=1&pageSize=1 → 200
   /me/sessions 仍列 1 条：{ ip: 'testclient', expiresAt: 2026-11-12,
                             current: True }        ← 服务端认为你还在线

C. 真正调 POST /identity/logout → 200，同一张票再请求 /me → 401
   重复调用 logout → 200（幂等）

D. POST /identity/refresh 用 refreshToken → 200，新票 expiresIn=7200s
```

两个后果，性质不同：

1. **每 2 小时强制下线**（体验）。后端给了 7 天滑动续期，前端一次都没用。
   用户会把它当成 bug 报上来："为什么每天上午和下午各掉一次线。"
2. **"退出登录"没有退出**（安全）。共用电脑、网吧、演示机上点退出，令牌在服务端
   仍然有效到 2 小时后；`/me/sessions` 里那一条还是 `current: true`，管理员看到的
   "在线会话"和用户以为的"已退出"是两回事。用户因为"怀疑被盗"而退出的场景，
   这个按钮什么也没做。

`use-sign-out.ts` 的注释把这件事写明了（"令牌在服务端会一直有效到过期"），所以这不是
偷偷漏掉，而是**已知未接线**。但它现在挂在"主动退出"这个安全语义最强的按钮上，
建议按 🔴 处理：接 `POST /identity/logout`（失败也要继续清本地，不能把用户卡在页面里）。

**建议**（一起做，约 1.5 小时）：

- `TokenResult` 补 `refreshToken` / `refreshExpiresIn`（后端字段已经在了，现在被类型层丢掉）；
- `sessionApi` 加 `logout()` / `refresh()`；
- `endSession` 拆成「本地清场」与「通知服务端」两件事：**先清本地再发请求**，
  或者 fire-and-forget —— 反过来会让"网络断了就退不出去"；
- 过期那条路径改成先试 `refresh`，成功则 `signIn` 新票，失败才 `endSession('expired')`；
  同时注意并发：多个请求同时 401 时只允许一次 refresh（单飞）；
- 三处注释同步改掉 —— 它们现在是**错的事实**，比没有注释更费人。

---

## 四、🟡 FE3-02 `setSubject` 能把已退出的会话"复活"，并把快照写回磁盘

**位置**：`shared/access/session.ts:192-195`

```ts
setSubject: (subject) => {
  persistSubject(subject);              // 写 localStorage
  set({ subject, status: 'authenticated' });   // 不看 token 在不在
},
```

`signOut` 会把 `status` 置 `anonymous`、清掉令牌与快照；但只要有一个**迟到的**
`setSubject` 在它之后到达，状态会翻回 `authenticated`，而令牌是 `null`。

迟到是现实存在的：`useMe` 的 `staleTime` 是 5 分钟，React Query 在重连（`refetchOnReconnect`
默认 true）时会重取 `/me`；另一个标签页退出登录 → 本页 `endSession('external')` 的同时，
本页那张 `/me` 正好在飞。

**实测**（zustand store 直接驱动）：

```
退出后 setSubject → status=authenticated  token=null  subject.name=老大  落盘快照=true
不变量「authenticated ⇒ token !== null」成立？ false
```

两个副作用：

- **UI 复活**：`RequireAuth` 只看 `status`，于是外壳被渲染出来——导航按上一个账号的
  `permissions` 过滤、按钮按老权限显示，然后每个请求 401、再被踢一次。用户看到的是
  "退出登录之后又闪回了应用界面"。
- **残留 PII**：`signOut` 里 `persistToken(null)` 本来会把 `sf_subject` 一起清掉，
  这条路径把它**重新写了回去**。共用设备上，退出之后 `localStorage` 里仍留着
  `sf_subject = {"name":"…","roles":[…],"permissions":[…]}`。

**建议**：让不变量在写入处成立，三行：

```ts
setSubject: (subject) => {
  if (!get().token) return;   // 没有令牌就没有会话；迟到的响应一律丢弃
  persistSubject(subject);
  set({ subject, status: 'authenticated' });
},
```

配一条用例：`signOut()` 之后 `setSubject(...)`，断言 `status === 'anonymous'` 且
`localStorage.getItem('sf_subject') === null`。这是"退出登录"唯一能被自动化守住的方式。

---

## 五、🟡 FE3-03 两个同名的 `useSignOut`，一个是原子出口，一个不是

**位置**：`shared/access/use-session.ts:18` 与 `widgets/user-menu/use-sign-out.ts:17`

```ts
// 骨架公共层
export const useSignOut = () => useSessionStore((state) => state.signOut);

// 组件层：唯一的原子出口
export function useSignOut() {
  return useCallback(() => { endSession('manual'); navigate(LOGIN_PATH, { replace: true }); }, […]);
}
```

现在只有 `UserMenu` 引用了组件层那个（正确）。但 `shared/access/use-session.ts` 那个是
**骨架的公开 API**，一个新增"退出"入口的开发者最可能 import 的就是它 —— 而它调用的是
store 的 `signOut`：不清查询缓存、不跳转。

结果就是我们刚在 FE2-02 里修掉的那个 bug（换账号后看到上个账号的缓存）**从同一个门重新
进来**，而且这次不会有任何东西挡住它：类型对、lint 过、页面看起来也退了。

**建议**：把 `use-session.ts` 里那个删掉（它目前零调用），或者改名成
`useSignOutAction` 并加注释"只清 store，不推荐直接使用"。名字应该留给"用户点退出"
这件事，而不是留给底层动作 —— 这一层歧义迟早会被踩。

---

## 六、🟡 FE3-04 仍然没有 CI

`studio/package.json:18` 有 `verify = typecheck && lint && test && build`，写得很对；
但仓库根目录没有 `.github/workflows`，后端那套 204 条用例 + `test_openapi_contract.py`
的字节比对、前端这 221 条用例，**都只会在有人想起来的时候跑**。

这不是理论风险：R10-01 就是"生成物编不过"躺在仓库里没人发现 —— 如果 CI 里有一条
`tsc -b`，它会红在提交那一刻，而不是等一轮评审挖出来。

**建议**：一个 workflow，两个 job（`api`: pytest + ruff + mypy + `export-openapi --check`；
`studio`: `npm run verify`）。这一步的收益不在于"发现问题更快"，而在于**把已经写在
文档里的规矩变成不能绕过的门**。

---

## 七、💭 其余

- **`schema.ts` 仍然零引用（749 行）**。FE-03 时就提过，这轮它反而更值得说：手写类型
  与生成物分叉的活证据就是 `TokenResult` —— 后端 `TokenOut` 有 6 个字段，前端类型只写了
  4 个，而且**编译期没人报错**。生成物在那儿摆着却不消费，等于契约没有约束力。
  低成本做法：让 `entities/*/model/types.ts` 从 `schema.ts` 里 `Pick` 或 `type X = components[...]`。
- **登录冷却按"从现在起 15 分钟"锁**（`login-feedback.ts:29` 的 `parseCooldownSec` 从后端
  "请 15 分钟后再试"里取时长，`LoginForm` 据此禁用按钮并倒计时）。
  后端是滑动窗口：6 次失败后解锁时刻由**第一次**失败决定，不再继续失败的话，实际解锁比
  前端锁定的时间更早。前端多锁几分钟，方向安全但不是事实。💭 级：文案改成"最多 15 分钟"
  或直接不锁、只禁用 3 秒。
- **品牌名有三个来源**：`index.html` 的 `<title>雨范 AI 短视频工作台`、
  `shared/config/app.ts:20` 的 `APP_NAME = 'AI 短视频工作台'`（少"雨范"）、登录页与顶栏里
  5 处硬编码的"雨范 / AI STUDIO"。改一次名要改五处，`vite-env.d.ts` 里那条注释
  （"产品名不在这里，留两个开关只会改错那一个"）说的就是这件事，但硬编码还没收。
- **生成器修复未提交**。R10-01 的改动（`openapi_export.py`、`RevokedSessionsOut`、
  重新生成的 docs）都在工作区，`--check` 是绿的，但没进 commit。建议本轮修复一起提，
  否则下一个人 `git checkout` 之后会重新踩到"生成物编不过"。

---

## 八、建议顺序

1. **FE3-01**（会话层接 refresh + logout，1.5h）—— 唯一有安全含义的一条；
   顺带把三处假注释改掉，它们是这轮最容易被下一个人相信的东西。
2. **FE3-02**（`setSubject` 守卫，5min + 一条用例）—— 三行，止住残留快照。
3. **FE3-03**（删掉同名 hook，5min）。
4. **FE3-04**（CI，半小时）—— 一次投入，之后每轮评审都能少查一类事。
5. 💭 里挑两件：`TokenResult` 从 `schema.ts` 派生（顺手解决契约零引用）、
   冷却文案。

---

## 九、值得保留的做法

这轮有几处"改一遍就不用再想"的写法，值得在新模块里照抄：

- **`permission-catalog.test.ts` 去解析后端源码**。不是"记得同步"，而是"忘了就红"。
  比注释强，比人工比对强，而且给出了"只有前端检出就整体跳过"的降级路径。
- **`NO_PERMISSION_PATHS` 带反向断言**：豁免项一旦补了权限，测试就要求把它从名单删掉。
  豁免不会悄悄变成永久后门。
- **`http.ts` 那段 mock 动态 import 的注释**写了"为什么必须动态、写错会多出多少 kB"。
  半年后有人想改回静态 import，注释会拦住他。
- **`end-session.ts` 的模块注释**把四条结束路径一条条列出来，并说明"唯一出口"的理由
  （"另外三条是被动发生的，用户不会在那时想到该有人清缓存了"）。这正是上轮那条 bug
  的根因说明 —— 它写在了修复里，而不是写在报告里。
- **`Pager` 不显示总条数**：注释明确"各页措辞不同，由调用方决定"，避免公共件长出
  业务措辞。
