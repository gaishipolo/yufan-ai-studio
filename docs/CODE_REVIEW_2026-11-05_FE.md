# 前端代码评审报告（首次 · studio/）

> 评审时间：2026-11-05 15:20 ｜ 范围：`studio/` 全量（119 个 ts/tsx，6832 行）
> 关联：[`R4 后端`](./CODE_REVIEW_2026-11-05_R4.md)｜[`R3`](./CODE_REVIEW_2026-11-05_R3.md)｜[`R2`](./CODE_REVIEW_2026-11-05_R2.md)｜[`首轮`](./CODE_REVIEW_2026-11-05.md)｜[`CODE_STYLE.md`](./CODE_STYLE.md) / [`REVIEW_CHECKLIST.md`](./REVIEW_CHECKLIST.md)

---

## 一、结论

这一轮前端是**重写**，不是修补：`modules/` 纵向切片 + `shared/registry` 装配 + `shared/access` 权限骨架，三道骨架都在，而且质量高于我预期的平均水平。工具链也补齐了（ESLint 分层规则、vitest、`verify` 脚本），基线全绿。

但有一个结构性问题：**骨架与后端能力之间缺最后一根线**。后端这五轮已经把 `identity` 做完了（登录、角色、权限展开、`/me`），前端也把 session store、`RequirePermission`、`<Can>` 都写好了，唯独「谁去调 `setSubject`」没有人——`setSubject` 全仓零调用，`sf_token` 只读不写。开发期看不出问题，因为两个兜底同时在起作用；上线时两个兜底同时消失，结果是全站 401、页面全部显示「没有数据」。

第二个问题是**错误全部被吞**：9 个页面里零处 `isError`，只有 6 处 mutation 有 `onError`。于是「后端 500」「令牌过期」「网络断了」和「确实没有数据」在界面上是同一个样子。

### 质量基线（实测）

| 项 | 结果 | 说明 |
|---|---|---|
| `npx tsc -b` | ✅ 通过 | 严格模式，零 `any`、零 `ts-ignore` |
| `npx eslint . --max-warnings 0` | ✅ 通过 | 含分层 import 规则、jsx-a11y |
| `npx vitest run` | ✅ 80 passed（13 files） | 无 snapshot，全是行为断言 |
| 页面级集成测试 | ❌ 无 | `src/pages/**` 零测试 |
| 契约生成物使用率 | ❌ 0 | `shared/api/schema.ts` 零 import |

### 问题清单

| 编号 | 级别 | 问题 |
|---|---|---|
| FE-01 | 🔴 | 生产构建没有身份来源：无登录入口、`setSubject` 零调用、`sf_token` 只读不写；dev 靠后端匿名兜底 + `VITE_DEV_PERMISSIONS` 掩盖 |
| FE-02 | 🔴 | 全部 query 错误被吞：请求失败渲染成「没有匹配的任务 / 共 0 条」，与空数据不可区分 |
| FE-03 | 🟡 | `schema.ts`（630 行、标称自动生成）零引用；手写 `VideoTask` 已与 `TaskOut` 不一致，字段改名时 tsc 不会报错 |
| FE-04 | 🟡 | 权限骨架零使用：9 个模块 0 个声明 `permissions`，`<Can>` 零引用，`RequirePermission` 永远放行 |
| FE-05 | 🟡 | mock 与真实后端四处不一致，会掩盖已修的后端问题（删除计数、重试前置、详情进度、canReadAll） |
| FE-06 | 🟡 | 「重新生成」按钮永远可点，但后端只允许 `failed` 状态，其余状态必 409 |
| FE-07 | 🟡 | 默认时长取「最大档」（12s）而非目录 `default`（5s），直接放大计费成本 |
| FE-08 | 🟡 | `FALLBACK_MODELS` 与真实目录不一致（多出 `1:1`），且回退无任何提示 |
| FE-09 | 🟡 | 侧边栏角标不用 `GET /tasks/stats`，改为每 5s 拉一次列表只为取 `stats`，且固定 `scope=mine` |
| FE-10 | 🟡 | 批量下载/收藏/回收站是「假成功」toast；`useDeleteTasks` 定义了但零调用，删除能力未接线 |
| FE-11 | 🟡 | 可访问性三缺：Toaster 无 `aria-live`、Tabs 无 `role=tab`、Modal 无焦点陷阱 |
| FE-12 | 💭 | 选中项跨筛选残留、搜索框与 URL 单向同步、头像按钮无 onClick、`RouteError` 直出技术细节等 6 项 |

---

## 二、🔴 FE-01 生产构建没有身份来源

**位置**：`shared/access/session.ts:18-31`（唯一的身份来源）、`shared/api/http.ts:20-27`（唯一的令牌读取）

### 实测证据

```
$ grep -rn "setSubject\|signOut\|sf_token" src
src/shared/access/session.ts:49:  setSubject: (subject) =>
src/shared/access/session.ts:51:  signOut: () => set({ subject: GUEST_SUBJECT, ... }),
src/shared/api/http.ts:22:  const token = localStorage.getItem('sf_token');
```

- `setSubject` 只有定义，**零调用**
- `sf_token` 只有 `getItem`，**零 `setItem`**
- `modules/` 下 9 个模块，没有 `identity` / `login` / `auth`

### 为什么现在看不出来

两个兜底同时在起作用，谁也不会报错：

| 环节 | 开发期 | 生产构建 |
|---|---|---|
| 后端身份 | `APP_ENV != production` 时回退演示超管 `u_001`（`api/deps.py` 注释：「非生产环境回退演示账号，生产回 401」）| 401 |
| 前端身份 | `.env.development` 可设 `VITE_DEV_PERMISSIONS`，无值则 `GUEST_SUBJECT` | `GUEST_SUBJECT`（`import.meta.env.DEV` 为假，整段被摇掉）|

我用 TestClient 打了一次真实后端（test 环境）：

```
[无令牌] GET /tasks -> 200 OK          ← 开发期就是这样通过演示账号放行的
```

也就是说：开发期无令牌能拿到数据，是因为**后端**在兜底，不是因为前端做对了。前端从来没成功鉴权过一次。

生产构建的推演（把两个兜底同时关掉）：

1. 顶栏头像渲染 `subject.name.slice(0,1)` → `GUEST_SUBJECT.name === '未登录'` → 显示「未」
2. 所有 `api.get('/tasks')` 不带 `Authorization` → 后端 `get_actor` 返回 401
3. 401 被 `http.ts` 的拦截器包成 `ApiError` → 由 **FE-02** 吞掉 → 页面显示「没有匹配的任务」「共 0 条任务」

用户看到的不是「请登录」，而是「你没有任务」。这比直接报错更糟：它会让人以为数据丢了。

### 建议

按后端已经实现的能力接线即可，不需要新接口：

1. 新增 `modules/identity/module.ts` + 登录页，`POST /identity/login` → `setSubject` + 写 `sf_token`
2. 启动时若存在令牌，`GET /identity/me` 回填 `setSubject`（否则刷新即掉登录）
3. `http.ts` 响应拦截器补 401 分支：`signOut()` + 跳登录（现在 `session.ts:40` 的注释写着「401 时清空」，但没有任何地方实现它）
4. 建议把令牌迁移到内存 + `refresh` 机制：`localStorage` 下的长期凭证，任意 XSS 即可窃取（这条从首轮 B-03 一直挂到现在）

---

## 三、🔴 FE-02 全部 query 错误被吞

**位置**：`pages/**`、`features/**`、`widgets/**` 全量

```
$ grep -rn "isError\|error:" src/pages src/features src/widgets --include=*.tsx
（无输出）

$ grep -rn "onError" src --include=*.tsx | wc -l
6
```

6 处全在 mutation（创建、重试、收藏、生图…）。**query 一处都没有**。

### 后果链

`TaskCenterPage.tsx:55-57`：

```ts
const { data, isLoading, isFetching, refetch } = useTaskList(params);
const items = data?.items ?? [];
```

请求失败时：`data === undefined` → `items = []` → 走 `items.length === 0` 分支 → `<EmptyState title="没有匹配的任务" description="换个筛选条件或清空搜索关键字再试试" />`，同时 `TaskSearchRow` 显示「共 0 条任务」。

`AssetsPage`、`ImagePage`、`TemplatesPage` 是同一写法。于是：

| 真实原因 | 用户看到 |
|---|---|
| 令牌过期（401） | 没有匹配的任务 |
| 后端 500 / SQL 报错 | 没有匹配的任务 |
| 网络断开 / 代理没起 | 没有匹配的任务 |
| 确实没有任务 | 没有匹配的任务 |

叠加 FE-01 之后，生产环境上线第一天大概就是全站这个画面。

### 建议

- 每个列表页取 `isError`，用 `<EmptyState>` 的同款位置渲染错误态 + 「重试」按钮（复用 `refetch`）
- 或在 `query-client.ts` 加全局 `QueryCache.onError`：401 → `signOut`，其余 → `toast(e.message, 'error')`。注意别对轮询任务重复弹（用 `query.meta.silent` 或按 key 去重）
- `retry: 1` 会让失败延迟约 1 个 RTT 才显现，错误态出现前建议显示骨架屏而不是空状态

---

## 四、🟡 FE-03 契约层是死代码

`shared/api/schema.ts` 头部写着：

```ts
/**
 * 后端接口类型（自动生成，请勿手工编辑）。
 * 重新生成：cd api && .venv/Scripts/python -m cli export-openapi
 */
```

```
$ grep -rn "api/schema" src
src/shared/api/schema.ts:7:  *   import type { ... } from '@/shared/api/schema';   ← 只有它自己的注释
```

**630 行生成物，零引用。** 实体层另写了一份 `VideoTask`：

| 字段 | `TaskOut`（生成物） | `VideoTask`（手写） | 差异 |
|---|---|---|---|
| `progress` | `number?` | `number` | 可选 → 必填 |
| `coverUrl` | `string?` | `string` | 同上 |
| `authorName` | `string?` | `string` | 同上 |
| `favorited` / `downloadable` / `prompt` | `?` | 必填 | 同上 |
| `errorMessage` | `string \| null` | `string \| undefined` | null vs undefined |
| `startedAt` | `string \| null` | `string \| undefined` | 同上 |

我核对过真实响应：`progress`、`downloadable`、`authorName`、`modelId` 都会返回，所以现在没有实际故障。但代价是**这套「后端改字段、前端自动跟着变」的机制收益为 0**：因为没人 import 生成物，后端改名时 `tsc` 不会报错，只有上线后页面空白。

顺带一个小坑：`null` 与 `undefined` 的差异是有实际影响的——`TaskPreviewModal.tsx:74` 用 `task.errorMessage && ...` 判断，`null` 与 `undefined` 都 falsy 所以没事；但将来若有 `??` 与 `||` 混用就会分叉。

### 建议

实体层的类型改成从生成物派生，一行搞定且立刻获得漂移检查：

```ts
// entities/task/model/types.ts
import type { TaskOut } from '@/shared/api/schema';
export type VideoTask = TaskOut;                        // 或 Pick<TaskOut, ...> 收窄
```

如果担心可选字段导致页面到处 `??`，在 `task.api.ts` 里做一次归一（把 `null` 压成 `undefined`、给可选字段补默认值），**让归一集中在一处**，而不是每个组件各写一遍 `?.`。

---

## 五、🟡 FE-04 权限骨架零使用

```
$ grep -rn "permissions" src/modules/ | wc -l
1        ← 唯一一处还是 usage/module.ts 里被注释掉的示例

$ grep -rn "<Can \|useCan" src --exclude-dir=shared
（无输出：只有 Slot.tsx 和 ModuleNav.tsx 在 shared 内部用）
```

也就是说：

- `RequirePermission` 永远 `allowed === true`（`permissions: []` → `can()` 直接返回 `true`）
- `<ForbiddenState>` 在全仓永远不会被渲染
- 导航项的权限过滤、`<Can>` 的按钮隐藏，都没有真实调用方

功能上不算 bug（后端每轮都在加权限点，前端不藏也能用），但带来两个实际风险：

1. **删除 / 重试 / 创建这些会产生费用的按钮，对无权限用户完全可见**。点下去才 403，提示还是后端的文案。
2. 这层骨架的存在会让人误以为「前端已经做好门禁」——和之前 `identity/permissions.py` 里 5 个常量 4 个没人用是同一种模式。

### 建议

要么接上，要么删掉，别留半成品：

- 给 `task-center` 声明 `permissions: ['task:read']`，`studio` 声明 `['task:create']`，工具栏按钮包 `<Can permission="task:delete">`
- 同时把 `usage` 模块里注释掉的 `permissions: ['usage:read']` 保留成 TODO，别当成已完成

---

## 六、🟡 FE-05 mock 与真实后端四处不一致

mock 本身设计得不错（适配器层拦截 + 路由表自动注册 + `/tasks/stats` 与 `/tasks/{id}` 的注册顺序还专门写了注释）。但有三处行为与真实后端不同，而且**恰好把前几轮刚修好的问题掩盖掉了**：

| mock 行为 | 真实后端 | 掩盖了什么 |
|---|---|---|
| `DELETE /tasks` 返回 `{ deleted: ids.length }`（`task.mock.ts:143`） | 返回**实际删除条数**，不存在的 ID 会被跳过 | R4-05「批量删除部分成功静默 200」在 mock 下永远看不出 |
| `POST /tasks/{id}/retry` 不校验状态（`task.mock.ts:114-125`） | 仅 `failed` 可重试，否则 `InvalidState` → 409 | 见 FE-06 |
| `GET /tasks/{id}` 不调用 `db.tick()`（`task.mock.ts:85-94`） | 进度由 worker 独立推进 | 列表页 `tick()` 会推进度，详情接口不会 → 弹窗里进度条冻结在打开时的值，而 `useTaskDetail` 特意加了 2.5s 轮询也没用 |
| `canReadAll: true` 恒真（`task.mock.ts:69`） | 取决于 `task:read:all` | 「无权限时收起全部任务选项」这条路径在 mock 下永远走不到 |

第 3 条尤其值得修：它让「详情弹窗进度冻结」这个 R1 就提过的问题在 mock 环境下看起来已经解决了。

### 建议

mock 的价值是**行为对齐**，不是「有个响应就行」。三行改动：

```ts
// 删除：按实际存在且能删的 id 计数
const before = db.tasks.length;
db.tasks = db.tasks.filter((t) => !ids.includes(t.id));
return { deleted: before - db.tasks.length };

// 重试：与后端同一条前置条件
if (task.status !== 'failed') throw new HttpError(409, '仅失败的任务可以重试');

// 详情：先推进，再返回
handler: (ctx) => { db.tick(); ... }
```

---

## 七、🟡 FE-06 / FE-07 / FE-08 / FE-09 / FE-10

### FE-06 「重新生成」永远可点

`TaskPreviewModal.tsx:94-106` 无条件渲染重试按钮；后端 `task/service.py:144`：

```python
if TaskStatus(task.status) is not TaskStatus.FAILED:
    raise InvalidState("仅失败的任务可以重试")
```

于是对 `completed` / `running` / `queued` 的任务点「重新生成」，必定收到一条红色 toast。叠加 mock 不校验状态（FE-05），联调阶段完全发现不了。

修法：`disabled={task.status !== 'failed'}`，并把 disabled 的原因写进 `title`（「仅失败任务可重试」）。

### FE-07 默认时长取最大档

`StudioPage.tsx:47-51` 与 `CreateTaskModal.tsx:46-50` 都这么写：

```ts
return String(durationValues[durationValues.length - 1]);
```

`durationOptionsFor` 返回升序数组，所以取末位 = **最大档**。真实目录里 video 模型是 `{"min":5,"max":12,"default":5}`（`infra/ai/base.py:79`），用户不动下拉框就拿到 12s。

而 `ParamField` 类型里明明定义了 `default`（`entities/model/model/types.ts:11`），全仓零读取。这是「按秒计费」的产品里最不该有的默认值方向。

修法：`const fallback = schema?.default ?? durationValues[0]`。

### FE-08 兜底清单与真实目录不一致，且静默

`model.queries.ts:16-41` 的 `FALLBACK_MODELS` 写死 `aspectRatio.options: ['9:16','16:9','1:1']`，真实 echo 供应商只声明 `["9:16","16:9"]`（`base.py:80`）。

`useModels` 在**任何**失败（401 / 超时 / 5xx）时静默换成兜底，用户没有任何感知：

```ts
return items?.length ? items : FALLBACK_MODELS.filter(...)
```

后果：`/models` 抖动时，用户能选到后端不支持的 `1:1`，提交必被 `params.py` 拒绝，报错文案却指向「比例不支持」，看起来像用户操作错了。

修法：兜底只保留 `id`/`label`（参数能力留空 → 禁用提交 + 提示「模型目录不可用」），别用一份会过期的副本来冒充能力声明。

### FE-09 角标不用现成的轻量接口

`task.queries.ts:53-60`：

```ts
queryFn: () => taskApi.list({ page: 1, pageSize: 1, scope: 'mine' }),
select: (data) => data.stats,
refetchInterval: 5_000,
```

后端已经有 `GET /tasks/stats?scope=`（并且生成物 `EndpointData` 里就写着 `"GET /api/v1/tasks/stats": TaskStats`）。现在每个在线用户每 5 秒多拉一次列表封套 + 1 条任务，只为拿 5 个数字；且 `scope` 固定 `mine`，管理员切到「全部任务」时侧栏角标和列表头部对不上（这是 R3-03 的同类问题，后端修了、前端没跟）。

### FE-10 批量操作是假成功

`TaskCenterPage.tsx:109-115`：

```tsx
onCollect={() => toast(selected.size ? '已加入收藏' : '请先选择任务', ...)}
onRecycle={() => toast('回收站为空')}
onDownload={() => toast(`准备下载 ${selected.size} 个视频（待接入下载接口）`)}
```

前两个一个请求都不发，却给出「已加入收藏」这种完成态文案。而 `useDeleteTasks`（真正的删除 mutation）定义了、零调用，工具栏里连删除按钮都没有。

对比 `UsagePage`：它明确写「接口未接入」并置灰交互——同一套设计语言，两种做法。建议统一成后者：未接线的按钮 `disabled` + `title="接口待接入"`。

---

## 八、🟡 FE-11 可访问性三缺（jsx-a11y 覆盖不到）

`eslint-plugin-jsx-a11y` 已接入，静态可查的都过了。漏掉的是运行期语义：

| 位置 | 问题 | 后果 |
|---|---|---|
| `Toaster.tsx:14-25` | 容器无 `role="status"` / `aria-live="polite"` | 读屏用户收不到任何操作反馈（成功、失败全静默） |
| `Tabs.tsx:22-45` | 用 `<button>` 但无 `role="tab"` / `aria-selected` / `aria-controls` | 读屏无法知道当前选中的是哪个筛选项 |
| `Modal.tsx:29-52` | 只有初始 `focus()` 与 `Esc`，无焦点陷阱 | Tab 键能把焦点移到弹窗背后的页面，键盘用户会「走丢」 |

`Modal` 的滚动锁逻辑写得很好（引用计数 + `onClose` 用 ref 避免 effect 反复重跑，注释还解释了原因），说明作者意识到这类细节——焦点陷阱可以按同样思路补。

---

## 九、💭 FE-12 细节

- **选中项跨筛选残留**：`selected` 只在创建成功后清空，切换 status tab / 搜索时不清理。现在只影响 toast 里的计数，一旦接上批量删除就会**删掉看不见的任务**。建议在 `params` 变化时 `setSelected(new Set())`，或按 `items` 求交集渲染。
- **搜索框与 URL 单向同步**：`keywordInput` 只在初始化时读 URL，浏览器后退时输入框不回填（URL 变了、输入框还是旧值）。把 `keyword` 作为 `key` 或在 `keyword` 变化时同步 `setKeywordInput`。
- **头像按钮没有 onClick**：`TopBar.tsx:31-37` 是个 `<button>` 却什么都不做，看起来可点。要么接用户菜单，要么渲染成 `<span>`。
- **`RouteError` 直出 `error.message`**：生产会露出 `Failed to fetch dynamically imported module` 这类技术细节，建议按 `import.meta.env.PROD` 换成固定文案 + 上报。
- **ESLint 分层只拦 `@/` 别名**：配置注释里自己写明了「相对路径 `../../features/...` 仍可绕过」。建议加一条 `no-restricted-imports` 的 `patterns: ['../*']` 白名单校验，或干脆禁用跨层相对路径。
- **mock 数据里仍有后端不存在的模型**：`VITE_USE_MOCK=true` 时下拉框会出现 `Vidu Q1` / `Seedance 2.0` / `海螺 01`（`mocks/db.ts:20`），和在 mock 里练出一身错肌肉记忆。
- **`CoverImage` 的 `picsum.photos` 兜底**：mock 封面走公网图，内网/离线开发直接裂图（好在 `gradient.ts` 有渐变兜底，不至于破版）。

---

## 十、值得保留的做法

这轮前端有几处是**把约定变成机制**，比写文档强：

1. **注册表装配 + 注册期校验**（`shared/registry/create-registry.ts`）：模块 id 重复、路由重复、导航指向不存在的路由，全在启动时 `throw`，而不是运行期白屏。`navGroups` 的分组排序、`cards` 的 `seq` 兜底也都考虑到了。
2. **`import.meta.glob` 自动发现**（`app/registry/index.ts`）：`eager` 只加载清单，页面仍按路由分包——这个取舍是对的，注释也写清楚了「为什么可以 eager」。
3. **ESLint 把分层写成规则**（`eslint.config.js`）：`pages` 层禁 `http`、`modules` 层禁横向 import，并且注释说明了 `no-restricted-imports` 会被后一个 config 整体覆盖这个坑。这是真踩过的痕迹。
4. **`router.test.ts` 断言「每条页面路由都被 `RequirePermission` 包裹」**：不是测行为，是测**装配不变量**。这类测试能挡住「加了新页面忘了包守卫」。
5. **`Modal` 的滚动锁引用计数** + `onClose` 用 ref：多层弹窗叠加、父组件传内联箭头函数这两个真实坑都处理了，还写了原因。
6. **`mocks/kernel` 的适配器层拦截**：mock 不是 `if (isMock)` 散落在业务里，而是替换 axios adapter；路由表按注册顺序匹配，`/tasks/stats` 必须排在 `/tasks/{id}` 之前的顺序陷阱还留了注释。
7. **`use-collapsed-groups` 的降级**：`localStorage` 不可用时静默退化为内存态；全部展开时删 key 不写空数组。

另外有两处我特意验证过、**没有问题**，可以放心：

- `formatDateTime` 的时区：后端返回 `"2026-11-05T06:54:51.762751Z"`（6 位微秒 + Z），`new Date()` 在 V8 下能正确解析并转本地时区（实测 06:54Z → 14:54，+08:00）。
- `onlyAuthorized` 参数：前端 `Switch` 传的 `onlyAuthorized=true` 后端真的过滤了（实测 total 12 → 8），不是死控件。

---

## 十一、建议顺序

| # | 动作 | 预估 | 理由 |
|---|---|---|---|
| 1 | FE-01 接登录（页面 + `setSubject` + `/me` 回填 + 401 登出） | 2–3 h | 唯一阻塞上线的问题；后端接口已就绪，纯接线 |
| 2 | FE-02 错误态（列表页 `isError` + 全局 401 处理） | 1.5 h | 与 1 同一条链路，一起改只验一次 |
| 3 | FE-06 重试按钮判别状态 + FE-05 mock 三处对齐 | 40 min | 让「按钮能不能点」和 mock 行为同时变真 |
| 4 | FE-03 实体类型收敛到 `TaskOut` | 1 h | 越早改收益越大，之后每次后端改字段都免费获得检查 |
| 5 | FE-04 两个模块声明权限 + 关键按钮 `<Can>` | 1 h | 顺带把 `ForbiddenState` 这条路径跑通 |
| 6 | FE-07 / FE-08 / FE-09（默认值、兜底收紧、用 `/tasks/stats`） | 1 h | 都是小改动，直接影响费用与带宽 |
| 7 | FE-11 可访问性三缺 | 40 min | 可以和后端 R4-11 的权限改动并行 |

第 1、2 项建议同一个 PR：它们的验收标准是同一条——**未登录访问任意页面应看到登录页，令牌过期应回到登录页**，而不是看到「没有任务」。

---

## 十二、修复记录（2026-11-05 15:40 起，前端）

| 编号 | 状态 | 落点 |
|---|---|---|
| FE-01 🔴 生产无身份来源 | ✅ 已修 | `modules/auth` + `pages/login` + `entities/session`；`useLogin` 写 `signIn`，`useMe` 回填 `setSubject`，401 由 `http.ts` 清场 |
| FE-02 🔴 错误被吞 | ✅ 已修 | 新增 `shared/ui/ErrorState.tsx`（按状态段分类 + 重试）；接入任务中心 / 素材 / 模板 / 分镜 / 镜头 / 生图历史 6 个列表页 |
| FE-04 🟡 权限骨架零使用 | 🟡 部分 | `user-access` 菜单与路由声明 `identity:read`，`UsersSection`/`RolesSection` 用 `<Can>` 藏写操作；其余 8 个模块仍未声明，等后端补权限点 |
| 其余 | ⏳ 未动 | FE-03（`schema.ts` 零引用）、FE-05（mock 对齐）、FE-06..FE-12 |

FE-02 的两个细节（不改会以为数据丢了）：

- 失败时 `TaskSearchRow` 显示「共 — 条任务」、状态页签不显示数字、素材页显示「占用信息不可用」。
  之前请求失败与「真的是 0 条」在界面上完全一致，这是这条问题的真正危害。
- 分类复用 `errorHttpStatus`（`code // 100` 即 HTTP 状态）：401 → 重新登录、403 → 无权限、
  5xx → 服务端出错并展示后端原文、断网 → 连接不上服务。**不按完整业务码分支**。

顺带交付（原清单没有，但同一条链路上的空白）：

- **会话如何结束**：后端无 `/logout`、无 `/refresh`，因此不做静默续期，只做到点登出
  （`useSessionExpiryWatch`）+ 401 清场 + 跨标签页同步（`useSessionCrossTabSync`）；
  登出原因（expired / unauthorized / external）只用于登录页那句提示。
- **登录失败说人话**：401 / 403 / 429 / 超时 / 断网 / 5xx 分别措辞；429 时按钮按秒倒计时锁定，
  时长从后端文案解析，前端不硬编码「5 次、15 分钟」这些后端策略。
- **mock 补齐登录限流**（第 6 次 429），否则这条 UI 分支只能在真后端上碰运气复现。
