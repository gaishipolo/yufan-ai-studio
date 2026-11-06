# 前端可扩展框架复盘：PR #1 合并暴露的问题

> 范围：`studio/` 的注册表式模块框架（`shared/registry` + `modules/*/module.ts` + `app/router.tsx`）
> 触发：合并同事 PR #1（`lweiting958-boop/feat/replacement-templates` @ `1915614`，合并提交 `48112bd`），
> 随后并入用户体系分支（`670c932` → 合并 `1fdaf3f`）
> 性质：**只做总结，不含代码改动**
> 日期：2026-11-06
>
> 本文只讲**前端**骨架。同一轮合并里后端骨架的同类问题见 `docs/EXTENSIBILITY_GAPS.md`（12 条），
> 两份是互补关系，不是重复。

## 0. 一句话结论

框架对「**加一个页面**」这件事是成立的——新增 `modules/<id>/module.ts` 就能自动获得路由、导航、
宫格卡片、权限守卫、懒加载分包，且不与其他人的改动冲突。

对「**加一层能力**」不成立：登录态、会话引导、品牌这类横切关注点无处安放，只能改骨架文件，
而这与文档 `§1.1 不变量 1「业务代码不修改骨架文件」`、`§1.3「没有任何功能需要改 router.tsx /
Sidebar.tsx / TopBar.tsx」` 直接矛盾。这次 PR 合并的绝大部分返工，都发生在这条缝上。

另外，框架的**校验全部集中在「路由拓扑」**（谁抢了哪个路径、导航是否指向真实路由），
而元数据一致性、接口契约、样式隔离、mock 覆盖这四类问题一条校验都没有，
它们的失效方式都是**静默的**：类型检查、lint、281 个单测、build 全绿，只有人点进去才发现。

> 11-06 补：接口契约、样式隔离、可枚举路由产物三项已补上机械守卫（§5）；
> **元数据三处冗余已从结构上消除**（`nav` / `card` 挂在 route 上派生），mock 覆盖按
> 「只记录、不做门禁」处理。

---

## 1. 问题总览

| 编号 | 问题 | 类型 | 严重度 | 现状（2026-11-06 复核） |
|---|---|---|---|---|
| A1 | 横切能力无法模块化，必然改骨架文件 | 框架 | 🔴 P0 | 能力层协议未定，仍未动 |
| A2 | 一个路由两层页面实现，合并时必然叠加 | 框架 | 🔴 P0 | 已收拾；决定不重构，靠薄壳约定约束 |
| B1 | `features` 层语义漂移（复用块 vs 整页） | 框架 | 🟠 P1 | 两层现状已写进文档；裸选择器已修 |
| B2 | 模块元数据三处冗余，改一处漏两处静默失效 | 框架 | 🟠 P1 | 已修：nav/card 由 route 派生，产物字节等价 |
| C1 | 骨架 widget 的品牌区没有配置化 | 框架 | 🟡 P2 | 已修：品牌收进单一常量 |
| C2 | 前端没有可枚举的路由产物 | 流程 | 🟡 P2 | 已修：`docs/ROUTES.md` + 字节比对 |
| C3 | 契约只覆盖类型，未覆盖路径与能力参数 | 流程 | 🟡 P2 | 已加路径守卫；表单改读 `paramSchema` |
| C4 | mock 覆盖率无门禁，随功能增长腐化 | 流程 | 🟡 P2 | 已定口径：只记录覆盖率，不做门禁 |
| D1 | dev server 崩溃伪装成「模块缺导出」 | 环境 | ⚪ 备忘 | 已定位 |

> 「现状」列是 2026-11-06 复核后的状态，与最初成文时不同；落地明细见 §5。

---

## 2. 逐条分析

### A1 · 横切能力无法模块化，必然改骨架文件（P0，最重）

**现象**：并入「用户体系」时，除了新增 `modules/auth`、`pages/user-access`、`entities/user|role`，
还必须改：

- `app/router.tsx`：+27 / −4，引入 `RequireAuth`、新增 `layout: 'bare'` 分支
- `app/providers.tsx`：+6 / −3，挂会话 Provider
- 新增 `app/SessionBootstrap.tsx`：87 行，冷启动换票

**根因**：`AppModule` 能声明的只有 `routes / nav / cards / slots`——全部是「页面级」的。
而登录态、会话恢复、全局守卫是**包裹在所有页面外面**的东西，粒度比 `AppModule` 高一整层，
框架没有对应的第二类扩展点（能力层 / capability），于是只能落到骨架里。

**为什么这次特别疼**：`app/router.tsx` 是「勿改清单」里的文件，两条业务分支（用户体系、PR #1）
都会碰它，谁先合谁就先把它改成自己需要的样子。扩展点是被**第一个真实需求倒逼**补出来的，
后到的分支只能在其形状上继续长。这次恰好是用户在 `1fdaf3f` 手工解掉，但这是运气，不是机制。

**还有一层**：早期框架假设「所有页面都在外壳内」，没有 `bare` 布局，
导致「用户还没登录就要看」的登录页根本无法接入。这说明**扩展点的设计基线取自当时的功能集合**，
一旦出现类型不同的功能（公开页、回调页、错误页），就得回头改内核。

### A2 · 一个路由两层页面实现，合并时必然叠加（P0）

**现象**：合并后素材页、模板页各出现两套 UI——骨架原有的 tab 列表 + PR 作者写的整页，
同屏并存、都在发请求。事后 `59513ba` 用「改用作者整页实现」修掉：两个文件各 `+5 / −81` 与
`+5 / −96`（合计删 177 行），现在 `AssetsPage.tsx` 与 `TemplatesPage.tsx` **都只剩 9 行**。

**根因**：`module.ts` 的 `load` 指向 `pages/<id>/XxxPage.tsx`，但真实页面写在
`features/<id>/Xxx.tsx`。`pages/` 于是退化成一层「薄壳」——只 `import` 一个 feature 组件。
框架里**没有任何机制规定「一个路由只能有一处实现」**：`pages/` 可以写完整页面，
`features/` 也可以写完整页面，两边都合法，合并时自然会长出两套。

**影响**：这不仅是视觉问题。两套实现意味着两套 `useQuery`，同一个接口被请求两遍；
两套权限判断；用户的筛选状态互相独立。合并时看起来「零冲突」（PR 自己也说 rebase 零冲突），
因为冲突根本不在 diff 层面，而在**语义层面**。

**11-06 决定：不重构。** 团队口径是「页面实现就该在 `pages/`」，与现状（`pages/` 9 行薄壳
+ `features/` 整页）不一致；本轮选择保留现状、在 `FRONTEND_EXTENSION.md §2` 写死薄壳规则，
而不是搬动同事刚写的文件。至于「这是否是必然复现的机制缺陷」仍无证据，
按**一次合并失误 + 靠约定约束**处理，不再当成必须重构的框架缺陷。

### B1 · `features` 层语义漂移（P1）

`docs/FRONTEND_EXTENSION.md` 把 `features` 定义为「**可复用的**业务交互块（如角色编辑器）」。
PR #1 实际产生了：

- `features/replacement-templates/ReplacementTemplates.tsx`（186 行，**整页**，不是可复用块）
- `features/archive-library/ArchiveLibrary.tsx`（79 行，同样整页）
- `features/replacement-templates/replacement-templates.css`（**386 行全局 CSS**）

样式里有 `.is-selected` 这种裸类名，会溢出到其他组件。包内其余选择器靠 `replacement-` 前缀
自觉隔离——没有 CSS Modules、没有 Tailwind 约束，命名空间全靠人守。

结论：`features` 现在同时承担「可复用交互块」和「整页实现」两个角色，两者生命周期与复用预期完全不同。
这是 A2 的上游原因。

**11-06 复核**：`.is-selected .replacement-card-check` 这处裸类名已改成
`.replacement-template-card.is-selected .replacement-card-check`，并加了 `styles.test.ts`
守「顶层类选择器必须带 `replacement-` 前缀」。`features` 的**语义不再强行统一**：
#7「整页搬回 `pages/`」本轮决定不做，改由 `FRONTEND_EXTENSION.md §2` 按「承认两层现状」
写清薄壳规则。

### B2 · 模块元数据三处冗余（P1）

**实测数据**（扫描 10 个 `module.ts`）：

| 指标 | 数量 |
|---|---|
| 模块 | 10 |
| 路由 | 10 |
| 导航项 | 9 |
| 宫格卡片 | 4 |
| `permissions` 字符串引用 | 20 次（去重后仅 **8** 个，平均每个写 2.5 遍） |
| `path` 声明 | 22 次（去重后仅 **9** 个，重复声明 **13** 次） |

`modules/assets/module.ts` 里 `asset:read` 写了 3 遍（route / nav / card），
`/assets` 也写了 3 遍。改权限或改路径时，**漏改一处不会报错**：

- 改 route 的权限、漏改 nav → 侧栏入口对所有人可见，点进去 403
- 改 nav 的权限、漏改 route → 有权限的人看不到入口

**已有的校验比想象中强**，这一点要如实说明——不是没有防护：
`create-registry.ts` 会在注册期拦下「模块 id 重复」「路由抢占」「导航指向不存在的路由」
「导航指向 bare 页面」「宫格卡片指向不存在的路由」；`router.test.ts:106` 还会校验
`declaredPermissions ⊂ 权限目录`（拼错会红）。所以「路径写错」和「权限码拼错」这两类**已经**被测出来了。

**真正的缺口只有一个**：同一模块内 route / nav / card 三处的权限**不要求一致**。
校验只管「这个码存在」，不管「三处是否同一个码」。

**11-06 落地（用户选定「10 个模块全改」）**：删掉 `AppModule.nav[]` / `cards[]`，
改成 `routes[].nav` / `routes[].card`，只写展示差异；`path` 与 `permissions` 由
`create-registry.ts` 从所属 route 派生，重复声明从结构上不可能发生。`AppRegistry`
产物形状不变——`docs/ROUTES.md` 的字节比对在改造前后**完全一致**，是等价性的硬证据。
`card.title` / `card.icon` 缺省取 `nav`，`bare` 路由禁止声明 nav/card 在注册期拦下。
脚手架、`modules/README.md`、`FRONTEND_EXTENSION.md §3/§5.4/§9` 全部同步。

### C1 · 骨架 widget 的品牌区没有配置化（P2）

`PR #1` 直接改 `widgets/top-bar/TopBar.tsx`，把写死的「天火」改成「雨范」：

```diff
+        <img src="/favicon.svg?v=yufan" alt="雨范 Logo" ... />
-          <b ...>天火</b>
+          <b ...>雨范</b>
           <div className="h-4 w-px bg-line-strong" />
           <span className="text-sm font-semibold tracking-[.3px] text-ink">{APP_NAME}</span>
```

同一行下面就是 `{APP_NAME}`——品牌名有**三个来源**：TopBar 里写死的中文、`shared/config` 的
`APP_NAME`、后端 `app_name`。这次只改了两处，第三处靠 `APP_NAME` 兜着，暂时看不出来。

框架有 `topbar.actions` 插槽用于右侧动作区，但**左侧品牌区没有插槽、也没有配置项**，
所以「改个名字」这种最低风险的改动，也得动骨架 widget。

### C2 · 前端没有可枚举的路由产物（P2）

后端有 `openapi.json` + `API.md` 两份生成物，还有 `app export-openapi --check` 做字节级门禁。
前端对应位置是**空的**：路由表在 10 个 `module.ts` 里由 `import.meta.glob` 在构建期拼出来，
没有任何清单文件。

后果在本轮直接体现：按常识猜 `/images`、`/users`、`/roles`、`/audit`，**四个全错**；
真实路径是 `/image`（单数）、`/system/user-access`（两级）、`/assets`（复数）。
命名本身也没有规律可循。`docs/FRONTEND_EXTENSION.md §5.1` 那张 19 条路径的契约表，
是手工维护的，天然会漂。

### C3 · 契约校验只覆盖类型，未覆盖路径与能力参数（P2）

`shared/api/schema.ts` 是后端生成的，`API.md` 也被要求同步，所以**类型层面**有保障。
但 `entities/*/api/*.api.ts` 里的路径字符串是手写的：

```ts
catalog: () => api.get<ReplacementCatalog>('/templates/replacement-catalog'),
```

**本次实测**（脚本比对，28 条前端路径 vs 46 条 openapi 路径）：真正不匹配的有 **0 条**，
3 条「疑似」都是 `baseURL` 与路径前缀拼接造成的误报。也就是说**当前是对的**。

但没有任何门禁保护它：路径写错能通过 `tsc`、`eslint`、281 个单测和 `build`，
只有真正发请求时才 404。本次冒烟抓到的 `/templates/replacement-catalog` 404 与
`/assets/archive` 501，成因是**后端进程陈旧**（进程 18:58 启动，路由文件 21:36 才写盘），
不是前端调错——但这恰好说明：前端对「路径是否存在」完全没有自检能力，
一旦真是前端写错，表现和这次一模一样，排查成本也一样高。

**第三类表现：能力参数也没进契约。** 后端把生图能力声明在
`GET /models?capability=image` 返回的 `paramSchema`（`size` / `count` / `model` 的取值范围），
前端却把 `SIZE_OPTIONS` / `COUNT_OPTIONS` 写死成常量，尺寸还用了全角 `×`。后端受理时按
`paramSchema` 校验，于是又是一次「构建、类型、全部单测全绿，用户点提交才 400」。
这和路径写错是同一个根因：**生成物只把请求/响应的类型带过来了，运行时的路径与
能力参数仍然是手写的**。

**11-06 落地**：`src/shared/api/endpoints.test.ts` 从生成物 `schema.ts` 的 `EndpointData`
键取后端路径全集，扫描 api 层字面量（含 `const base` 展开）做子集校验，解析不了的路径模板
直接报错而不是静默跳过；`ImagePage` 的表单改为读 `paramSchema`，不再写死常量。覆盖边界
（`createRestResource` 工厂内部拼出的 detail 路径、运行时拼接的 URL 不在其列）
已写在测试注释里，不假装全覆盖。

### C4 · mock 覆盖率无门禁（P2）

10 个模块里只有 3 个有 `mock.ts`（`auth` / `usage` / `user-access`），
PR 新增的 `archive` / `replacement` 系列接口**零 mock**。
文档写的是「写 mock（**可选但强烈建议**）」——没有门禁的「强烈建议」等于没有。
mock 模式的页面会直接打到不存在的端点。

**11-06 落地（用户选定「只记录覆盖率」）**：`studio/scripts/gen-mock-coverage.mjs`
从生成物 `schema.ts` 的 `EndpointData` 取后端端点全集，用 Vite SSR 真实执行 mock 自动发现入口
（不靠正则猜 mock 文件），读 `mockRouteTable()` 的 `{ method, pattern }`，把路径参数代入样例值
`demo` 后按「同方法 + 正则能否命中」判定，生成 `docs/MOCK_COVERAGE.md`；`--check` 只比对不写盘。

**明确不做 CI 门禁**：一条 handler 能匹配多个端点（`/tasks/([\w-]+)` 同时命中详情与删除），
「每个端点都有 mock」无法可靠机械判定，硬做成断言只会变成可绕过的假守卫；
判定本身也是启发式的，命中不等于 handler 返回了正确数据。所以脚本不设达标线、不进
`npm run verify`，只保证「改了接口或 mock 后重跑一次，现状是可查的」。

**实测**：58 条端点 / 34 条 mock 路由 / 疑似覆盖 33 条，3 / 10 模块带 `mock.ts`；
未命中的 25 条里，归档（`assets/archive/*`）与替换模板（`templates/replacement-*`）
两整片为零，与本文最初的判断一致；另有后端新增的 `POST /images/{id}/retry` 尚未接 mock。

### D1 · dev server 崩溃伪装成「模块缺导出」（环境，备忘）

本次重启时 Vite 因 lockfile 变化重新预构建依赖，commit 阶段 `rm node_modules/.vite/deps_temp_*`
被本机批量删除守卫拦下（`count:69 / threshold:50`），Node 抛错退出。
崩溃残留的**空 transform 缓存**会让浏览器报：

```
does not provide an export named 'useSignOut'
```

指向业务代码，而业务代码完全正确（954 B，`export function useSignOut` 在第 17 行）。
排查顺序应该是：**先看 dev server 日志，再 `curl` 看响应字节数，最后才怀疑源码**。
这类假信号此前已经误导过一次（FE3 那次）。

---

## 3. 这次验证有效、不要退化的机制

复盘不能只列缺口。以下机制在本轮真实拦住了问题，属于框架资产：

1. **注册期校验**（`create-registry.ts`）：模块 id 重复、路由抢占、导航指向不存在路由、
   导航/宫格指向 `bare` 页面——配错在启动时抛错，不是运行时白屏。
2. **权限码目录镜像 + 逐条比对**（`entities/role/model/permission-catalog.ts` +
   `permission-catalog.test.ts`）：前端镜像与后端 `PermissionSpec` 对不上直接红。
3. **`declaredPermissions ⊂ 目录`**（`router.test.ts:106`）：权限码拼错会被测出来。
   这条尤其重要——拼错的失效方式是「入口对所有人永久隐藏」，fail-closed 且完全静默。
4. **`shell` / `bare` 分离**：守卫在这里统一包，业务侧不手写 `<RequireAuth>`。
5. **模块自动发现 + 懒加载**：`import.meta.glob` 让新增模块不需要登记，
   也就没有「多人改同一行」的冲突；页面仍按路由分包。
6. **ESLint 禁止模块互相 import**：把「复用必须下沉」从约定变成 lint 错误。
7. **门禁**：`tsc` / `eslint --max-warnings 0` / 40 文件 281 测试 / build + CI 双 job（Linux 上跑）。

---

## 4. 建议的优先级（仅排序，不含实施）

**P0｜先补扩展点的形状，再谈其他**
- 把「能力层」显式建成第二类扩展点（auth / session / theme / i18n），有独立目录与注册协议，
  不塞进 `modules`。目标是让「加登录」不再改 `router.tsx`。
- 明确「一个路由只有一处页面实现」：要么 `pages/` 只做落点薄壳，要么让 `module.ts` 直接
  `load` 整页组件。两者留一个。

**P1｜元数据收敛**
- `nav` / `cards` 继承所属 route 的 `path` 与 `permissions`，只声明差异（如 `nav: { label, group }`），
  消掉 13 次路径重复与 2.5 倍权限重复。
- 给 `features` 分层定义：复用块 vs 整页容器，样式强制 CSS Modules 或统一前缀。

**P2｜把静默失效变成显式失败**
- 生成 `docs/ROUTES.md`（从 `appRegistry.routes` 直接读，无需新依赖），补齐 C2。
- 加一条「api 层路径 ⊂ `openapi.paths`」的测试（本轮验证脚本约 20 行即可），补 C3。
- 新增端点必须带 mock，纳入 CI，补 C4。（**11-06 复核否决**：改为只生成 `docs/MOCK_COVERAGE.md` 记录覆盖率，理由见 C4）
- 品牌区间配置化（插槽或单一 config），补 C1。

---

## 5. 落地进展（2026-11-06 复核）

| 条目 | 本轮处理 |
|---|---|
| P0 生图功能 | 表单参数改读 `paramSchema`、补 `/static` 代理、异步四态闭环；提交 `34971d5` |
| P1 契约生成物 | 权限目录 111 行手抄 → 生成物薄壳，纳入前端两份生成物；提交 `34971d5` |
| C1 品牌 | 全部收进 `shared/config/app.ts`；`app.test.ts` 钉住 `index.html` 与界面组件 |
| C2 路由产物 | `npm run gen:routes` 生成 `docs/ROUTES.md`，`app/registry/routes.test.ts` 字节比对 |
| C3 契约 | `endpoints.test.ts` 校验 api 层路径 ⊂ 生成物 `EndpointData`；表单改读 `paramSchema` |
| B1 样式 | 裸 `.is-selected` 加前缀限定，`styles.test.ts` 守顶层选择器命名空间 |
| B2 元数据派生 | 10 个模块全改为 `routes[].nav` / `routes[].card`，`path` / `permissions` 由 route 继承；`ROUTES.md` 改造前后字节等价 |
| C4 mock 覆盖 | 用户拍板「只记录、不做门禁」；`npm run gen:mock-coverage` + `--check` 生成 `docs/MOCK_COVERAGE.md` |
| A2 分层 | #7 不做；`FRONTEND_EXTENSION.md §2` 按「承认两层现状」写清薄壳规则 |
| 未动 | A1 能力层（协议待定）、#7 整页搬回 `pages/`（用户明确不做） |

品牌常量化、路由清单、路径契约、样式守卫四项都是**零冲突纯增量**：只新增脚本 / 测试 /
文档与少量常量引用，不碰 `router.tsx`，也不动同事刚写的页面。
B2 是唯一动了 10 个模块声明的结构性改造，但 `AppRegistry` 产物形状不变
（`docs/ROUTES.md` 的字节比对改造前后完全一致），下游 widget / 路由代码一行未改。
C4 按用户拍板停在「记录」，不设达标线、不进 `npm run verify`，避免把启发式判定伪装成门禁。

---

## 附录 A · 本轮实测数据

| 项目 | 结果 |
|---|---|
| 模块 / 路由 / 导航 / 卡片 | 10 / 10 / 9 / 4 |
| 权限码引用 / 去重 | 20 次 / 8 个 |
| `module.ts` 内 path 声明 / 去重 | 22 次 / 9 个（重复 13 次） |
| 前端 api 层路径 vs openapi | 28 条 vs 46 条，真实不匹配 **0** 条 |
| 门禁 | tsc 0 / eslint 0 / 281 测试全绿 / build 3.69s / 入口 80.04 kB（gzip 27.46） |
| 路由冒烟 | 9 个页面全部渲染，无 pageerror；`/studio` `/image` `/tasks` `/assets` `/templates` `/storyboards` `/shots` `/usage` `/system/user-access` |
| 已存在的 mock | 3 / 10 模块；34 条 mock 路由疑似覆盖 58 条端点中的 33 条（`docs/MOCK_COVERAGE.md`） |

## 附录 B · 证据索引

| 结论 | 证据 |
|---|---|
| 横切能力改骨架 | `670c932`：`studio/src/app/router.tsx` +27/−4、`providers.tsx` +6/−3、新增 `SessionBootstrap.tsx` 87 行 |
| 页面两层叠加 | `48112bd` 改 `pages/{assets,templates}/*Page.tsx`；`59513ba` 两文件合计 −177 行，收至各 9 行 |
| features 当整页用 | `features/replacement-templates/ReplacementTemplates.tsx`（186 行）、`replacement-templates.css`（386 行，含裸类名 `.is-selected`） |
| 品牌区硬编码 | `git diff bf0e3c7 48112bd -- studio/src/widgets/top-bar/TopBar.tsx`：`天火` → `雨范`，同一行仍有 `{APP_NAME}` |
| 元数据冗余 | `modules/assets/module.ts`：`asset:read` × 3、`/assets` × 3 |
| 已有校验 | `shared/registry/create-registry.ts:108-150`、`app/router.test.ts:106`、`entities/role/model/permission-catalog.test.ts` |
| 后端进程陈旧 | PID 41684 启动 18:58，`replacement_router.py` 写盘 21:36 → `GET /api/v1/templates/replacement-catalog` = 404 |
| dev server 崩溃 | `SAFE_DELETE_BULK_CONFIRM_REQUIRED {"count":69,"threshold":50}`，Vite `runOptimizer` → `commit` |
