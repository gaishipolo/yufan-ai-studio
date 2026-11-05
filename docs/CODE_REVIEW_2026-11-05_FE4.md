# 前端代码评审报告（第四轮 · studio/）

> 范围沿用前提：**只审全局骨架与已接后端的扩展点**（入口 / 路由工厂 / 注册表 / 权限层 /
> API 契约与查询层 / 会话生命周期 / 构建产物 / 新增的「用户与权限」功能切片）。
> 纯展示页面、占位页、样式细节不在本轮范围内。
>
> 评审时间：2026-11-05 19:0x。基线：`tsc -b` 静默、`eslint --max-warnings 0` 无告警、
> `vitest run` **245 passed / 36 files**（上轮 221/33）、`vite build` 通过
> （入口 `index` 79.23 kB / gzip 27.14 kB；`UserAccessPage` 27.53 kB / gzip 8.90 kB），
> 产物内**搜不到** `admin@12345` / `editor@12345`。
> 工作区状态：前端 42 个改动文件 + 一批新增目录仍未提交；`.github/workflows/ci.yml` 新增未跟踪。

---

## 一、结论

**上轮 4 条全部真修好**，而且这一轮补齐的不是补丁，是一整代能力：

- FE3-01（会话层停在上一代）→ 续期、登出、跨标签页、恢复失败页全部到位，
  `TokenResult` 也改成从生成物 `TokenOut` 派生，**根因被掐掉**而不是把 6 个字段手抄一遍。
- FE3-02（`setSubject` 复活已结束的会话）→ `session.ts:288` 加了 `if (!get().token) return`。
- FE3-03（两个同名 `useSignOut`）→ 骨架那个删了，还留了一段说明"名字留给用户意图"。
- FE3-04（没有 CI）→ 后端 + 前端两个 job，前端跑的就是 `npm run verify` 同一条命令。

这一轮的骨架**已经可以判"够用"**。剩下的问题不再是"缺一层防护"，而是三件更细的事：
**新能力在一个最常见的场景下没生效**、**新扩展的界面把后端行为写反了**、
**"契约只在一个类型上收口"这个根因仍然只在 `TokenOut` 上解决**。

> 🔴 **FE4-01**：access 令牌过期后冷启动，会把**刷新令牌一起删掉**。
> 实测：`status=anonymous`、`refreshToken in storage = null`。
> 后端给的 7 天滑动会话，在"关掉页面 > 2 小时再回来"这条最常见的路径上等于不存在。

此外 4 条 🟡、4 条 💭，没有一条需要改动骨架结构。

---

## 二、上轮复核（逐条实测）

| 编号 | 上轮结论 | 本轮实测 | 判定 |
|---|---|---|---|
| FE3-01 | 🔴 前端未接 refresh/logout | `shared/api/auth.ts` 独立通道 + `token-refresh.ts` 单飞 + `end-session.ts` 原子出口；`mock-mode.test.ts` 跑通「登录→续期轮换→登出后旧票失效」全链路 | ✅ 真修好 |
| FE3-02 | 🟡 `setSubject` 不看 token | `session.ts:288` 令牌为 null 直接 return，注释写明"迟到的 /me 不会点亮界面" | ✅ 真修好 |
| FE3-03 | 🟡 两个同名 `useSignOut` | `use-session.ts` 只留 `useSignIn`，并写明"这里刻意没有 useSignOut" | ✅ 真修好 |
| FE3-04 | 🟡 仓库无 CI | `.github/workflows/ci.yml`：api 跑 ruff/mypy/pytest/契约比对，studio 跑 `npm run verify`；`app` 入口与 `verify` 脚本都已存在（已核） | ✅ 真修好 |

`FE3-01` 的修法值得单独记一句：`TokenResult = Pick<TokenOut, ...> & { user: Me }`。
以前是"后端加字段、前端不知道"，现在是"后端加字段、类型自动跟上"。**这是本仓库唯一一处
把生成物真正用起来的地方** —— 也正因为只有这一处，才引出 FE4-03。

---

## 三、🔴 FE4-01 冷启动会把刷新令牌删掉：7 天会话实际只有 2 小时

### 现场

`shared/access/session.ts:113` 的启动恢复有四个分支，第二个是"令牌过期"：

```ts
// session.ts:126
if (Number.isFinite(expiresAt) && expiresAt > 0 && Date.now() >= expiresAt) {
  persistToken(null);          // ← 这一行
  return blank('anonymous', 'expired');
}
```

`persistToken(null)` 不是"只清 access 令牌"，它在 `:74-81` 把**四把钥匙全部删掉**：

```ts
if (!token) {
  writeStorage(TOKEN_EXPIRES_STORAGE_KEY, null);
  writeStorage(SUBJECT_STORAGE_KEY, null);
  writeStorage(REFRESH_TOKEN_STORAGE_KEY, null);        // 7 天有效的那张
  writeStorage(REFRESH_EXPIRES_STORAGE_KEY, null);
  return null;
}
```

### 实测

造一个真实场景（access 2 小时前签发、refresh 还剩 7 天），只 `await import('./session')`：

```
[probe] status= anonymous
[probe] refreshToken in memory= null
[probe] refreshToken in storage= null      ← 期望 'still-valid-refresh'
```

### 为什么这条比看起来严重

`useSessionExpiryWatch` 处理的是"**页面开着**、令牌到点"。
而"页面关着"这段最长的闲置时间，恰恰只能靠启动恢复那条路径：

| 场景 | 结果 |
|---|---|
| 页面开着跨过 2 小时 | ✅ 到点续期成功，可待 7 天 |
| 关掉标签页 1.5 小时再打开 | ✅ 令牌未过期，直接 authenticated |
| **关掉标签页 3 小时再打开（午休 / 过夜）** | ❌ refresh 被删，回登录页 |
| 手机锁屏 3 小时 | ❌ 同上 |

也就是说这套续期机制只在"一直不关页面"时成立，而刷新令牌存在的意义正是覆盖"页面关着"。
**`FE3-01` 修的东西在最长的那段闲置里没生效。**

### 测试为什么没发现

`session.test.ts:34` 这条用例把**错误行为写成了预期**，而且只塞了令牌没塞 refresh：

```ts
it('令牌已过期 → 直接清空，不进入 loading', async () => {
  localStorage.setItem(TOKEN_STORAGE_KEY, 'expired-token');
  localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() - 1_000));
  localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify(subject));
  ...
  expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
  expect(localStorage.getItem(SUBJECT_STORAGE_KEY)).toBeNull();
```

"清空"这件事本身是对的（清 access、清快照、别闪登录页），漏的是**只清两把、留住第三把**。
用例名字里"直接清空"这四个字，正好把这个区分盖住了。

### 建议改法

1. `resolveInitialSession` 里把"过期"拆成两支：

```ts
if (expired) {
  const refreshToken = readStorage(REFRESH_TOKEN_STORAGE_KEY);
  const refreshExpiresAt = Number(readStorage(REFRESH_EXPIRES_STORAGE_KEY) ?? '0');
  const refreshUsable =
    Boolean(refreshToken) && (!refreshExpiresAt || Date.now() < refreshExpiresAt);

  if (refreshUsable) {
    // 只清 access 与快照，留住刷新令牌，交给引导阶段换票
    writeStorage(TOKEN_STORAGE_KEY, null);
    writeStorage(TOKEN_EXPIRES_STORAGE_KEY, null);
    writeStorage(SUBJECT_STORAGE_KEY, null);
    return { status: 'loading', /* token: null, refreshToken, ... */ };
  }
  persistToken(null);
  return blank('anonymous', 'expired');
}
```

2. `SessionBootstrap` 补一步"**有 refresh 但没 access** 就先换票"：

```tsx
const needsRefresh = status === 'loading' && !token && Boolean(refreshToken);
useEffect(() => {
  if (needsRefresh) void refreshAccessToken().catch(() => endSession('expired'));
}, [needsRefresh]);
```

漏了这一步会变成另一种事故：`useMe(Boolean(token))` 的 `enabled` 是 false，
查询永远不发，`status` 停在 `loading`，`<RequireAuth>` 无限转圈（正是 SessionRecovery 想解决的那种卡死）。

3. 用例补两条：**有 refresh 且未过期 → 保住它并最终 authenticated**；
**refresh 也过期 → 与现在一致，anonymous + reason=expired**。

---

## 四、🟡 FE4-02 重置密码弹窗写着"会话不受影响"，后端是"全部吊销"

`features/user-security/ResetPasswordModal.tsx:60`：

> 重置后旧密码立即失效，**本人已登录的会话不受影响（令牌仍有效）**。

后端 `identity/service.py:851`：

```python
await self.set_password(user, new_password)
await self.sessions.revoke_all(user.id, reason="password_reset")
```

这条注释是 R9 那轮特意加的（"让管理员知道踢干净了"）。也就是说：

- 实际的语义是**重置 = 强制下线所有设备**；
- 界面告诉管理员"不会影响对方"，他会以为对方还能继续用、事后才收到"我被踢了"的报障；
- 而且这正是重置密码最有价值的**安全属性**（口令泄露时重置应当立刻切断），
  被界面说成了"没有"。

建议文案直接反过来，并把它当卖点写清楚：
「重置后旧密码立即失效，**该账号所有已登录设备会被强制下线**（含正在使用中的会话），
本人需要用新口令重新登录。」

同一处还有一个次要问题：`PASSWORD_MIN_LENGTH` / `checkPassword` 前端会先校验一遍，
但**建号（UserFormModal）、重置（这里）、改自己密码（修改密码弹窗）三处都要各自记得调**。
这是上一轮反复出现的"不变量只守一个入口"的轻量版本 —— 目前三处都调了，
建议在 `checkPassword` 的注释里点明"三处共用，新增第四处必须接"，或者干脆把它包进
一个 `<PasswordField>` 组件，让"忘了校验"在结构上不可能。

---

## 五、🟡 FE4-03 契约生成物只被 1 个类型引用，其余仍是手抄

`shared/api/schema.ts` 749 行，全仓**只有一处** import：

```
entities/session/model/types.ts:2  import type { TokenOut } from '@/shared/api/schema';
```

而 `User` / `Role` / `Me` / `PermissionGroup` 全部是手写的，并且**和生成物的可选性对不上**：

| 字段 | 生成物 `UserOut` | 手写 `User` |
|---|---|---|
| `displayName` | `string \| undefined` | `displayName: string`（必填） |
| `isActive` | `boolean \| undefined` | `isActive: boolean`（必填） |
| `roles` | `RoleBriefOut[] \| undefined` | `roles: RoleBrief[]`（必填） |
| `email` | `string \| null \| undefined` | `string \| null` |

生成物的可选来自 Pydantic 默认值（`exclude_unset` 语义），手写版则假定"后端一定发"。
两边**永远无法互相赋值**，于是也就永远不会被编译器比对 —— 后端哪天真改成
`response_model_exclude_unset=True`，`user.isActive` 会在运行时变成 `undefined`，
而 `can()`、列表筛选、`isSuperuser()` 全都读它。

`FE3-01` 的教训不是"记得同步字段"，是"**不要手抄**"。现在这个教训只在 `TokenOut` 上落实了。
建议两步，成本都很低：

1. **类型从生成物派生**（保留手写版的可读性）：

```ts
import type { UserOut, RoleBriefOut } from '@/shared/api/schema';

// 后端这些字段有默认值、schema 里因此是可选的；但契约上一定会发，
// 这里把"一定发"显式收窄，收窄错了 tsc 会红。
type Required1<T, K extends keyof T> = Omit<T, K> & Required<Pick<T, K>>;

export type User = Required1<UserOut, 'displayName' | 'isActive' | 'roles'>;
```

2. **加一个零运行时的契约断言文件**（`entities/*/model/contract.test-d.ts` 思路），
用 `expectTypeOf<User>().toMatchTypeOf<UserOut>()` 之类钉住"手写版必须是生成物的子类型"。
它不替代后端测试，但能让"后端加字段"这件事在前端**红一次**，而不是等下一轮 review 挖出来。

---

## 六、🟡 FE4-04 同一批接口定义了两遍，其中一遍整套是死的

`entities/user/api/user.api.ts:44` 有一个 `identityApi`，和 `entities/session/api/session.api.ts`
声明的是**同一批后端接口**：

| 接口 | sessionApi | identityApi |
|---|---|---|
| `GET /identity/me` | `me: () => api.get<Me>(...)` | `me: () => api.get<Me>(...)` |
| `PATCH /identity/me` | `updateProfile(patch: ProfilePatch)` | `updateProfile(payload: Pick<UpdateUserPayload, ...>)` |
| `POST /identity/me/password` | `changePassword(payload: PasswordChangePayload)` | `changePassword(payload: { oldPassword; newPassword })` |

实测 `grep -rn "identityApi" src/` **只有定义处这一行**，零调用。
它不会报错，但会造成两件具体的事：

- 有人要加"改邮箱"时，最可能 import 的就是 `identityApi`（名字更像"身份接口"），
  然后发现 `ProfilePatch` 和它的入参类型不是同一个，二选一；
- 后端 `PATCH /identity/me` 改字段时，**要改两处**，改一处不会被任何检查发现。

建议删掉 `identityApi`，在 `session.api.ts` 顶部（那段已经写了"为什么 refresh/logout 不在这里"）
补一句"本人身份接口只有这一份，`entities/user` 只管管理员视角的别人"，
把"两个视角"的边界写死在注释里。

**同一类问题还有一处**：`displayNameOf` 有两份同名实现，语义不同：

```
entities/user/model/types.ts:73    displayName?.trim() || username || '未命名'
entities/session/model/types.ts:74 me.displayName || me.username          ← 零调用
```

`session` 那份没人用、也不 trim、也没有兜底。`FE3-03` 刚因为"同名不同义"删掉一个
`useSignOut`，这里又长出来一个。建议删 `session` 那份，让"昵称怎么取"只有一个答案。

---

## 七、🟡 FE4-05 扩展文档的契约表还停在 4 字段的 TokenOut

`docs/FRONTEND_EXTENSION.md:252`：

```
| 登录 | POST /identity/login | { username, password } → { accessToken, tokenType, expiresIn, user } |
```

同一份文档 §5.6.1.1 用了三屏解释"后端有 refresh、要单飞、登出要显式带票"，
表格里却还是 4 个字段，而且**没有 `/identity/refresh` 和 `/identity/logout` 两行**。

这不是排版问题：§5 开头写着"下面表格就是它的真实契约，前端直接消费即可，不要另起一套"。
`FE3-01` 的起因就是"照着一张缺字段的表抄类型"。文档现在自相矛盾，
下一个读表的人会再抄一次。

建议：表格补 `refreshToken` / `refreshExpiresIn`，加两行 refresh / logout，
并在表头点一句"令牌相关字段以 `shared/api/schema.ts` 的 `TokenOut` 为准，本表只做导览"。

---

## 八、💭 其余

- **FE4-06 `Pager` 页码越界不回收**：`Pager.tsx` 里 `current = clamp(page, totalPages)` 只影响
  显示，不回写调用方的 `page`。删掉第 3 页最后一条后，`page` 仍是 3，
  查询继续要第 3 页（后端回 `items: []`、`total` 变小），界面显示"第 2 / 2 页"+ "没有匹配的账号"。
  建议在列表页 `data` 变化后发现 `page > totalPages` 就 `setPage(totalPages)`（或让 `Pager` 在
  `current !== page` 时回调一次）。

- **FE4-07 一行的 loading 传染所有行**：`UsersSection.tsx:234` 的"启用"按钮用的是
  同一个 `useUpdateUser()` 实例的 `isPending`。启用 A 时，**所有**已停用行的按钮一起转圈。
  一个 mutation 的 pending 是整体状态，按行区分要 `mutation.variables?.id === user.id`。

- **FE4-08 `setSubject` 不校验是不是同一个人**：`session.ts:284` 现在守住了"没有令牌就不写"，
  但没守"写进来的 subject 必须属于当前会话"。换号场景（A 的 `/me` 迟到、B 已登录）里
  token 非空、subject.id 却是 A —— 界面会带着 B 的令牌显示 A 的权限。
  实际触发需要 A 的 `/me` 在卸载后仍能回到 `useMe` 的 effect，概率很低；
  一行 `if (subject.id !== current.subject.id) return;` 就能连这个缝一起封掉。

- **FE4-09 `isAuthEntryRequest` 用的是 `includes`**：`http.ts:46` 的
  `url.includes('/identity/login')` 现在正好只命中登录接口（已核 `/identity/me/logins`、
  `/identity/users/{id}/logins` 都不含该子串）。但后端只要加一个 `/identity/login-audit` 之类的
  路径，它的 401 就会被当成"密码错了"，**不续期直接清场**。
  改成 `url === '/identity/login'`（或 `endsWith`）即可，语义也更准。

---

## 九、建议顺序

1. **FE4-01**（唯一的功能性缺陷）—— 改 3 个文件 + 2 条用例，顺带把 `session.test.ts:34`
   那条"把错误行为写成预期"的用例拆成两支。
2. **FE4-02**（一行文案，但方向是反的，且和安全属性有关）。
3. **FE4-03 / FE4-04**（一个"删死代码 + 类型从生成物派生"的小 PR，能一次性把
   `FE3-01` 的根因彻底封掉，也顺手把两份 `displayNameOf` 合一）。
4. **FE4-05**（文档表格）。
5. 💭 四条，随手可做，不阻塞。

---

## 十、值得保留的做法

- **`shared/api/auth.ts` 的旁路通道**：注释把"为什么不走主干"写成两条独立原因
  （会递归 / 清场后注不进头），并明确"只做发请求+解包，怎么收由调用方决定"。
  这是本次改动里最难写对的一块，写对了。
- **`token-refresh.ts` 的三条规矩 + 单飞**：并发 401 共享 Promise、失败先怀疑"别的标签页换过了"、
  不碰身份不碰路由。而且它在换票返回后**重新取一次 state**（注释写了 zustand 换对象这件事），
  这种细节只有真踩过才写得出。
- **`end-session.ts` 的四条路径收敛**：把"谁该通知服务端"用一张表说清楚（只有 manual 该），
  而不是在四个入口里各写一遍 `queryClient.clear()`。`FE2-02` 的根因被结构性解决了。
- **`router.test.ts` 把"每个页面必须声明权限"变成断言**，还带一条反向断言
  （豁免项一旦补上权限就报错，逼人删名单）。测试能反过来约束测试自身，很少见。
- **`permission-catalog.test.ts` 解析后端源码做比对**：不依赖 openapi（权限码在 schema 里是自由字符串），
  而是直接读 `PermissionSpec`。这是在"没有更好的契约载体"时最务实的选择。

---

*本轮未改动任何代码，只新增了临时探针 `src/shared/access/__probe-cold-start.test.ts`（已删除）。*
