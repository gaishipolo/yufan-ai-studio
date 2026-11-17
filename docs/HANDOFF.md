# 交接文档 · 2026-11-11

> 给「新开的会话 / 下一个接手的人」：只写**现在是什么状态、下一步做什么、哪些坑不能再踩**。
> 结构看 [`ARCHITECTURE.md`](ARCHITECTURE.md)，怎么加模块/供应商看
> [`../api/docs/EXTENDING.md`](../api/docs/EXTENDING.md)，模型驱动看
> [`MODEL_DRIVER_STANDARD.md`](MODEL_DRIVER_STANDARD.md)。
>
> 维护方式：每次大改动后更新 §0 / §1 / §3。修完一条待办就删一条——这份文档的价值同样在于它短。

## 0. 一眼现状

| 项 | 值 |
|---|---|
| 仓库 / 分支 | `https://github.com/gaishipolo/yufan-ai-studio` · `main` |
| HEAD | 本地领先 `origin/main` **4 笔未推送**（`822a4cd` 计费模式进 paramSchema → `bfa03e1` 模板库计费模式 → `e6e2f91` 任务中心不再能创建任务 → `2ed4943` 参数全面由模型 paramSchema 驱动）；**推送前先问用户**。笔数以 `git rev-list --count origin/main..HEAD` 为准，hash 见 `git log --oneline origin/main..HEAD`。另有**未提交的前端视觉重构**（2026-11-16，见 §1）。未跟踪的只有 `var/`、临时脚本（如 `var_fix_fe.py`）、快捷方式 `.lnk`、`vite.config.ts.timestamp-*.mjs` 与用户私有的 `EXCHANGE_TOPICS_2026-11-12.md`，都不该进提交 |
| CI | 此前连续多轮全绿（含 `bc0940e`）；本轮推送后照 §4 核对。PG 迁移仍在观察期（`continue-on-error`） |
| 本机门禁 | **前端**（2026-11-16 视觉重构 + 资产库三件事）：`npm run verify` 全绿 —— typecheck 0 / lint 0 / **657 passed（83 文件）** / build ok。**后端**（同轮资产库改造）：`pytest -q` **1369 passed / 12 skipped** / ruff 0 / mypy 248 文件 0；契约四份已重导出 |
| 规模 | 11 个业务模块 · **95 条路径 / 125 个端点** · **53 个权限码** · **4 份**契约生成物（数字以 `app modules` 与 `api/docs/openapi.json` 为准，别再手抄） |
| 后端 | `api/.venv/Scripts/app.exe serve --reload` → 8080，前缀 **`/api/v1`**（裸 `/health` 会返回 40400） |
| 前端 | `cd studio && npm run dev` → 5173，`/api` 已代理到 8080 |

## 1. 这一轮（11-11）做了什么

### 1.0 前端视觉重构（2026-11-16，已提交并推送）

用户要求「用 frontend-design 技能优化前端视觉，避免不必要的辅助标签与说明，
让布局本身发挥解释作用」。**只动前端，后端与契约零变动。**

**三条硬规则**（写进 `docs/CODE_STYLE.md` §3.10，下次接手的人照着写）：

1. **字阶六级**：以前全仓 18 个字号、239 处挤在 10–11.5px，标题和说明肉眼分不出。
   现在 `micro 10 / meta 11 / xs 12 / body 13 / lead 15 / title 19 / display 26`，
   语义固定：**区块标题 `text-lead`，页面标题 `text-title`，正文 `text-body`**。
   任意值 `text-[13px]` 全仓清零（只留产品字标副行的 `text-[7px]`）。
2. **数据用等宽字**：时长 / 画幅 / 尺寸 / 进度 / 计数 / 时间码 / 存储键一律
   `font-mono` + `tabular-nums`，于是「哪条片子最长」一列扫过去就知道。
3. **页面不自己解释自己**：`PageHeader` 的 `description` prop **删了**（TS 拦得住）。
   10 个页面开头那段「这个页面是干什么的」全部删除 —— 页面里已经摆着筛选条、
   页签、卡片了，那句话是把看得见的东西又念一遍。
   只保留**用户猜不到又会被拒**的约束（如「名称唯一」「至少 8 位」），
   且贴在字段标签那一行的**右端**（`FormField` / 工作台 `Field` 的 `hint`）。

**结构件**：新增 `shared/ui/Panel.tsx`（`Panel` + `PanelHeader`），
收掉各页面手写的 `<section className="rounded-card border … p-3.5">` 与
自定义的 `SectionTitle`（那些标题前面还挂着**装饰圆点** —— 不编码任何东西，删了）。

**颜色各司其职**：`brand`（青）以前只当装饰圆点用，现在只表示**在飞**
（生成中 / 进度条头端）；`primary`（蓝）只表示交互；语义色只表示状态。

**签名元素只两处**：顶栏下沿那条胶片齿孔边（`.film-edge`）、画布那层极淡的方格。

**顺手修的两处真问题**：

- **工作台「分镜序列 · 自动拆解 · 4 个镜头」是在吹牛**：那个面板列的是
  `/shots` 镜头库的前 4 条运镜词条，跟用户这段话没有任何关系。改成「常用镜头 4」
  + 一条去镜头库的路，并把「运镜已编排」换成**真正能插进提示词的那句词条**。
- **「▶ 预览」pill 铺满 20 张卡** = 20 份重复说明。改成悬停才显形
  （和勾选框同一套交互语言）。

**顺手修的环境坑**：本机 Node v26 自带 Web Storage，没给 `--localstorage-file`
时它的 `localStorage` 是个 undefined 的全局，正好挡住 jsdom 的实现 ——
用到它的用例（侧栏折叠、登录 token）整片红，`window.localStorage.clear()` 直接炸。
在 `src/test/setup.ts` 补了个内存版（只在 jsdom 真没给时才装）。
**这不是设计改动，是为了让门禁能跑**；用 `git stash` 对照确认过是预先存在的问题。

**顺手修的真缺陷（用户报的）**：模板封装编辑模板时，提示词里打 `@` 弹出的
**候选槽位面板掉出视口** —— 面板写死朝下展开，而提示词框在右栏最下面，
候选条整块落到屏幕外，用户只看到输入框闪一下。修法：把 `MultiSelect` 里
已有的翻转头抽成共用 hook **`shared/hooks/useDropPlacement.ts`**
（量浮层自己的高度 + 上下哪边放得下，滚动时重算），两处都用；
tailwind 补 `animate-slideUp`（朝上弹时动画方向要对）。
真机实测：输入框底边 806 / 视口 860 → 面板翻到 442–576，完整可见。

**顺手修的第二个真缺陷（用户报的）**：`@` 提及在**英文后面失效** ——
`mention.ts` 的 `detectMention` 有一条「@ 前面挨着词字符就算邮箱地址」的启发式。
那是把冷门case（提示词正文里写邮箱）的防护开在了常用路径上：提示词里写英文
极常见（`cinematic handheld shot@` 想引槽位），写邮箱几乎见不到。
改成 **`looksLikeAddress()`：只看像不像地址**（`@` 后面已打出域名尾巴，
或 `@` 前面就是域名而后面还没写），其余一律当提及。
真机实测：`cinematic handheld shot@` 面板弹出；`a@b.com` / `site.com@` /
`a@b` 均不弹。

**模板封装编辑页按参考图改版**：右栏重排为「提示词 → 槽位速览 → 展示案例」；
新增「槽位速览」卡片（绿点=已固定、蓝点=用户填写）；分区色条统一主色
（颜色留给状态用）；槽位从「托盘上的卡片」改成「清单」（细线分隔）；
类型标记从文字 chip 改成图标（映射抽到 `features/template-specs/slotIcons.ts`，
清单与提示词 chip 共用）；「谁定」分段控件选中项改实心；展示名输入框平时
隐形边框；固定槽位单独一行 + 左边绿条 + 缩略图 + 「更换」；页头并进吸顶栏
（「← 模板封装 / 模板名」+ 按钮同一行，页头因此从 `SpecsPage` 挪进
`SpecWorkspace`）；提示词卡片加「字数 N / 20000」。
通用结论写进 `docs/CODE_STYLE.md` §3.10「控件形态：五个省力的默认」。
**没做**：参考图里提示词正文中的 `{{slot:xxx}}` 内联 chip 渲染 —— textarea 里
要做 overlay 精确复刻换行排版，脆且难维护。

**顺手修的第三个真缺陷（用户报的）**：模板库**右栏在滚动到顶部时被视口切掉 11px**
—— 用户原话「滚动到顶部时右侧栏会被遮住一点，页面整体向下滚动一点才能完全显示」。

根因不是吸顶坏了，是**高度上限写错了对象**：右栏上限写死
`xl:max-h-[calc(100vh-91px)]`，那个 91 是按**吸住之后**的位置算的（71 + 20）。
可页面刚打开时它还顶在「模板库」标题下面，**自然位置在 110**（比吸顶位置低 39px），
底边落在 110 + 760 = 871，超出 860 的视口 11px；往下滚一点它吸住了（71 + 760 = 831），
反而完整了 —— 于是「往下滚一点才看得全」。

修法与 `useDropPlacement` 同一个思路：**量，不写死**。新增共用 hook
**`shared/hooks/useStickyFitHeight.ts`** —— 临时摘掉 `sticky` 读出面板在文档流里
**本来的位置**，上限 = 视口高 − 自然位置 − 12。三个要点已写进
`docs/CODE_STYLE.md` §3.10「吸顶面板的高度上限：按它自己的位置算」：
上限恒定（面板不会在滚动中忽胖忽瘦）、普通流里不限高、**用回调 ref**——
右栏是等数据回来才挂的，只在组件挂载时量的话量到的还是空气（这个坑踩了一次：
第一版用 `useRef` + `useLayoutEffect`，量了个空气，上限一直是 `none`，bug 原样复现）。

真机实测（`var/rail-verify.mjs`）：1440×860 下 scrollY 0/20/80/400 底边余量
12/32/51/51，**任何滚动位置都不再被切**；1440×700、1280×800 同样余量 12；
窄屏 390 下不是吸顶元素 → 不限高，页面照常滚。

**顶栏下的模块宫格撤了**：用户要「顶栏下的快捷入口栏不要了」——那排大卡片与
**侧栏是同一批入口的第二份画法**，一屏两套导航，注意力被摊薄，内容区还白占
一整块高度。删 `widgets/module-nav/ModuleNav.tsx` + `AppShell` 的渲染，并**把
`card` 这个概念整套拆了**（它只为那排宫格而存在，留着就是零调用代码）：
`types.ts`（`routes[].card` / `ModuleCardDef` / `RouteCardDef` / `ResolvedCard` /
`AppRegistry.cards` / 插槽 `module-nav.trailing`）、`create-registry.ts` 的装配与
注册期校验、4 个模块清单的 `card` 块、`render-routes.ts` 的「宫格」列
（`docs/ROUTES.md` 已重新生成）、4 条只测宫格的用例。
**新增模块现在只声明 `routes[].nav` 一处展示信息。**

**门禁**：`npm run verify` 全绿 —— typecheck 0 / lint 0 / **657 passed（83 文件）** / build ok。
测试改动：`StudioPage.test.tsx` 的 `react-router-dom` mock 补 `Link`；
`ReferenceImages` 的提示文案改短后同步断言；`PromptBox.test.tsx` 新增浮层方向
与「英文后打 @」用例；`mention.test.ts` 新增「英文后打 @」用例
（判别力自检：还原修复后正好 3 条变红）；`SlotCard.test.tsx` 类型标记改成按
role=img + 名称查、固定槽位按钮区分「更换/选择素材」；`specDraft.test.ts` 新增
提示词上限的契约漂移守卫；`useStickyFitHeight.test.ts` 新增上限算法与回调 ref
时机 7 条（判别力自检：把算法还原成写死的 `100vh - 91`，正好 5 条变红）。
（比上一轮多 7 条：右栏上限那组；比再上一轮少 4 条：撤掉模块宫格时一起没了
4 条只测宫格的用例。）

**看效果**：内置 Browser 面板只有 639px，看不到桌面版排版。用
`var/shot-mock.mjs`（临时脚本，走 mock 模式 + puppeteer，1440 宽）逐页截图，
产物在 `var/shots/`。**下轮再看桌面版，直接用它，别再跟 639px 较劲。**

---

起点是一次全面只读审查（框架 / 资产库 / 模型启动，约 20k 行），产出 **P0×2 · P1×9 · P2×11**，
随后按「先写会失败的用例再改」逐条修完并推送：

| 提交 | 一句话 | 影响面 |
|---|---|---|
| `945f3bb` | `GenerationStore` 的写方法返回 `bool`，终态保护不再被调用方忽略 | 取消后不再出现「已取消 + 已成功」双写 |
| `8c9c62a` | 模型驱动门面按 `param_schema` 校验参数，按驱动 `spec.id` 落库 | 未声明参数由「静默失效」变 **400 + `availableParams`** |
| `2c66111` | 彻底删除的最终 `DELETE` 加 `rowcount==1` 闸门；回收站写冻结（`40088`） | 与 restore 并发时不再「恢复成功但内容已删」 |
| `2089a67` | `register()` 先全量校验再落库；`TRUSTED_PROXIES` 拒绝 `/0`；停机先等调度器再 cancel | 注册失败不留半套；`0.0.0.0/0` 直接启动失败 |
| `bd8cde6` | 限流桶 key 带「额度@窗口」；事件优先级真正生效（组间串行、组内并发） | 同 scope 不同额度不再共用一个桶 |
| `783f2b4` | 打包下载前重判可见性；`_stage` 改 `iter_chunks()` 分块落盘 | 失权的人拿不到字节；不再整对象进内存 |
| `0b539cc` | 三个 fail-closed 注入口缺失都报 FAIL | `app doctor` 不再漏报 |
| `c391559` | 替换档案注入口改回「注册」语义（去掉 `replace=True`） | 与 ports 约定一致 |
| `9bcf692` | 指标超限丢弃的样本可观测（`app_metrics_series_dropped_total`）；HTTP 计数器上限按 140 路由给足 | 丢样本不再静默 |
| `b8f9302` | 彻底删除按 `storage_key` / `cover_key` 数引用（不再按 sha256） | 不再留永久孤儿 blob |
| `70b379e` | 打包远端素材分块写盘 | 大对象不再全量进内存 |
| `f03890d` | 供应商解析收进 `ModelRouter.provider_for()`，删掉 `getattr(pipeline,"resolve_provider")` 后门 | 流水线只能用 `strict_model=True` 关兜底 |
| `4fcd9a5` | 把 CI 上一直红的 **mypy 11 处**修绿 | 类型门禁从「接入即红」变有效 |
| `ae5cf73` | 测试环境 scrypt `2**14 → 2**10` | 全量 235~289s → 约 200s；登录 150ms → 22ms |
| `17b3813` | 事件循环用例按**观察窗口**定工作量，不再按轮数定额 | 快机器上不再「窗口太短」假红 |
| `e521df9` | 新增本交接文档 + 修正 6 处过期口径 | 新会话不用重新摸现状 |
| 本轮 | **`bailian` 迁进 `drivers/`**（T32，`a18e927`）：新增可选协议 `InputPreparer` + `core/prompt_refs.py`，替换链改为「从路由拿实例、按类型判断」 | 百炼不再靠 `AI_PROVIDERS` 加载；未配密钥在选渠道时就说清 |
| 本轮 | **模型供应商整体迁移**（2026-11-13）：废弃 `AI_PROVIDERS` 白名单；`echo` → `drivers/echo.py`，`libtv` 执行器（`executor.py` / `process.py` / `state.py` / `runtime/`）→ `modules/model_driver/libtv/`；模板库经 `core/ports/model_generation` 的 `libtv_paths()` / `libtv_execute_stage()` 问执行器 | 只剩 `drivers/` 一条接入路径；`core/config.py` 白名单字段、`.env` 的 `AI_PROVIDERS`、`infra/ai/<name>.py` 全删；子进程入口改成 `-m modules.model_driver.libtv.process` |
| 本轮 | **三个替换模型通用化**（2026-11-13）：取消 `internal=True` 进 `/models`；新增 `modules/model_driver/generic_inputs.py` 把 `/tasks` 的 `prompt + referenceKeys + params` 物化成替换链同款归档工作区；LibTV 通用链在提交前补跑 `upload` 阶段 | 生成工作台可以直接选百炼/LibTV 替换模型；`maxReferences=0` 的模型带参考素材当场 400，不再静默丢弃；`tests/test_generic_inputs.py` 8 条钉住档案形状与幂等 |
| `cfc0508` | `ModelSpec.internal` + `ModelRouter.public_models()`：替换链专用模型不再出现在目录（T33，后被本轮取代） | 当时视频下拉里不再有选中即 400 的 `*-replacement` |
| 本轮 | **任务中心接生图 + 补齐自身真实操作**（2026-11-13）：`GET /tasks/feed` 从「视频 + 替换档案」扩成三路；新增 `core.ports.generation_feed` 注入口，生图模块在 import 期注册 `feed_provider.read_generation_feed`；前端加 `ImageTaskCard` / `ImagePreviewModal` 与来源 `source=image` | 生图记录进任务中心，统计 / 搜索 / 状态分组 / 现签产物地址三路同口径；批量下载、显式 `PATCH` 收藏、`DELETE /tasks` 删除（回 `skipped`）、视频 / 替换成片下载从假按钮变真实操作。**用户明确「v2 先不用管」，本轮完全没碰模板库 v2** |
| 本轮 | **模板封装迁移 A/B/C1**（2026-11-14）：A 纯规则上移 `core/template_rules/`；B `manifest.json` DB 化四张表（迁移 `a3c7e91b2d45`）；C1 新 `/template-manifests` 配置服务（预检 / 幂等保存 / 发布 / 停用）+ `core.ports.template_catalog` 只读端口 | 素材一律 `library_assets.id`，本地文件、文件 sha256、journal 进度文件、HTTP 客户端整套没搬；`template_packager` 的能力改为 DB 内预检 + 版本回执。旧 `/template-packs` 与 `modules/template` 的 ①② 暂留，等 C2 收敛 |
| 本轮 | **C1.5 发布回执闭环**：`publish()` 冻结 `published_pack_id` / `published_pack_version` / `published_template_id`，`template-catalog` 快照带出回执；复用包 `packId` 预检改成查库确认「存在且已发布」 | 模型里三列此前声明了却从未写入（journal 语义断在这里）。新建包回执指向自己 + 本次 version，复用包指向被复用行及其版本；停用不清空（下架 ≠ 抹历史）。发布投影（`video_templates` 行）仍等 C2 搬表后同模块直写 |
| 本轮 | **C2 第一刀（绞杀者）**：新增 `core.ports.template_runtime`（`template-runtime` 注入口，实现暂由仍持有表的 `modules/template/runtime_port_impl.py` 注册）；`replacement_service` / `authorization` / 固定引用解析全部改走端口，执行链不再 import `PackRepository` / `video_template_service` / `TemplatePackVersion` | 目的是让「搬表」变成纯机械动作：C2 正刀只需挪文件 + 换注册点，消费方一行不改。执行链的模板名缓存从进程级全局字典（`video_template_service._NAME_CACHE`，已删）改为**每请求一份** `self._template_names`，`_prompt_origin` 改成纯函数、显式收名字表。目录卡片仍走 camelCase dict（封装模块自己的对外形状），消费方 `ReplacementPack.model_validate` 校验一次 |
| 本轮 | **C2 正刀：封装整体搬进 `template_pack`**（2026-11-14）：`template_packs` / `_references` / `_versions` / `video_templates` 四表、`pack_service` / `pack_router` / `video_template_*` / `template_references` / `library_references` / `seed` / `runtime_port_impl` 全部迁入；`models` / `repository` / `schema` / `dependencies` 按表拆分；旧 `template_pack_templates` / `_slots` 与 `/template-packs` 端点、前端「封装模板」页面一并下线（迁移 `c6f0a2d7e813`） | 路径与权限码一个没改（`/templates/replacement-packs`、`/templates/video-templates`、`template:pack:*`）；`asset_source.py` 上移 `core/library_assets.py`、`ReplacementPack` 形状上移 `core/template_pack_view.py`、老 JSON 读盘约定上移 `core/legacy_json.py`；`import-template-legacy` 拆成 `import-template-packs` + `import-template-runs`。**但发布投影仍未做**（见下条） |

| 本轮 | **发布投影 + `packId` 收口到运行行**（2026-11-14）：`publish()` 现在真的把配置投影成
  `template_packs`（head / 参考图 / 冻结版本）+ `video_templates`；`packId` 改指已发布的
  **运行素材包**（`template_packs.id`），校验同时要求冻结版本行存在 | 包 id 与模板 key
  沿用离线 packager 的 `uuid5` 派生（`pack:<key>` / `ai_{ns.hex}_{fn}`），否则 CLI 时期
  已发布的模板会重复；回执 `published_pack_id` / `published_template_id` 与只读目录端口
  现在都指向运行行。投影**不是单事务**（复用 `PackService` / `create` 的既有提交语义），
  按 key 幂等、失败可重跑。`test_publish_projects_into_runtime_tables` 走对外接口断言 |
| 本轮 | **D：离线打包器下线**（2026-11-14）：删 `api/src/template_packager`（6 文件）、
  `scripts/package_video_template.py`、`scripts/` 里的示例素材目录、
  `core.config.template_packager_state_dir` 与 `health.py` 的进度目录自检；
  `AI_TEMPLATE_PACKAGER*.md` 改成 `/template-manifests` 三步接口（预检 → 保存 → 发布） |
  入口只剩 API：配置存 DB、进度靠 `fingerprint`/`revision` 幂等、回执指运行行。
  测试：删掉 3 个 CLI 用例文件（37 条），`test_scene_text_template` 改写成走
  `/template-manifests` 的端到端（发布出的模板直接喂 `/replacement-runs`），
  `test_template_catalog_storage` 去掉 state-dir 那条。**整条迁移到此收口** |

| 本轮 | **模板封装页面 + 四项补齐**（2026-11-14）：新增 `/templates/manifests`（侧边栏「模板封装」，与「模板库」并列的第二条路由，守卫 `template_pack:read`）—— 新建/编辑配置、选原片与参考图（复用 `/library/selection`，不另造素材目录）、素材包可新建或复用已发布包、写功能提示词、预检 / 保存草稿 / 保存并发布，发布回执给可点的工作台深链（`/templates?sourceId=&videoTemplateId=&kind=`，工作台本来就按这三个参数还原选择） | 按钮按 `template_pack:create` / `template_pack:publish` 隐藏（只读用户仍能看列表）；参考图与功能块可拖动改序，**顺序就是契约**：参考图次序决定 `{{Mixed N}}` 编号、也就是 `mediaOrder`，功能次序决定卡片排列；每张参考图可填逐图说明，投影时落到素材包绑定的 `description`。页面上不写操作指引，缺什么由服务端预检逐条报 |
| 本轮 | **真机实测封装链（用资产库现有素材）**（2026-11-14）：连本地 PG + MinIO 起后端，用 `我穿的是米兰之夜.mp4`（6.0s、含音轨）+ 6036 系列两张产品图新建素材包、复用已发布人物包，走完 预检 → 保存 → 发布 → 投影 → 准备替换：`/replacement-runs` 返回 `prepared`、`promptMode=fixed`、`inputSummary` 三张输入各带 `usage`（逐图说明拼在用途说明之后）。实测揪出一个真缺陷：回执 `workbenchPath` 的 `kind` 用的是 `function.kind or "product"`，人物包被说成产品包 —— 改为与幂等投影同源的 `_kind_of()`（scene 看 `scenePromptDefault`，其余看素材包 kind），补 `test_workbench_path_kind_follows_pack_not_function` | 另一个「疑似缺陷」核实**不是**：模板 `rules.scopeOptions` 为空时工作台不显示范围控件（`showScope = boundTemplate ? availableScopes.length > 0 : true`），后端空对空直接放行；我第一次 curl 手填了`worn` 才撞 400，是调用方式错。**别给后端加「空即回落素材包范围」的兼容** —— 会破坏 `selectable_scope=False` 那组用例的语义 |
| 本轮 | **模板库填表页（P2）**（2026-11-14）：新增 `/templates/library`「模板库」——左列已发布模板卡片、右栏就地填表（`fixed` 只读缩略图 / `user` 走素材或封装选择器 / 固定段灰显不可编辑 / 可编辑段 textarea），提交 = `compile` → 字段摊平 → `POST /tasks`；原 `/templates` 的导航名改成「替换工作台」以免与新「模板库」混淆（P3 它下线） | 页面无本地规则：数量上下限、可读性、编号展开仍在服务端。两道防"静默丢"：`params` 里出现 `/tasks` 表达不了的键（`size`/`count`）**报错不丢**；时长缺省取模型默认档、两处都没有才报错，不编 5 秒。`compile` 新增 `promptOverride`（只改 `editable` 段；`locked` 永不可改；模板 EDITABLE 为空时 400；写 `{{Mixed N}}` 也 400）。真实素材实测：固定原片 + 3030粉 封装 → `{{Mixed 1..4}}`、任务 `queued`（6 秒 / 9:16 / 4 张参考） |
| 本轮 | **P2 功能收尾**（2026-11-14）：① 五种素材形状逐条真跑（纯提示词 0 槽 / 多图 2 张 / 图+视频 / 图+音频附件 / 图+文本），每条 `compile → 摊平 → POST /tasks` 均 201，跑完删任务；② 补掉"长提示词晚失败"：新增 `EXECUTION_PROMPT_LIMIT=2000`（= `CreateTaskIn.prompt` 的 `max_length`），草稿不判、预检/发布粗判、`compile` 按展开后精判 | 两个上限不是一回事：`MAX_PROMPT=20000` 是编写上限（旧模板有 2 万字级提示词，P3 迁移要能先存成草稿），`EXECUTION_PROMPT_LIMIT=2000` 才是执行上限。漂移由 `test_execution_prompt_limit_matches_task_schema` 直接读 `CreateTaskIn` 的 `MaxLen` 钉住。填表页顺带修两处渲染：纯提示词模板不再渲染空的「填入素材」区、无引用槽位时不显示编号说明 |
| 本轮 | **P3 第一刀：替换能力改由模型声明**（2026-11-14）：`ModelSpec.replacement` （驱动声明，三个 `*-replacement` 为 true）→ `ModelSummary` → `/models` 与 `/model-driver/models` 出参；`GET /template-specs/published?target=replacement|generation|all` 按它筛（只 join 当前发布版本） | **判据不许从槽位组合推断**："有 fixed video 槽 + 有 pack 槽就是替换模板"会把普通多图模板误判，然后在替换链路里以「没有绑定档案」失败。实测 6 个模板：replacement 5 / generation 1，分得干净。这一刀不动表、不动替换链 —— 下一步才是 runtime 端口读 spec |
| 本轮 | **P3 迁移：旧模板 → spec 草稿**（2026-11-14）：新增 `scripts/migrate_templates_to_specs.py`（默认只读预览、`--apply` 才写、`--model` 必填且须声明 `replacement`）；旧素材包先落成资产库封装（`kind=pack`，position/role/description 原样），spec 用**一个** `source=pack, binding=fixed` 槽位引用 | 三处踩过的坑都写进注释：① 派生 key **不能粗暴截断**（`ai_<hex>_<fn>` 截 40 位会把函数名切掉，同一 manifest 的功能算出同一个 key）；② 旧提示词的 `{{Mixed N}}` 要**逐个**翻成 `{{slot:views.j}}`，整组 `*` 会把"以某一张为准"改成"全都要"，找不到落点就整条跳过；③ 同一轮里多个模板指向同一个旧包时，plan 阶段都还没建封装，要按名字记一份本轮的账，否则会建两份。实测 3 条旧模板：迁移→发布→compile，提示词与素材顺序与旧模板逐字/逐项相同；重跑全部跳过 |
| 本轮 | **执行层提示词上限 2000 → 7000**（2026-11-16）：真实模板（`spec-cxxbvz` 芳华里你们看-精致妈妈）提示词 2158 字被发布预检挡下，改了 `CreateTaskIn.prompt` 的 `max_length` 与 `EXECUTION_PROMPT_LIMIT` 两处（漂移守卫原样生效），重生成 `openapi.json`；`API.md` / `schema.ts` / `permissions.ts` 无变化 | 2000 不是供应商给的值：`bailian` 驱动放到 20000，`libtv` 完全不校验，2000 只是初始提交里 `CreateTaskIn` 的入参约束。`GenerateImageIn.prompt`（生图端点）仍是 2000 —— 模板封装不走它，别顺手一起改。实测：2158 字过预检 `valid=true`，7001 字仍 422 `String should have at most 7000 characters` |

| 本轮 | **P3 收尾：替换链整体下线**（2026-11-14）：删 `modules/template`（执行链 / 物化 / 归档工作区 / 运维命令 / `replacement_runtime`）、`template_manifests` 四表、`template_packs*`、`video_templates`、`replacement_runs*`；四个跨模块契约（`core.feed`、`template_runtime` 端口、`template_catalog` 端口、`template_pack_view`）与 `core/template_rules` 一并删除；迁移 `b8e4c1f90d27`（11 张表，**不可降级**） | 「接读」没做，改成直接下线 —— 终局本来就不留独立工作台，先接读等于给要拆的桥加固。任务中心从三路变两路（视频 / 生图）；前端只剩 `/templates/library`（填表）与 `/templates/specs`（封装，导航名改成「模板封装」）。历史调用档案按用户拍板**直接删**（开发库 49 行，升级前 `pg_dump` 备份在 `api/var/db-backup-before-p3-*.dump`）。守卫没丢：编号等价性改成对照冻结常量、`test_user_retry_exits` 去掉 `template_replacement` kind、`test_generation` 不再有 `fail_on_model_gone=False` 的例外 |

| **模板只剩 `template_specs` 一套表**：`video_templates` / `template_packs*` / `template_manifests*` / `replacement_runs*` 已删 | 前端/脚本/运维 | 想拿「某模板要什么素材」读 `GET /template-specs/published`；提交走 `compile` → `POST /tasks`。旧的 `/templates/replacement-*`、`/templates/video-templates`、`/template-manifests` 端点全部 404 |
| 权限码 `template:read/use/review`、`template:pack:manage/publish` **已删** | 角色配置 | 填表用 `template_pack:read`，封装用 `template_pack:create/publish`。内置角色已改（viewer/editor 只读、asset_specialist 可封装）；自建角色里若还留着旧码，会显示成「无法识别」 |
| `GET /tasks/feed` 的 `source` 只剩 `all` / `video` / `image` | 前端/脚本 | `source=replacement` 会 422；替换任务就是 `tasks` 里的一行 |

| 本轮 | **提示词合成一段 + 展示案例 + 回收站**（2026-11-14）：`prompt_locked`/`prompt_editable` → 单个 `prompt`（迁移 `c9a1f4e27b53`，草稿 JSON 一并改写）；`compile` 去掉 `promptOverride`，文本槽位改出 `{{Text N}}` 并进 `textLibraryIds`；新增 `demoAssetId`（只存草稿、不进快照）＋模板库卡片/详情展示，没设显示「暂无案例」；规格删除改软删并加回收站（`DELETE` / `GET /trash` / `POST /{key}/restore`） | 用户原话「提示词不用区分固定段和可编辑段，要可变通过文本插槽实现」—— 两段的存在理由只有"模板库改 editable"，入口一取消就没有区别了。**改类型时槽位 key 只在没被引用过时跟着改名**（引用过改名会让 `{{slot:…}}` 静默失效）。封装页 key 全部自动生成、界面不显示 |

| `TemplateSpec.prompt` 由 `{locked, editable}` 变成**一段字符串** | 前端/脚本 | 迁移已把两段用空行拼好；`promptOverride` 一并删除，传它会 422 |
| `compile` 出参里文本槽位的 token 是 `{{Text N}}` | 前端/脚本 | 与 `textLibraryIds` 顺序一一对应；`audio` 仍然不可引用 |
| 新增 `demoAssetId`（模板出参） | 前端 | 展示案例，图或视频；**不进发布快照**，改它不用发新版 |
| `DELETE /template-specs/{key}` 是软删 | 前端/运维 | 进回收站，`GET /trash` 列出、`POST /{key}/restore` 恢复；资产引用照旧算（不按 `deleted_at` 过滤） |

| 本轮 | **模板库支持图片模板**（2026-11-14）：提交按模型能力分流，`video` → `POST /tasks`、`image` → `POST /generations/image`；`taskPayload.ts` 分成 `toTaskPayload` / `toImagePayload` / `toSubmitPayload`；右栏「参数」改为按模型 `paramSchema` 渲染（图片显示尺寸/张数），按钮文案分「生成图片 / 生成视频」 | 起因：`简单测试-图片` 在模板库点生成报「这些参数无法提交给通用生成任务：count」—— 封装页能选图片模型，填表页却只发 `/tasks`（按 `Capability.VIDEO` 解析）。后端一行未改，两个接口本来都支持 `textLibraryIds` |

| 本轮 | **占位符在界面上换成真实数据 + 生成产物按「用户名-时间」命名**（2026-11-14）：新增 `PromptRefs`（`entities/library/ui`），模板库/任务中心/资产库的提示词里 `{{slot:key}}` / `{{Text N}}` / `{{Mixed N}}` 就地渲染成正文或与文字同高的缩略图，模板库未选位置给「选择」按钮；后端 `ImageFeedItem` 补两个 id（只读投影，守卫豁免）、视频入库快照存注入后的提示词、生图作业名用注入后的文案；产物名改 `core/naming.py`（`用户名-年-月-日-时-分-秒`），前端下载同步 `format.generatedFileName` | 封装页**保持**显示 `{{slot:key}}`（作者要看见引用）。解析不到就保留占位符，不静默吞。命名按本地时区：库里存 UTC，直接格式化会让东八区用户看到早八小时 |

| 本轮 | **封装进类型筛选，删掉自动打的「封装」标签**（2026-11-14，迁移 `f4b8d2a61c07`）：资产库类型筛选多一档「封装」（前端 `KIND_OPTIONS` + URL 白名单、后端 `kind=pack` 本来就收）；`tags.PACK_TAG_NAME` / `ensure_tag` 删除，建封装只继承成员标签；工具栏的建封装按钮改名「新建封装」；资产选择抽屉不列「封装」类别 | 「这是个封装」是 `kind` 的事实，不是用户词表里的词：标签可被 × 掉，× 完这个封装在「图片 / 视频 / 音频 / 文本」里全都筛不到，只剩「全部」能翻到 —— 而它正是那些素材唯一的容器。改前 40 条里有 6 条只能靠「全部」翻。现在四类相加正好等于「全部」（有守卫钉） |

| `library_tags` 里不再有系统建的「封装」词 | 运维/前端 | 迁移删了它与绑定；封装的标签只剩「继承成员」与用户手选的那些。别再按标签找封装，用 `?kind=pack` |
| `GET /library/assets?kind=` 实际支持 5 类 | 前端/脚本 | `video/image/audio/text/pack`；`all` 不是合法值（前端省略参数表示全部），传它会 422 |

| 本轮 | **槽位「要什么」合成一根轴**（2026-11-14，迁移 `a7d3f0c85e21`）：`type` 收编 `pack`（资产库的 kind 就是槽位类型，含封装）、`source` 整个删掉（`InputSlot.is_pack` 唯一判据）、新增 `tag_id`（一级标签 = 候选集范围）；前端「类型」下拉与「加槽位」按钮都从 `KIND_OPTIONS` 生成、标签用 `KIND_LABEL`（不再写死「视频」而库叫「成片」）；「来源」换成「一级标签」，`AssetPicker.tagIds` 同时供封装页固定选择与模板库填写 | 两根轴表达同一件事时接口能收到 `type=image, source=pack` 这种自相矛盾的组合，而每个 `if slot.source == "pack"` 都得再回答「那 type 算什么」。`tag_id` 只是候选集：`compile` 不看它，标签改名或被删，已发布版本照旧能编译。旧字段 `source` 传了**当场 422**（不静默忽略）；草稿 JSON 也在迁移里改写（漏了那份草稿一打开就 422） |

| 槽位契约：`type` 多了 `"pack"`，`source` **已删**，新增 `tagId` | 前端/脚本 | 封装槽位就是 `type=pack`；再传 `source` 会 422。`tagId` 是候选集范围（一级标签，含其下级），不参与编译 |
| `GET /library/selection` 的 `tagIds` 现在有两处调用方 | 前端 | 封装页挑固定素材、模板库填写，都按槽位的 `tagId` 收窄 —— 两处看到同一批素材 |

| 本轮 | **模板库素材填写改为按一级标签分组**（2026-11-14）：`SlotMediaPicker` 把每个要让用户填的图片/视频槽位渲染成一块——标题 `一级标签 · 槽位名`，下面一行二级标签（**只有子标签、没有「全部」**；点了哪一档才拉哪一批素材，未点不列也不发请求），点素材即选中；无子级的一级标签不出那一行、直接列一级标签下的素材；文本/音频/封装仍走各自选择器。配套**发布闸门**：要让用户填的图片/视频槽位必须有 `tagId`，否则发布 400 并指名槽位 | 没有标签时那段只能退化成"全部素材"，素材一多就没法挑。胶囊不显示计数：标签接口是「列表口径」（封装成员不单列），这里是「选择器口径」（成员也要能挑），数字对不上就只放右上角「共 N」 |

| 变化 | 谁受影响 | 说明 |
|---|---|---|
| 未在 `param_schema` 声明的参数 → **400** + `availableParams` | 前端/脚本 | 前端参数区本来就由 `GET /models` 渲染，无需改动；手写参数的地方会立刻看到 400 |
| 回收站里的条目改名 / 换标签 / 改成员 → **40088** | 前端 | 要先恢复再改（「删除」的语义是冻结） |
| `TRUSTED_PROXIES=0.0.0.0/0` → **启动失败** | 运维 | 等于信任任意伪造 `X-Forwarded-For` |
| agnes 提交预算 **120s → 210s**（驱动声明 180 + 余量 30） | 运维/上游 | 以前上游慢于 120s 会被框架取消并判「提交结果未知」 |
| `APP_ENV=test` 时 scrypt 用 `2**10`，其他环境仍 `2**14` | 测试 | 参数编码在哈希串里，存量口令不用迁移 |
| 指标超限丢弃可在 `/metrics` 看到 `app_metrics_series_dropped_total{metric}` | 运维 | 以前是静默丢 |
| `.env` 里不再有 `AI_PROVIDERS`：模型供应商全部由 `drivers/` 自动扫描载入，没有启用清单 | 运维 | 删掉那一行；只需留 `BAILIAN_API_KEY` / `AGNES_API_KEY` 等私有配置 |
| 百炼渠道可用性改由驱动回答（`InputPreparer.unavailable_reason`） | 前端/运维 | 没配密钥时渠道目录返回「服务端尚未配置百炼密钥」，文案与以前一致 |
| `GET /models` 不再返回 `internal=True` / `placeholder=True` 的模型 | 前端/脚本 | 过滤仍在目录层；按 id 解析不受影响（`public_models()` 对 `models()`） |
| **三个替换模型已公开**（`bailian-wan3-*` / `libtv-wan3-replacement`，2026-11-13） | 前端/脚本 | 取消 `internal=True`，带 `paramSchema` 与 `maxReferences` 进 `/models`；通用 `/tasks` 会把 `referenceLibraryIds` 冻结成 `referenceKeys` 并物化成替换链同款归档工作区。`maxReferences=0` 的模型带参考素材会当场 400，不再「收下再静默丢掉」 |
| `drivers/` 里**文件名带 `_` 前缀的驱动不自动载入** | 驱动开发者 | `_mock_async.py` / `_mock_sync.py` / `_template.py` 都不进模型目录、不进 `app drivers list`、不出现在用户可见的下拉里。需要离线联调时显式调 `registry.register_example_drivers()` |
| 资产库多了 `kind=text`：`POST /library/text-assets` + `/assets/{id}/text{,/versions,/restore,/raw}` | 前端/脚本 | 只收 `.txt`/`.md`、UTF-8、≤1MB；`coverUrl` 恒为 null，卡片改画 `textExcerpt`。老资产读到 `textExcerpt/textVersion/textUrl` 分别是 null / 0 / null |
| 生图 `GenerateImageIn`、视频 `CreateTaskIn`、替换 `PrepareReplacement` 各多 `textLibraryIds` + 出参 `textBindings` | 前端/脚本 | 提示词里写 `{{Text N}}`，提交时冻结正文注入；出参只回「哪几份、第几版」不回正文。替换工作台只认 `{{Text N}}`，别的手写花括号会被前端拦 |
| 上传接口 `kind=text` | 前端/脚本 | 请求体上限从 300MB 降到 **1MB**（`LibraryTextTooLarge` 413）；非 UTF-8 一律 400，不做替换字符兜底 |
| `library_assets` + 三张生成表各加列、新增 `library_text_versions`（迁移 `e2b7c4a9f310`） | 运维 | 存量行靠 `server_default` 天然是「没有文本引用」，**无需回填**；purge 由 `lifecycle._purge_row` 显式删版本行 |
| `GET /tasks/feed` 的 `source` 多 `image`；`TaskFeedItem` 多 `image` 投影与 `outputUrl` | 前端/脚本 | 三路分别鉴权（`task:read` / `image:read` / `template:read`），`statsOnly` 只回统计与 `total`；产物 URL 在出参现签。无 `image:read` 或未注册 `generation-feed` 时这一路不出现，另外两路照常 |
| 生图 `succeeded` 但成图不在 → 分组 `attention`（不是 `completed`） | 前端 | 与替换档案同款口径，避免「已完成却点不出图」；卡片显示「成图待恢复」 |
| 任务中心批量收藏改 `PATCH /tasks/{id}` 显式 `{favorited:true}` | 前端 | 不用 toggle 的 `POST /favorite`（点两次会取消收藏）；批量删除走 `DELETE /tasks` 并如实显示 `skipped`。两类操作只作用于勾选的通用视频任务 |
| 下载走 feed 已现签的 `outputUrl`，不新增后端下载端点 | 前端 | `shared/lib/download.ts::downloadUrl` 用 `<a download>` 直连，避开对象存储 / CDN 的 CORS；跨源时浏览器忽略 `download` 但仍能打开产物 |

### 1.1 资产库：取消素材封装 + 标签树三级化 + 按代码审查报告修 27 条（2026-11-16，已提交）

用户一次性提了三件事：**增加三级标签**、**取消素材封装功能**、**按代码审查报告修**。
审查报告本身在 `docs/LIBRARY_REVIEW_2026-11-16.md`（前端 18 条 + 后端 17 条，
逐条给了触发路径与修复方向）。

#### 取消素材封装（`refactor(library)`）

封装(pack)是把若干素材打成一个「整体」的容器：一行 `kind=pack` 的资产行（没有自己的
字节）+ `library_pack_items` 的有序成员。撤掉它的理由不是不好用，而是**它让「一条素材」
不再是唯一的事实** —— 同一个成员既在个人库里、又在封装里，于是长出了三样只服务它的东西：
列表要决定「成员不再单列」（`hidden_by_pack_clause`）、下载要把封装再展开一次、
引用检查要多一个来源。

- 后端：删 `packs.py`、`library_pack_items` 模型与表、四个 `/library/packs` 端点、
  可见性里「封装成员可读」那一支、下载里的「封装展开成成员」、标签继承、
  三个封装错误码、`sync-pack-tags` 命令、引用来源里的封装那一条
- 契约：`LibraryAssetKind` / `origin` / `BulkAction` 都不再有 `pack`，详情出参不再有
  `packItems`，四份生成物重新导出
- 模板封装：槽位类型只剩 image / video / audio / text（「一个槽位绑一整个封装」靠的就是
  资产库的封装）
- 前端：封装弹窗、封装 chip、提示词里的封装缩略图、工具栏的「新建封装」、批量栏的
  「封装」、类型筛选里的「封装」档，以及 mock 里那套封装数据
- 迁移 `c3d8e1f04a7b`：删 `kind='pack'` 的资产行（连带标签绑定、共享申请单、下载任务里的
  引用）、删 `library_pack_items`、删 `template_spec_slots.pack_id`，并把模板里的封装槽位
  改成空的图片槽位（留着会直接非法，模板连编译都过不了）

#### 标签树三级化（`feat(library)`）

- 上限 `MAX_TAG_DEPTH=3`，且**整棵子树**一起算：挂过去后最深那层会超三层就拒
  （挂到自己的下级下面也拦 —— 那是环）
- 三处口径同一套：打标拒「有子标签的标签」（与层数无关）、筛选展开「自身 + 全部下级」、
  计数 = 自身 + 全部下级（按资产 id 去重）
- 出参两处变了：`depth` 是算出来的真实层数；`parentName` 是**父的整条链**（「产品 / 3030」）
- 前端：标签栏两行 → 三行，打标选择器按整条链分组，词表管理能显示三层，悬浮栏补第三行

#### 按审查报告修（前端 15 条 + 后端 12 条）

后端最容易漏的两类：**跨状态复核缺失**（共享审批通过时不看资产当前状态与可见性 →
回收站里的条目被改成 team 可见、或旧的组级申请单把团队共享抹掉）与**异常路径的资源清理**
（下载 GC 先清 key 再删字节、删失败被吞 → zip 永久泄漏；入库整份读进内存 → 300MB × 并发）。

后端两条**判断题**记在这里：B1（生成入库端点接受任意 `storageKey`）没有把字段从
payload 里拿掉（那要动契约），改成「物化前按 key 反查资产行校验归属」；B5（入库幂等键
没按人隔离）没有改键的格式（会打断生成侧按前缀查与存量行），改成 owner 过滤 + 409。

**没做的**：F16（每张案例封面各发一次详情请求）需要改模板契约，本轮没做。

### 1.2 模板封装：模型可选 + 槽位标签任意组合 + 提示词占位显示插槽名（2026-11-17，已提交）

用户提了四件事，其中三件是功能、一件是检查。

**模型也可选**：作者能声明「用这个模板时允许选哪些模型」（`allowedModels`，最多 10 条，
每款都要过参数/槽位那两道闸）。清单随**发布快照**走 —— 改完要重新发版，使用者才看到
新的可选项（与 `model` 同样的冻结口径）。模板库的使用面板在清单非空时给一个只含清单内
模型的下拉；挑了不在清单里的模型直接 400，**不静默换回固定模型**。迁移
`f3a8c1d5e902` 给 `template_spec_versions` 加 `allowed_models` JSON 列。

**槽位标签任意组合**：槽位从**一个** `tagId` 改成**一组** `tagIds`（任意级别，组合起来是并集）；
资产库标签栏支持**跨链多选**（选「产品」与选「角色」可以并存），同一条链上仍只留最具体
那一层（选了二级就别再留它的一级，否则范围重复）。模板库里的素材选择器同样三级 + 多选。
老模板走**读时回退**而不是破坏性迁移：契约层把老草稿的 `tagId` 翻译成 `tagIds` 的第一项
（否则 `extra=forbid` 会让老草稿一打开就 422），行级新增 `template_spec_slots.tag_ids`，
读时为空就退回旧的 `tag_id`。迁移 `d9e4f1a83c07` 只加列不回填。

**提示词占位**：textarea 里只能是原始 token（`{{slot:image_1}}` 这种内部 id，作者读不懂），
所以输入框下面加一段**预览**：占位渲染成带类型图标的 chip（「主图」），普通文字是纯文字 ——
一眼就知道哪几段会被素材顶掉。预览限高 110px 自己滚，不把右栏撑开。

**上传代码的检查结论**：

- **日期是对的**：库里存 UTC（`created_at` 就是上传那一刻），接口按 `UtcDateTime` 输出
  带 `Z` 的 ISO（naive 值按 UTC 补齐 —— sqlite 读回没有时区，不补齐前端 `new Date()`
  会按本地时区解析，整体偏 8 小时），前端 `formatDateTime` 再转本地。实测库里
  `2026-11-12T01:46:08Z` → 本地 `11-12 09:46` ✓。
- **上传时打标签的实现是合理的**：标签整批统一（不能给不同文件打不同标签）；只给**叶子**
  可选（一级分组是打标守卫拒掉的，界面不能给）；扩展名白名单与后端同一份；体积上限与后端
  对齐（文本 1MB、其余 300MB）；打标签是**第二段**——失败只提示「标签未打上」，不会把已经
  上传成功的文件报成「上传失败」（那会让用户重传、库里多一条重复素材）。
  唯一可挑的：每个文件一次 PATCH，N 个文件 N 次请求（后端本来就有批量 `addTag`）。

### 1.3 审查 21:00 之后的八笔提交（2026-11-17）

用四个只读审查去看那八笔（案例用途 / 封装下线 / 标签三级 / 审查报告修 27 条 /
模型清单 + 槽位标签 / 提示词占位），拿到两十条具体缺陷，本轮全部修掉了。

**真正会误导的那几条**（都是「界面写的与点进去看到的不一致」）：

- **标签栏计数没过滤案例**：列表默认排除 `purpose=case`，而标签计数把它算了进去——「标签旁写 8，点进去只有 3 条」。
  现在计数与列表同口径：普通入口排除案例，案例池入口（`purpose=case`）只算案例。
- **案例池的标签栏数字根本不是案例口径**：后端 `GET /library/tags` 不认 `purpose` 参数，FastAPI 静默忽略未知参数，
  数字全错且不报错。现在表展开来只认「空」与「案例」两个值，写错一律 400。
- **同一个一级下的兄弟分支被吃掉**：收敛判据写成了「按链根分组」，勾了「产品 / T40」与「产品 / 3030 / X100」
  只剩一支——另一支素材被静默筛掉，胶囊却还高亮着。现在判据是「有更具体的一档被选中就让位」，
  同链留最具体的那一层，兄弟分支两支都留。
- **标签调整后的层数与父链是移动前的快照**：移动提升一级后返回的 `depth` 仍是旧值，改名接口的 `parentName` 只给直系父名。
- **案例删掉之后就找不回了**：用途过滤原本对草稿与回收站也生效，被删的案例在回收站里看不到（而角标又把它算进去了）。
  现在用途过滤只对 active 生效。
- **新权限码没落进任何内置角色**：非超管作者配不了展示案例（封装页的「设为案例」只会报 403）。
  现在 `library:case:read/write` 已进入「内容编辑」与「素材专员」两个角色。
- **提示词预览的引用形状比真实编译宽**：预览用的是不容空格的严格版正则，老草稿里的 `{{ slot:image_1 }}` 不识别；
  现在改成容空格（与后端、与 `specDraft` 同一口径）。
- **模型换了而选择残留**：模型清单里两款模型的 `userSelectable` 参数不同时，切模型会拒绝。现在换模型就清空用户选择。
- **已下线功能的空跑用例**：旧的「排除条件必须落在 SQL 里」一用例拿已删的封装做例子（参数早不存在，断言恒真），
  现在改用真实存在的维度（类别）。
- **mock 与真实后端倒退**：演示环境的词表是平的（三级能力一行都看不到）、上传不认 `kind=text`、
  共享申请永远「已通过」。现在 mock 也是三级词表 + 用途路由 + 待审批申请。

下面这些项本轮没做（写在 `TODO.md` 里）：封装下线后的 `pack_id/pack_version` 死字段、
模板封装页的素材选择器看不到案例池、`allowedModels` 的「可接不接得住编号槽位」闸、案例池的跨用户可读与 403 用例。

### 1.4 老草稿里的封装字段：模板列表整页 500（2026-11-17）

前面接到「前端没有模板」：模板都还在库里（14 条），是 **列表接口整页 500**。

根因是封装下线那笔提交（94d2ec2）删了槽位契约里的 `packId`，却没给**老草稿里还带着这个键**的情况做读时兜底：
入参模型是 `extra="forbid"`，那个键被判成「未知键」→ **422**；而列表会把每个草稿都校验一遍，一个拿不掉就整页报错。
本地库里 14 条草稿里有 12 条带着 `packId`（它们都是封装下线前存的）。

修法与老 `tagId` → `tagIds` 同一个形状：在契约层把旧键丢掉（新增
`_drop_legacy_pack_fields` 前置校验器），而不是逼人去改库。封装槽位本身在迁移
`c3d8e1f04a7b` 里已经改成空图片槽位了，丢的只是一个残留引用。
补了一条回归用例（直接改库把草稿改回封装下线前的样子，再读列表）。

**教训**：下线一个功能时，要同时看**删掉了契约字段**与**存量数据里还带着那个键**的那批行。
迁移只改了行表，没有动草稿 JSON 里的那一把——它们不在任何迁移的负荷里，只在读草稿时才会被看见。

## 3. 没做完的（直接从这里开工）

完整清单在 [`TODO.md`](TODO.md)，这里只标需要拍板 / 刚变动的：

1. **T30 · PG 迁移闸收紧**：`postgres-migrations` job 已连续 3 次（#62/#63/#64）通过，
   TODO 里的收紧条件已满足，但 **2026-11-11 拍板「先不动」**——保持 `continue-on-error`，
   这道闸目前只负责暴露方言差异、不阻塞合并。哪天要收紧，照 TODO 的前置检查做。
2. **被移出小组后 owner 看不到素材**（产品口径待定，暂按现状）。
3. 其余存量：T2/T8/T9/T10/T22~T26/T29，均带验收口径。
4. **任务中心 `canReadAll` 仍只表示 `task:read:all`**：生图 reader 自己支持
   `image:read:all`，但前端范围下拉只有一套、由 `task:read:all` 驱动。当前不会越权
   （后端三路各自判权限），要拆「生图全站范围」得连契约字段一起改。

## 3.1 测试范围怎么定（2026-11-12 用户拍板：别每次都跑全量）

全量 `pytest -q` 是 **169 秒 / 1570 条**。多数改动只碰一两个模块，跑全量是浪费。
**判据交给工具，不要凭印象挑**（凭印象会漏）：

```bash
cd api && ./.venv/Scripts/python.exe scripts/select_tests.py --print
```

它做三件事：

1. **依赖反查**：静态解析每个测试文件的 `import`，建「源文件 → 测试文件」反向图；
   改了 `src/modules/library/tags.py` 就命中 `tests/test_library_tags.py`。
2. **只向上游传一层**：谁 import 我 → 也复测；**不向下传**（我 import 谁不算）。
   双向传递会让 `core/registry.py` 的改动扩散到全部 286 个源文件，等于全量。
3. **分级**：

   | 级别 | 触发条件 | 跑什么 |
   |---|---|---|
   | `targeted` | 业务模块 / 纯扩展改动 | 依赖图算出的测试文件 |
   | `framework` | `core/` `api/` `db/` `infra/` `generation/` **且改了已有语义** | 依赖图 + 全部架构守卫 |
   | `contract` | 改了 `modules/*/router.py` / `schema.py` / `permissions.py` | 依赖图 + `export-openapi --check` |
   | `full` | 端口目录 / 装配声明 / `modules/*/spec.py` / 迁移 / `pyproject.toml` | 全量 |

**「纯扩展」与「改语义」的区分**：diff 里只有新增行、没有删除行 → 纯扩展
（给 `NamedRegistry` 加个 `remove()` 方法，53 个依赖它的测试一个都不会坏），
按 `targeted` 跑；有删除行（改了已有函数体）→ 改语义，按上层规则走。

**升级到全量的硬条件**（工具自动判断）：`src/api/ports.py`、`src/api/assembly.py`、
`src/api/discovery.py`、`src/core/exceptions.py`、`src/core/response.py`、
`src/generation/runner.py`、`pyproject.toml`、`migrations/`、`modules/*/spec.py`。

**永远要跑的守卫**（工具自动包含）：`test_architecture.py`、`test_internal_models.py`、
`test_openapi_contract.py` —— 它们不快，但正是「改错了没人发现」的那一类。

**别做的事**：别自己凭印象挑测试文件（会漏）；别因为「保险起见」就跑全量；
也别跳过 `ruff` / `mypy`（那两个是秒级的，与测试范围无关）。
前端同理：`npm run verify` 只在改了 `studio/` 时跑。

**自查工具准不准**：把改动临时还原，跑它选出的测试 —— 应当**变红**。
（本次已验：还原示例驱动的旧命名，`test_internal_models.py` 准确变红。）

已关闭、留结论防重复劳动：

- **T31 · 素材直传阿里云 OSS**：**不是合规遗漏，是有意设计**。部分视频模型只接受
  公网 URL 作参考素材，所以必须先传到公网可达的地方再给直链；直传目标由百炼签发
  （临时目录，48 小时有效），凭据是一次性 policy，不带我们的 `Authorization`。
  完整口径在 TODO 的 T31 小节。
- **T32 · `bailian` 迁入 `drivers/`**：驱动自动载入，不再进
  `AI_PROVIDERS`；替换链改走可选协议 `infra.ai.base.InputPreparer`（上传素材 +
  自报可用性），提示词重编号下移 `core/prompt_refs.py`。回执路径与计费纪律都没变。
- **T33 · 替换链专用模型漏进目录**：`ModelSpec.internal` + `public_models()`。
  过滤只发生在**目录层**，`resolve()` / `provider_for()` 不动——替换链正是拿
  `bailian-wan3-*` 的 id 直接提交的。
  **2026-11-13 后续**：三个替换模型不再标 `internal`。通用入口现在会补出归档
  工作区（`modules/model_driver/generic_inputs.py`），所以「露脸必然 400」这个
  前提消失了；`internal` 机制本身保留给将来真正只服务某条业务链的模型。
  守卫改成「公开模型必须声明 `paramSchema` / `maxReferences`，且不当默认」。

## 4. 新会话怎么接手

```bash
# 起服务（改完代码必须带 --reload，否则 /docs 看不到新接口）
cd api && ./.venv/Scripts/app.exe serve --reload

# 后端门禁：basetemp 每轮必须换一个全新目录（复用到旧目录会出成片假错误）
# 全量（169s / 1570 条）只在改动触发「结构性条件」时才跑
cd api && ./.venv/Scripts/python.exe -m pytest -q --basetemp=var/pytest-$(date +%s)
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy --platform win32 src     # 与 CI 同口径

# **平时按改动范围跑，不必每次全量**（2026-11-11 用户拍板）：
# 先算出该跑哪些，再跑。工具会同时给出分级理由。
cd api && ./.venv/Scripts/python.exe scripts/select_tests.py --print
# 典型耗时：定向 ~20s vs 全量 ~169s

# 契约（改了接口就跑，四份一起提交）
./.venv/Scripts/app.exe export-openapi                       # --check 只比对不写盘

# 前端
cd studio && npm run verify
node scripts/gen-routes.mjs --check && node scripts/gen-mock-coverage.mjs --check   # verify 不含这两步

# 推送（本机代理会 502，必须清掉；并且要 HTTP/1.1）
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy GIT_TERMINAL_PROMPT=0 \
  git -c http.version=HTTP/1.1 push origin main

# 提交信息走文件，别用 -m（Git Bash 会吃掉多行与 @）
# Write var/commit-msg.txt  →  git commit -F var/commit-msg.txt
```

**看 CI（本机没装 `gh`）**：用 `git credential` 取 token 再打 API，日志同样能拉：

```bash
TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill | awk -F= '/^password=/{print $2}')
curl -sS --noproxy '*' -H "Authorization: Bearer $TOKEN" \
  "https://api.github.com/repos/gaishipolo/yufan-ai-studio/actions/runs?per_page=1"
# 失败时：/actions/runs/<run_id>/jobs 找结论为 failure 的 job，再取
#   /actions/jobs/<job_id>/logs  →  -o var/ci.log，grep FAILED / assert
```

## 5. 不变量速查（改代码前先读这几条）

- **store 写方法返回 `bool`**：`mark_succeeded/cancelled/failed` 遇终态返回 `False`，
  调用方拿到 `False` 必须立刻 return，不许再 archive / 发事件。
- **供应商解析只有一处**：`ModelRouter.provider_for(model, allow_fallback=...)`；
  历史记录（轮询/重试/恢复）不许兜底——流水线声明 `strict_model = True`。
  **别再写 `pipeline.resolve_provider`**。
- **模型下线 ≠ 任务失败**：`provider_for` 严格模式抛 `ModelUnavailable`
  （`infra/ai/router.py`）。轮询侧默认**当场判失败**（`fail_on_model_gone` 默认 True），
  不许退避重试到超时——用户最后只看到「生成超时」，指不出真正原因。
  跑**已付费**任务的流水线要声明 `fail_on_model_gone = False`（替换链就是），
  渠道停用只说明联系不上供应商，判失败会诱导重试 → 重复计费。
- **彻底删除**：先取 `id` 到局部变量再 `rollback`（ORM 实例会过期），最终 `DELETE` 的
  `rowcount != 1` 就整段放弃；引用计数按 `storage_key` / `cover_key`，不按 `sha256`
  （内容相同 ≠ 同一个对象）。
- **错误码** `status*100 + slot*10 + seq`，每模块每状态码只有 9 个 seq；
  library 的 409 段已满，新错误加了走 `40088`。撞墙预案见 `EXTENDING.md` §7.1。
- **注入口三件套**（`register_*` / `reset_*` / `has_*`）+ 登记 `api/ports.py` 的
  `PORT_CATALOG`；`register_` 是「注册」语义，要覆盖得用 `provide(replace=True)` 显式写。
- **契约四份**：`api/docs/openapi.json`、`api/docs/API.md`、`studio/src/shared/api/schema.ts`、
  `studio/src/shared/api/permissions.ts`。整文件覆盖，冲突时**重新生成**，不许手工解标记。
- **计时类断言按墙钟窗口定工作量**，不要按「跑几轮」：机器快慢差一个数量级时，
  固定轮数会让窗口趋零，断言先于被测行为失效（`test_password_offload.py` 是范例）。
  需要真实哈希成本的用例显式钉 `security._scrypt_n`（`full_cost` 夹具）。
- **模块之间不许互相 import**（`test_modules_do_not_import_each_other`），
  要复用靠装配期登记或上移 `core/`；`api/` 层也不许 import 业务模块。
- **驱动的可选能力靠协议识别**：需要「提交前上传素材」的驱动实现
  `infra.ai.base.InputPreparer`（`prepare_inputs` + `unavailable_reason`），调用方
  （替换链）从路由拿实例后 `isinstance` 判断。**不要** `getattr(provider, "prepare_inputs")`，
  也不要让 `modules/template` import 任何 `drivers/*.py`——那正是 T32 之前的状态。
- **提示词素材引用只有一份实现**：`core/prompt_refs.translate_prompt`（`{{Mixed N}}` →
  `视频1/图2/音频1`），档案与请求体都用它；别在驱动或模块里再抄一遍。
- **文本资产是「文件型资产 + 版本快照」，不是文本表**：`kind=text` 复用
  `library_assets` 的去重/权限/回收站/取件；正文另存 `library_text_versions`（**只增不改**，
  恢复旧版 = 再写一版）。上传与站内新建都要写第 1 版，否则 `read_text_many` 查不到。
  正文一律纯文本：严格 UTF-8、剥 BOM、`.md` 也不转 HTML，取件固定
  `text/plain; charset=utf-8` + `nosniff`；文本没有缩略图，别给它生成 `cover_key`。
- **`{{Text N}}` 注入只有一条链**：`core/library_text.py` 一处（校验 / 冻结 / 注入 /
  mask / 出参投影）。受理期 `freeze_text_refs` 把 `id/name/version/sha256/content`
  冻结进 `image_jobs.text_json` / `video_tasks.text_json` / `replacement_runs.text_json`，
  提交期只读快照注入，**不再查库、不再读 blob**——否则用户改稿后重试会换一段提示词。
  可读性判据只有 `LibraryLookup.read_text_many` 一份；替换链的编译器拒绝任何花括号，
  所以那条链要先 `mask_text_refs`、编译完再 `inject_masked_text_refs`。
  编号按 `textLibraryIds` 顺序从 1 起，最多 8 份；越界编号当场 400。
  前端重排 / 解绑走 `textRefs.ts::retargetTextTokens` 按 **id** 重编号，别按位置覆盖。
- **选择器默认排除文本**：`LibraryPicker` / `LibraryFlyout` 只有显式 `kinds` 含
  `'text'` 才显示；图片 / 视频 / 封装选择器不能因为多了一种类型就把文案混进去。
  文本也**不是**参考图，别塞进 `referenceLibraryIds` / `referenceAssetIds`。
- **任务中心不许 import 别的业务模块**：读别人的表走 `core.ports.generation_feed`
  （生图，SQL 侧筛选 / 统计 / 分页）或 `core.feed`（老替换档案，按 owner 全量）。
  第三个生成来源也自带 reader；`generation-feed` 未注册返回空页是合法状态，
  **别改成抛异常**，否则只跑任务模块的单测 / 裁剪装配时会整页 500。
- **聚合页产物地址一律现签**：库里存对象 key，出参才按当前存储驱动
  `artifact_urls()` 转换；视频 / 生图 / 替换三路都别把裸 key 交给前端。
- **模板封装模块只管数据、通过 ports 对外提供**（用户拍板）：生成是
  `modules/task` 的消费行为，不在封装模块实现、也不由封装模块去接。
  `core.ports.template_catalog` 是编写侧的只读窄接口，没有 `verify_picks` / 候选判定；
  旧的 `TemplatePackPort`（fixed/filtered 槽位）随 C2 一起下线，别合并。
- **替换执行链只经 `core.ports.template_runtime` 读封装数据**（C2 第一刀）：
  `replacement_service` / `authorization` 不许 import `PackRepository` /
  `video_template_service` / `TemplatePackVersion`，也不许拿 ORM 行。
  与 `template_catalog` 刻意分成两个端口：那边读**编写侧**（manifest / packs /
  functions），这边读**运行侧**（`template_packs` + `_versions` + `video_templates`）；
  合并会让编辑界面每加一个字段都去改运行侧快照。未注册时 **fail-closed**
  （`require_template_runtime()` 抛 `RegistryError`）——执行链拿不到冻结版本就
  准备不了任务，退化成「封装不存在」的 400 会把装配故障说成用户选错包。
  **C2 正刀已完成**：`register_template_runtime()` 的调用点已挪到
  `modules/template_pack/__init__.py`，`replacement_service` / `authorization`
  一行没改。搬表时唯一没预料到的是 `asset_source.py`（替换链侧）被
  `template_pack` 三个文件共用，已上移 `core/library_assets.py`。
- **迁移之后两个模板模块的边界**（C2 正刀，2026-11-14）：`modules/template` 只剩
  替换执行链（三张 `replacement_*` 表 + 路由 + worker + 编译器），
  `modules/template_pack` 拥有素材包 / 视频模板 / manifest 六张表与全部封装路由。
  两边**只能**经 `core.ports.template_runtime`（运行侧）与
  `core.ports.template_catalog`（编写侧）交换数据。共用词汇上移 core：
  `core/library_assets.py`（素材元数据）、`core/template_pack_view.py`
  （`ReplacementPack` 卡片形状）、`core/legacy_json.py`（老盘 JSON 读法）。
- **权限码由 `template` 声明、`template_pack` 的路由去 require**：
  `template:pack:manage` / `publish` 沿用重建前的命名（改名会让已有角色静默
  失去权限），而 `test_permission_codes_are_namespaced_by_module` 要求码前缀
  等于**声明它的模块名**，所以只能登记在 `template` 的 `ModuleSpec` 里。
  别为了「看起来对齐」把这两个码搬去 `template_pack`：那条守卫会红。
- **`import-template-legacy` 已拆成两条命令**：`import-template-packs`（写封装三表）
  与 `import-template-runs`（写调用档案三表）。拆分原因是业务模块不许互相 import，
  合起来就得让一半去写别人的表。升级时按 `packs → runs` 顺序跑。
- **模板名不再有进程级缓存**：`_prompt_origin` 是纯函数，名字表由读入口
  （`list_runs` / `detail` → `_warm_template_names`）经端口批量预热进
  `self._template_names`。别再把字典提回模块级：那是跨请求串味、且无法失效。
- **发布回执在发布那一刻冻结**：`published_pack_id` / `published_pack_version` /
  `published_template_id` 不是读时拼的，停用也不清空；`CatalogPack` /
  `CatalogTemplate` 快照原样带出。**它们指向投影出来的运行行**（`template_packs` /
  `video_templates`），不是配置行 —— 配置行 id 在运行链路里从不出现。
- **复用包的 `packId` 指运行素材包**（原 packager 口径）：预检查 `TemplatePack`
  的 `status='published'` + `version>0` + 冻结版本行存在，查不到就 400。
  别改回「配置行」（`template_manifest_packs`）：那样手工用封装编辑器建的包永远
  复用不了，投影时也按运行表查不到。
- **service 层显式 commit**：`db.session.get_session()` 只在异常时回滚。
  `save` / `publish` / `disable` 漏 `await session.commit()` 的症状是「接口 201、
  紧接着的读全 404」，很容易误判成权限或缓存问题。写侧新建实体还要在
  **第一次 flush 之前**填满所有 NOT NULL 列，否则被框架兜成 409 看不出真因。
- **POST 路由一律挂 `rate_limit`**：`test_route_policy.py` 按 HTTP 方法扫，
  连「只预检不落库」的 `/validate` 也算写路由，不挂就得进豁免表。

## 6. 本机环境坑（Windows 开发机）

| 现象 | 真相 |
|---|---|
| `python -m app` 静默退出 0 | 那是应用工厂。命令一律 `api/.venv/Scripts/app.exe` |
| `pytest` 出成片 error（260+） | 复用了旧 `--basetemp`。**每轮换新目录**，跑前 `ls var/<dir> \| wc -l` 确认是 0 |
| 后台跑全量「像卡死」 | 输出被 `\| tail` 吞着。改成 `> var/x.log` 再看 `tail` |
| `curl 127.0.0.1` 502 | 本机代理。加 `--noproxy '*'` |
| `OSError: SHFileOperationW 失败: 0x78` | WorkBuddy 的 safe-delete shim 把 `unlink` 转回收站，高负载偶发；单跑能过，不是代码问题 |
| `alembic.exe` 退出码 1 无输出 | 坏启动器，用 `python -m alembic` |
| vite 预构建崩 / 模块缺导出 | 删 `node_modules/.vite/deps_temp_*` 后重启 |
| MinIO 桶级 CORS 设不了 | 本机 `PutBucketCors` 未实现；数据在容器内，**别 `docker rm`** |
| `git-credential-manager.exe store` 报 no such file | 已知噪音，退出码 0，认证仍成功 |
| 仓库部分 `.md` 是 CRLF | 判断换行先 `splitlines()`，别直接比对字符串 |
| LibTV 任务 5 秒内失败，`errorMessage` = `素材上传准备未完成：RELEASE_INVALID` | 桥接层的字节校验没过：`run/records/executor-error.json` 会写清是哪个文件（`Vendored adapter changed: ...`）。**先怀疑 CRLF**：vendor 快照必须与 `manifest.json` 的 sha256 逐字节一致，`.gitattributes` 里 `.../libtv/runtime/vendor/** -text` 一旦漏掉（例如目录搬家后规则没跟），`core.autocrlf=true` 就会把快照检出成 CRLF，于是**所有** LibTV 任务都挂在 upload 阶段、而 `git status` 是空的。修法：补规则 → `rm -rf` 该 vendor 目录 → `git checkout --` 重新检出。守卫：`tests/test_libtv_vendor_manifest.py` |

## 7. 文档地图（哪份是活的）

| 文档 | 状态 |
|---|---|
| `ARCHITECTURE.md` / `EXTENDING.md` / `CODE_STYLE.md` / `REVIEW_CHECKLIST.md` | **活文档**，改结构/约定时同步 |
| `TODO.md` | **活文档**，唯一待办清单 |
| `MODEL_DRIVER_STANDARD.md` | **活文档**，含 §11 老路径豁免登记 |
| `OPS.md` | **活文档**，部署与告警 |
| `ROUTES.md` / `MOCK_COVERAGE.md` | 生成物，`--check` 校验，别手改 |
| `TEMPLATE_LIBRARY_V2.md` | **活文档**，模板库 v2 的五向 I/O 契约与待拍板项（2026-11-13 新写） |
| `TEMPLATE_SPEC.md` | **设计定稿、待实施**（2026-11-14）：模板封装泛化为 "槽位 + 两段提示词 + 模型绑定"，封装定表 / 模板库填表 / task 执行三层；**终态统一到 task**（P3 删掉独立替换工作台，替换成为 task 的 capability，复核并入任务中心）。与 `TEMPLATE_LIBRARY_V2.md` 无关（那份是任务中心的旧计划） |
| 历史评审 / 计划 / 需求 / 设计文档 | **2026-11-13 已清理**，只留 git 历史；不要从 git 里捞回来当现状读 |

## 8. 已知偶发（不是回归，别急着改代码）

| 现象 | 判据 |
|---|---|
| `test_archive_blobs` / `test_archive_index` 报 `OSError: SHFileOperationW 失败: 0x78` | safe-delete shim 回收站失败，**单跑必过**；同一轮里其余用例照常 |
| `test_scheduler::test_leader_only_lease_call_does_not_block_the_event_loop` 偶红（`worst` 80ms vs 50ms 阈值） | 负载敏感的计时断言，单跑 11 passed；机器空闲时复跑即绿 |
| 全量 3~6 分钟而不是 200s | 机器上有别的负载（浏览器/开发服务器）。看 `--durations` 有没有异常项再判断 |

判断顺序：**单跑复验 → 换全新 `--basetemp` 复跑 → 才怀疑代码**。反过来做会浪费一整轮。
