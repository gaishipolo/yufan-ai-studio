# 前端代码评审报告（第六轮 · studio/）

> 范围沿用前提：**只审全局骨架与已接后端的扩展点**（入口 / 路由工厂 / 注册表 /
> 权限层 / API 契约与查询层 / 会话生命周期 / 构建产物 / 模块声明）。
> 纯展示页面、占位页、样式细节不在本轮范围内。
>
> 评审时间：2026-11-06 14:2x。基线：`tsc -b` 静默、`eslint --max-warnings 0` 无告警、
> `vitest run` **316 passed / 45 files**（上轮 277/39，**+39 例 / +6 文件**）、
> `vite build` 通过（入口 `index` 79.52 kB / gzip 27.45 kB；`UserAccessPage` 27.59 kB）。
> 对比方式：上轮（11-05 19:40）之后 **57 个源文件**被改动（其中 14 个测试），
> 重点是 11:16–11:22 那批注册表重构。

---

## 一、结论

**这一轮的结构性改动是对的，而且比"再加测试比对"更彻底。**
`modules/*/module.ts` 现在只声明一次路由，侧栏入口与宫格卡片挂在路由上派生
（`route.nav` / `route.card`），`path` 与 `permissions` 从路由继承。
"同一个权限写三遍、改漏一处就是入口可见但 403"这个反复出现的问题，
第一次不是靠人盯、也不是靠测试比对三处一致，而是**从类型上写不出来**了
（`RouteNavDef = Omit<NavItemDef, 'path' | 'permissions'>`）。
生成器与守卫测试还共用了同一份渲染函数（`shared/registry/render-routes.ts`），
避免"两边都写错但比对仍绿"——这两处是本次改动里最值得保留的部分。

但**守卫的覆盖范围没跟上这次重构**。派生解决的是"同一份信息写三遍"，
没解决"骨架常量与模块声明对不上"：

> 🟡 **本轮最该修的一条**：`HOME_PATH` / `LOGIN_PATH` 与注册表之间**没有任何断言**。
> 实测把登录页路由改名为 `/signin`、按规范重跑 `gen:routes` 之后，
> **13 个测试文件 98 例全绿**；而运行时 `RequireAuth` 仍跳 `/login` ——
> 那个路径已经不存在，落到 `*` → 404，**用户退出登录后再也回不来**。
> 改 `HOME_PATH` 同理：28 例全绿，`/` 直接 404。
> 讽刺的是 `shared/config/app.ts:14` 的注释写着「避免两处各写一遍导致守卫跳到不存在的页面」——
> 病想到了，药没上。

上轮 5 条**一条都没修**。其中 FE5-01 更糟：现有用例
`use-session-watch.test.tsx:134`（"别的标签页删掉令牌 → 本页跟着登出"）把
上轮认定要改的错误行为**写成了预期**。也就是说，上一轮的建议如果要落地，
第一件事是删掉一条看起来很正当的用例——这大概是它一直没被改的原因。

---

## 二、上轮复核（逐条实测）

| 编号 | 上轮结论 | 本轮实测 | 判定 |
|---|---|---|---|
| FE5-01 | 🔴 冷启动删 `sf_token`，把他页踢下线并级联销毁 refresh | `use-session-watch.ts:84` 仍是 `event.key !== TOKEN_STORAGE_KEY`；探针复现：`sf_refresh_token` / `sf_subject` 双双变 `null`。**且新增了一条用例把这个行为钉成预期** | ❌ 未修，且被测试固化 |
| FE5-02 | 🟡 口令策略硬编码，接口零调用 | `grep password-policy src/` → 仍只有生成物自己；`validation.ts:35` `PASSWORD_MIN_LENGTH = 10`、`:38` `= 4`；仍无上限校验；`validation.test.ts` 仍用本地常量做期望 | ❌ 未修 |
| FE5-03 | 💭 `adoptStoredTokens` 可能点亮成"无权限" | `session.ts:378-384` 仍 `status: 'authenticated'` 且不碰 subject；唯一运行时调用点是 `token-refresh.ts:60`（续期失败兜底），冷启动 subject 为 GUEST 时仍会闪一帧 | ❌ 未修 |
| FE5-04 | 💭 `end-session.ts` 自己数错入口 | `:8` 写「**五条**结束路径」，`:22` 仍写「**四个入口**都调它」 | ❌ 未修 |
| FE5-05 | 💭 `useMe` 的 staleTime 与权限变更 | `session.queries.ts` 仍 5 分钟 + `refetchOnWindowFocus: false` | 观察项，未变 |

---

## 三、🟡 FE6-01 骨架常量与注册表脱钩，CI 全绿而产品打不开

### 现状

两个骨架常量住在 `shared/config/app.ts`，模块路由声明住在 `modules/*/module.ts`，
两边靠**人记得同步**：

```ts
// shared/config/app.ts:9
export const HOME_PATH = '/tasks';
// shared/config/app.ts:17
export const LOGIN_PATH = '/login';
```

现有的守卫只检查了常量的形状，没检查它们指向哪里：

```ts
// shared/config/app.test.ts:79-85
it('首页与登录页都是绝对路径（守卫直接用它们 Navigate）', () => {
  for (const path of [HOME_PATH, LOGIN_PATH]) expect(path.startsWith('/')).toBe(true);
  expect(HOME_PATH).not.toBe(LOGIN_PATH);
});
```

`router.test.ts` 反过来只检查"注册表 ⇄ router 一一对应"，不看常量。

### 实测（两次演练，均已还原）

**演练 A**：把 `modules/auth/module.ts` 的 `path: LOGIN_PATH` 改成 `path: '/signin'`
（模拟"改了路由忘了改常量"），按 CODE_STYLE 的合法流程重跑 `npm run gen:routes`：

```
已生成 docs/ROUTES.md（10 条路由 / 10 个模块）
✓ src/app/registry/routes.test.ts (2 tests)
✓ src/app/router.test.ts (7 tests)
✓ src/shared/access/... (全部)
 Test Files  13 passed (13)
      Tests  98 passed (98)
```

运行时后果（读码可确定，无需跑浏览器）：

```
未登录点「退出登录」
  └─ endSession('manual') → status=anonymous
       └─ RequireAuth: <Navigate to={LOGIN_PATH} />   // '/login'
            └─ 注册表里只有 '/signin'
                 └─ 落到 path: '*' → NotFoundPage
                      └─ 页面上没有任何登录入口
```

**演练 B**：`HOME_PATH = '/home'`，跑 `src/app` + `src/shared/config`：
`Tests 1 failed | 28 passed`。那 1 条失败是下一节（FE6-03）的 CRLF 假红，
与 `HOME_PATH` 无关——**没有一条用例因为首页指向不存在的路径而失败**。
运行时：`/` → `Navigate('/home')` → `*` → 404。这条**连 `ROUTES.md` 都不会变**
（清单来自注册表，`HOME_PATH` 是骨架常量），所以没有任何"顺手重生成"能补救。

### 为什么值得单独列一条

这是本仓库反复出现的同一形状的第六次：**守卫验"有没有"，不验"通不通"**。
`app.test.ts` 那个 `describe('骨架路径常量')` 看起来正是在守这件事，
实际上只守住了"是不是绝对路径"——一个永远为真的断言。
它给了人"这块有测试"的安全感，比没有测试更坏。

### 建议改法（4 行，放在 `router.test.ts`）

```ts
import { HOME_PATH, LOGIN_PATH } from '@/shared/config/app';

it('骨架常量指向的路由确实存在，且登录页是裸页', () => {
  const byPath = new Map(appRegistry.routes.map((route) => [route.path, route]));

  expect(byPath.has(HOME_PATH), `HOME_PATH=${HOME_PATH} 不是任何模块声明的路由`).toBe(true);

  const login = byPath.get(LOGIN_PATH);
  expect(login, `LOGIN_PATH=${LOGIN_PATH} 不是任何模块声明的路由`).toBeTruthy();
  // 包在外壳里会形成「未登录 → 送登录页 → 又被登录守卫送回登录页」的死循环
  expect(login?.layout, '登录页必须是 layout: bare').toBe('bare');
});
```

顺便建议给 `CODE_STYLE.md` 的"新增/修改路由的合法流程"补一条：
**改 `route.path` 时，`HOME_PATH` / `LOGIN_PATH` 要么跟着改，要么由这条测试拦住。**

---

## 四、🟡 FE6-02 `bare` 路由的 `permissions` 被静默丢弃

`create-registry.ts` 对所有路由一视同仁地收集权限：

```ts
// create-registry.ts:63-64
const permissions = def.permissions ?? [];
permissions.forEach((p) => declaredPermissions.add(p));
```

`router.tsx` 却只给 shell 路由包守卫，bare 路由直接渲染：

```tsx
// router.tsx:48-57
...bareRoutes.map(({ path, Component, moduleId }) => ({
  path,
  element: (
    <Suspense fallback={<FullPageLoader />}>
      <Component />          {/* permissions 在这里丢了 */}
    </Suspense>
  ),
}))
```

### 实测

探针给一个裸页声明 `permissions: ['identity:read']`：

```
[probe2] bare route permissions 被收进注册表 = [ 'identity:read' ]
[probe2] declaredPermissions = [ 'identity:read' ]
[probe3] /login element.type = Symbol(react.suspense)
```

即：**注册表里"有权限"，`declaredPermissions` 里也有，`router.test.ts` 的
「模块声明的权限点都在权限目录里」照样通过，运行时零门禁。**
声明了一个不生效的门禁，比没声明更危险——写的人会以为已经守住了。

### 与已有约定不一致

同一个 `assertRouteDef` 已经在别的组合上 fail-fast 了：

```ts
// create-registry.ts:169-174
if (def.layout === 'bare' && (def.nav || def.card)) {
  fail(`…独立页面 ${def.path} 不能声明 nav / card。bare 页面不套外壳，放进侧栏或宫格会失去导航与登录态。`);
}
```

`permissions` 属于同一类"bare 页面上无意义"的字段，却没有同样的待遇。

### 建议改法（一行）

```ts
if (def.layout === 'bare' && def.permissions?.length) {
  fail(`模块 ${mod.id} 的独立页面 ${def.path} 不能声明 permissions：bare 路由不经过权限守卫，声明了也不会生效。`);
}
```

（如果将来真需要"带门禁的裸页"，那就应该让 router 给 bare 路由也包
`<RequirePermission>` 并加一条用例，而不是让字段悬空。）

---

## 五、🟡 FE6-03 生成物字节比对在 Windows 上必然假红

`routes.test.ts` 用**字节相等**守住 `docs/ROUTES.md`（这个思路是对的），
但仓库的换行策略会让它在 Windows 上稳定失败：

```
.gitattributes:  *.md text                     # 只声明"归一化"，没定 eol
git config:      core.autocrlf = true
renderRoutes():  输出 LF
```

### 实测

```
$ git checkout -- docs/ROUTES.md && python -c "…count(b'\r\n')"
CRLF count= 52  LF count= 52          # 干净检出后是全 CRLF

$ npx vitest run src/app
 FAIL  src/app/registry/routes.test.ts > 路由清单 > docs/ROUTES.md 与注册表一致
 Test Files  1 failed | 12 passed

$ npm run gen:routes                  # 写盘用 LF
$ npx vitest run src/app
 Test Files  13 passed (13)
```

而这两种状态下 `git diff` 都是空的：

```
$ git diff --numstat docs/ROUTES.md
                                       # 无输出
```

**这是最坏的组合**：本地红、CI（Linux 检出 LF）绿、`git status` 两种状态都"干净"。
开发者遇到的现象是"我什么都没改，测试却红了"，跑一次 `gen:routes` 又好了，
下次重新检出再红一次。一个会周期性误报的守卫，很快就会被当成噪声忽略——
那它守的东西也就等于没守。

（同样的坑对 `docs/MOCK_COVERAGE.md` 也成立，只是它目前没有比对测试，暂时不疼。）

### 建议改法

在 `.gitattributes` 里给**生成物**单独钉死 eol，不要动其它手写 md：

```gitattributes
# 生成物按字节比对，必须与生成器输出（LF）完全一致
docs/ROUTES.md text eol=lf
docs/MOCK_COVERAGE.md text eol=lf
```

改完需要 `git add --renormalize docs/ROUTES.md docs/MOCK_COVERAGE.md`。
比在测试里 `replace(/\r\n/g, '\n')` 更好：后者会把"生成器输出里混进了 \r"
这种真正的缺陷一起放过。

---

## 六、其余

### 💭 FE6-04 注册期校验的两个空档（实测）

```ts
// create-registry.ts:18-22
function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return '/';
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}
```

1. **`path: '/'` 不被拦。** `assertRouteDef` 只查 `!def.path.trim()`，
   而 `'/'` trim 后非空。注释写着"不允许 `''`：首页由 `HOME_PATH` 决定"，
   但等价物 `'/'` 能通过，normalize 之后就是 `/`，与 router 里的
   `{ index: true }` + `HOME_PATH` 重定向抢同一个位置。
   探针：`path:'/' 会被拦下 = false`。
2. **`'tasks'` 与 `'tasks/'` 不判重复。** normalize 不剥尾斜杠，
   两条路由都能进表（探针：`no-error`），而 React Router 会把两者匹配到同一个 URL。

两处都是几行的收紧：

```ts
const path = normalizePath(def.path).replace(/\/+$/, '') || '/';
if (path === '/') fail(`模块 ${mod.id} 不要声明根路由 '/'；首页请改 src/app/config.ts 的 HOME_PATH`);
```

### 💭 FE6-05 `MOCK_COVERAGE.md` 没有一致性守卫

`ROUTES.md` 有 `--check` + 一条字节比对用例；`gen-mock-coverage.mjs` **没有 `--check`**，
测试也不读它。当前内容是最新的（本轮跑了一遍，`git diff` 为空），
但那份文档里"未命中端点清单"是会被当成待办清单读的，静默过期后就会误导。

文档里写的"这是记录，不是门禁"我认同——**不该**断言"覆盖率达标"。
但"不作门禁"不等于"允许静默过期"：建议加 `--check`（与 `gen-routes.mjs` 同构，
约 10 行）+ 一条 vitest 比对，只守"文件与当前 mock 表一致"，不守覆盖率。

### 💭 FE6-06 `Slot` 用数组下标做 key

```tsx
// Slot.tsx:24-26
{slots.map((slot, index) => (
  <SlotItem key={`${slot.moduleId}:${index}`} slot={slot} />
))}
```

`index` 来自按 `order` 排序后的数组。同一模块贡献两个同类插槽且 `order` 相同时，
注册顺序变化会交换 key —— 插槽里如果带着弹窗状态（`UserMenuItems` 就是这样），
会表现为"弹窗开着却换了内容"。建议 key 用 `${moduleId}:${name}:${order}`，
或在 `SlotContribution` 上加可选 `key`。

---

## 七、建议顺序

1. **FE6-01**（4 行）—— 现在改路由名会让产品登录不进去，而所有门禁都是绿的。
2. **FE5-01**（一个 import + 一个常量 + 改掉那条固化用例）—— 上一轮就该修的。
3. **FE6-03**（`.gitattributes` 两行 + renormalize）—— 止住本地周期性假红，
   否则第 1、2 条的新用例也会被同样的噪声掩盖。
4. **FE6-02**（一行 fail）—— 与已有一致，顺手。
5. FE6-04 / FE6-05 / FE6-06 与 FE5-03 / FE5-04 / FE5-02，按需。

---

## 八、值得保留的做法

- **`render-routes.ts` 抽出渲染逻辑，给生成脚本与守卫测试共用。**
  如果脚本自己拼字符串、测试自己拼一份，两边写错还能比对通过。
  这个"只允许一份渲染实现"的取舍，比多写 10 条用例值钱。
- **`RouteNavDef = Omit<NavItemDef, 'path' | 'permissions'>`。**
  用类型禁止重复声明，而不是靠 review 提醒"记得三处一起改"。
  以后 `NavItemDef` 加字段，派生类型自动跟上，不会漏。
- **`endpoints.test.ts` 把覆盖边界写在文件头**（哪些路径不在此列、为什么），
  并且对"解析不了的模板"直接报错而不是静默跳过。
  一个守卫最怕的不是覆盖不全，而是**让人以为覆盖全了**。
- **`router.test.ts` 的两条反向断言**：豁免名单里的路由一旦补上权限就必须从名单移除；
  以及"除豁免外每个外壳页面都必须声明权限点"。后者挡住了
  `can(subject, [])` 恒为 `true` 这个静默放行口子。
- **`styles.test.ts` 守全局 CSS 的命名空间**（顶层类选择器必须带 `replacement-` 前缀）。
  全局引入的 CSS 没有模块作用域，命名溢出是真实事故，用测试钉住前缀是低成本高收益。
- **`permissions.ts` 薄壳 + 后端字节比对。** 前端不再保留第二份权限镜像，
  角色编辑的"整组替换"不会因为少一项而把真实权限悄悄清掉。

---

*本轮未改动任何生产代码。临时探针 `src/__probe_fe6.test.tsx` 已删除；
两次演练（`modules/auth/module.ts`、`shared/config/app.ts`）已 `git checkout` 还原。
`docs/ROUTES.md` 与 `docs/MOCK_COVERAGE.md` 因重跑生成脚本被写回 LF（与索引内容一致，
`git diff` 为空）。还原后复跑基线仍为 316 passed / 45 files。*
