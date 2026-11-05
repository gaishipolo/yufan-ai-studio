# 前端代码评审报告（第五轮 · studio/）

> 范围沿用前提：**只审全局骨架与已接后端的扩展点**（入口 / 路由工厂 / 注册表 / 权限层 /
> API 契约与查询层 / 会话生命周期 / 构建产物 / 已接后端的「用户与权限」切片）。
> 纯展示页面、占位页、样式细节不在本轮范围内。
>
> 评审时间：2026-11-05 19:3x。基线：`tsc -b` 静默、`eslint --max-warnings 0` 无告警、
> `vitest run` **277 passed / 39 files**（上轮 245/36，**+32 例**）、`vite build` 通过
> （入口 `index` 79.91 kB / gzip 27.39 kB；`UserAccessPage` 27.69 kB），
> 产物内**搜不到** `admin@12345` / `editor@12345`。
> 对比方式：上轮 19:09 之后有 **25 个源文件**被改动，逐条读改动 + 自带用例 + 独立探针实测。

---

## 一、结论

**上轮 8 条（4 个 🟡 + 4 个 💭）全部修好，而且不是照着报告改一遍了事**——
每一处都带了自己的推理和回归用例，有些改法比我建议的更好：

- FE4-01 的修法不止"留住 refresh"，还把 `applyRefreshedTokens` 的守卫从
  「没有 access 就丢弃」改成「access 或 refresh 有一个就算会话还在」，
  并且区分了 `currentToken ? 'authenticated' : 'loading'`。
  **这是必须的**：否则冷启动换到票之后会被那条守卫原样丢掉，修了等于没修。
- FE4-03 除了派生类型，还加了一份 `contract.test.ts`：`expectTypeOf` 做类型层断言，
  再加一条**读源码**的断言挡住"退回手抄"（`expect(source).not.toMatch(/^export interface User\b/m)`）。
- FE4-02 的校验点收敛，配了一条扫三个调用点的用例（`validation.test.ts::CALL_SITES`）。

这一轮的骨架已经进入"**该有的都有了**"的状态。剩下的问题只有两类：
**同一功能里有一个信号源没跟着改**（🔴 一条），和**后端给了单一来源、前端还在硬编码**（🟡 一条）。

> 🔴 **FE5-01**：冷启动为了让"只有一个标签页的用户"不掉线，会删掉 `sf_token`；
> 而跨标签页监听正是以 `sf_token` 被删作为「他页退出」的信号。
> 实测后果：**两个标签页都闲置超过 2 小时后，刷新其中一个 → 两个一起掉线，
> 共享存储里的 refresh 也被抹掉**（`sf_refresh_token = null`）。
> 刚修好的 7 天会话，在多标签页这个配置下被自己人销毁。

---

## 二、上轮复核（逐条实测）

| 编号 | 上轮结论 | 本轮实测 | 判定 |
|---|---|---|---|
| FE4-01 | 🔴 冷启动连 refresh 一起删 | `session.ts:86 clearAccessToken()` 只清 access + 快照；`applyRefreshedTokens` 守卫改为「access 或 refresh 任一存在」；`SessionBootstrap` 先换票再 `/me`，401/网络故障/重试三条出口都有用例 | ✅ 真修好（单标签页） |
| FE4-02 | 🟡 重置密码文案与后端相反 | 文案改为「该账号所有已登录设备会被强制下线」，注释引用了 `revoke_all(reason="password_reset")` | ✅ 真修好 |
| FE4-03 | 🟡 生成物只被 1 处引用 | `User` / `Me` / `RoleBrief` 全部改为从 `UserOut` / `MeOut` / `RoleBriefOut` 派生；新增 `contract.test.ts` 双向断言 | ✅ 真修好（超出预期） |
| FE4-04 | 🟡 `identityApi` 死代码 + 重名 `displayNameOf` | `identityApi` 删除并留边界说明；`session` 那份 `displayNameOf` 删除 | ✅ 真修好 |
| FE4-05 | 🟡 文档契约表停在 4 字段 | 表格改为 `TokenOut：{ accessToken, tokenType, expiresIn, refreshToken, refreshExpiresIn, user }`，并加"别再照表格手抄" | ✅ 真修好 |
| FE4-06 | 💭 Pager 页码越界不回收 | `Pager` 增加 `useEffect` 回写边界值（放在提前 return 之前，hooks 无条件）；`totalPages <= 0`（未知总数）不回收；4 条用例 | ✅ 真修好 |
| FE4-07 | 💭 一行 loading 传染所有行 | 改为 `isPending && variables?.id === user.id` | ✅ 真修好 |
| FE4-08 | 💭 `setSubject` 不校验是不是同一个人 | `setSubject` 增加 `subject.id !== current.id` 判定，并对 GUEST 占位身份开了例外 | ✅ 真修好 |
| FE4-09 | 💭 `isAuthEntryRequest` 用 `includes` | 改为剥掉 query/hash 后精确比对 + `endsWith`；补了两条用例（形似路径、带查询串） | ✅ 真修好 |

顺带发现两处**没在报告里点名但一起修了**：改自己密码成功后主动 `endSession('password')`
（新增第 5 个登出原因，登录页文案已配），以及 `Pager` 之前只有 1 页时也照常渲染的边角。

---

## 三、🔴 FE5-01 冷启动删 `sf_token`，会把他页踢下线并级联销毁 refresh

### 链路

```
标签页 B（闲置 >2h 后刷新）
  └─ resolveInitialSession()：access 过期、refresh 还活着
       └─ session.ts:86  clearAccessToken()
            └─ writeStorage('sf_token', null)        ← 删的是**共享的** localStorage 键
                 └─ 标签页 A 收到 storage 事件
                      └─ use-session-watch.ts:84  if (event.key !== TOKEN_STORAGE_KEY ...)
                           └─ endSession('external')  → A 判定「此账号已在其他标签页退出登录」
                                └─ session.ts:389  signOut → persistToken(null)
                                     └─ 把 sf_token / sf_refresh_token / sf_subject **全部删除**
```

B 刚刚换到的新票，被 A 的 `signOut` 连同 refresh 一起抹掉；
B 随后也收到「`sf_token` 被删」，一并 `endSession('external')`。**两个标签页一起回到登录页，
且 localStorage 里什么都不剩** —— 下次打开必须重新输密码，后端那 7 天会话再也接不上。

### 实测

```
[probe] A.status= anonymous
[probe] A.reason= external                       ← "此账号已在其他标签页退出登录"（其实没人退）
[probe] storage sf_token= null
[probe] storage sf_refresh_token= null           ← 刚续上的 refresh 被清掉
[probe] storage sf_subject= null
```

单标签页的冷启动是正确的（同一个探针第一段）：

```
[probe] status= loading  token= null  refresh= rt-alive      ← 保住 refresh，等换票
```

### 性质说明（不回避）

这条**不是本轮引入的**：改动前冷启动同样会删 `sf_token`（`persistToken(null)`），
同样会把他页踢掉 —— 只是那时两边一起回登录页，看起来"一致"。
本轮修完之后 **B 能恢复了，A 却被拖下水**，于是这个问题第一次变得可见，
而且它恰好抵消了这次修复在多标签页下的全部收益。

按严重度我仍给 🔴：它销毁的是凭证本身（refresh 被抹掉，不可逆，只能重新登录），
而多标签页是这个产品很常见的用法（任务中心开一个、素材库开一个）。

### 建议改法（一行）

把跨标签页的判据从 **access 令牌**换成 **refresh 令牌** —— 后者才是"会话是否真的结束"的信号：

```ts
// use-session-watch.ts:84
import { REFRESH_TOKEN_STORAGE_KEY, useSessionStore } from './session';

const onStorage = (event: StorageEvent) => {
  // 只认刷新令牌被删：那才是「他页真的退出了」（signOut → persistToken(null) 会删它）。
  // access 令牌被删还有另一种含义 —— 冷启动「access 过期、refresh 尚存」时
  // clearAccessToken() 只清 access，会话本身仍然有效，这时不能跟着退出。
  if (event.key !== REFRESH_TOKEN_STORAGE_KEY || event.newValue !== null) return;
  ...
};
```

换完之后三条路径自动对齐：

| 动作 | 写/删的键 | 其他标签页的反应 |
|---|---|---|
| `signOut`（任何原因） | 删 refresh | 跟着退出 ✅ |
| `clearAccessToken`（冷启动） | 只删 access | 不受影响 ✅ |
| `applyRefreshedTokens` / `signIn` | 写 refresh（newValue 非空） | 忽略 ✅ |

配套补一条回归用例：模拟「access 被删、refresh 仍在」的 storage 事件，断言 store 仍是
`authenticated`；再补一条「refresh 被删」断言 `anonymous + external`。
（现有 `use-session-watch.test.tsx` 里已经有 storage 用例，加在这两类上即可。）

---

## 四、🟡 FE5-02 口令策略有服务端下发接口，前端仍硬编码，且测试改不动

后端为此专门开了接口，还留了一段"预判"注释：

```python
# api/src/modules/identity/policy.py
def describe() -> dict[str, object]:
    """给 `GET /identity/password-policy` 用的策略快照。

    **文案也在这里出**。前端原本在 `validation.ts` 里硬编码「至少 10 位」，
    后端改成 12 位之后，用户按提示输入却被 422 拒绝——这类不一致不是靠
    「记得同步」解决的，得让策略只有一个来源。
```

现状：

- 接口在 `router.py:131`，**匿名可访问**，已进生成物
  （`schema.ts:248 PasswordPolicyOut`、`:690` 的 `EndpointData` 映射）；
- 前端 `grep password-policy src/` → **只有生成物自己**，零调用；
- `validation.ts:42` 仍写死 `PASSWORD_MIN_LENGTH = 10`、`PASSWORD_MIN_UNIQUE_CHARS = 4`，
  三个弹窗的 hint 文案（「至少 10 位」）也是硬编码。

更关键的是**这条不一致不会被测试拦住**：`validation.test.ts` 用**本地常量**做期望值，
所以后端把 10 改成 12 时它照样全绿：

```ts
expect(checkPassword('a'.repeat(PASSWORD_MIN_LENGTH - 1), '新密码'))
  .toBe(`新密码至少 ${PASSWORD_MIN_LENGTH} 位`);   // 用的就是被改的那个常量
expect(checkPassword('abcd1234ef')).toBeNull();    // 恰好 10 位，后端改 12 后这条会变成误判
```

对比 `permission-catalog.test.ts`：它**解析后端源码**逐条比对，所以后端加权限点会红。
口令策略缺的正是同一层保护 —— 而这次它比权限点更容易做，因为**后端已经把答案端出来了**。

两条路，任选：

1. **消费接口**（推荐，也是后端写那段注释的本意）：
   `GET /identity/password-policy` 返回 `{ minLength, maxLength, forbidAllNumeric,
   forbidSameAsUsername, message }`，登录前的注册/改密场景匿名就能取；
   用一个 `usePasswordPolicy()` + `sessionStorage` 缓存即可，`checkPassword` 改成
   接收策略参数（保留默认值给离线/首屏兜底）。
2. **至少加一条源码比对的用例**（成本 10 行，立刻止血）：
   像 `permission-catalog.test.ts` 那样读 `api/src/modules/identity/policy.py`，
   把 `PASSWORD_MIN_LENGTH` / `PASSWORD_MIN_UNIQUE_CHARS` 解析出来与本地常量比。

顺带一个小的：后端有 `PASSWORD_MAX_LENGTH = 128`（注释说明"太长会让 scrypt 的输入
可以当 DoS 用"），前端 `checkPassword` 没有上限校验。没有上限时，粘一段 10 万字的文本
会先过前端、再被后端 422 —— 加一条 `value.length > 128` 的成本可以忽略。

---

## 五、💭 其余

- **FE5-03 `adoptStoredTokens` 可能短暂把界面点亮成"无权限"**：
  `session.ts:378` 在采纳他页令牌时置 `status: 'authenticated'`，但**不碰 subject**。
  冷启动时 subject 还是 `GUEST_SUBJECT`（`permissions: []`），于是从「采纳成功」到
  `/me` 回来之间，`<RequirePermission>` 会对声明了权限的页面渲染 `ForbiddenState`、
  侧边栏整排消失 —— 一帧的"我没有权限了"。
  和 `applyRefreshedTokens` 的处理对齐即可：**subject 还是 GUEST 时保持 `loading`**
  （`status: current.subject.id === GUEST_SUBJECT.id ? 'loading' : 'authenticated'`）。

- **FE5-04 `end-session.ts` 自己数错了入口**：文件头写「会话有**五条**结束路径」
  （新增了 `password`），下面第三段仍写「四个入口都调它」。同一段注释里两个数字，
  正是之前反复出现的"注释变成假事实"的雏形。改成"五条"或干脆去掉数字。

- **FE5-05 `useMe` 的 `staleTime` 与权限变更**：`session.queries.ts` 里 `/me` 的
  `staleTime: 5 * 60_000` + `refetchOnWindowFocus: false`，意味着管理员被降权后，
  最长 5 分钟内界面仍按旧权限显示（后端仍会 403，不是安全洞，是体验问题）。
  目前只有「改自己资料」会主动 `setQueryData`。若以后加"我的角色变了要立刻反映"，
  记得走 `invalidateQueries(sessionKeys.me)` 而不是等 stale。

---

## 六、建议顺序

1. **FE5-01**（一个 import + 一个常量 + 两条 storage 用例）—— 改完 7 天会话才算真的闭环。
2. **FE5-02 第 2 条**（10 行源码比对）先止血，之后再决定要不要消费接口。
3. **FE5-03**（一行条件），顺手。
4. FE5-04 / FE5-05 文档与观察项，不阻塞。

---

## 七、值得保留的做法

- **`contract.test.ts` 的"两层"写法**：`expectTypeOf` 保证类型兼容，读源码保证"没有退回手抄"。
  第二层尤其对 —— 手抄版只要字段写全，类型断言照样过，可它会在后端加字段时静默落后。
  这是本仓库目前最接近"契约守护"的东西。
- **`validation.test.ts::CALL_SITES`**：把"三处都要调同一个校验"从口头约定变成扫源码的断言，
  还写了"新增第四处请加进名单"。这类"元测试"比多加 10 个业务用例更能防止腐化。
- **`Pager` 的 `totalPages <= 0` 语义**：把"还不知道总数"和"总数为 0"分开，
  并在三条调用链上统一（`ImagePage` / `TaskCenterPage` / `UsersSection` 都传 0）。
  注释还写明了为什么不能回收 —— 一次网络抖动不该把用户从第 3 页踢回第 1 页。
- **`SessionBootstrap` 的三起点注释 + 5 条冷启动用例**：把"漏了换票会导致什么"
  （`useMe(Boolean(token))` 永远 disabled → 死在 loading）写在了代码旁边，
  下一个人改动这段时不用重新推一遍。
- **`ChangePasswordModal` 主动结束会话并配 `password` 原因**：不是等 401 被踢，
  登录页还能说清"这是你刚改密码的结果"，而不是让用户以为被入侵。细节做得很好。

---

*本轮未改动任何代码，只新增了两个临时探针（`__probe-crosstab` / `__probe-cascade`，已删除），
基线在删除后复跑仍为 277 passed。*
