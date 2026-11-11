# 人物 / 产品 / 场景替换与素材归档

## 文字换场景（原视频卡片的新增功能）

同一原视频卡片可新增“文字换场景”，与现有场景素材包功能并存。此模式只上传原片；工作台显示可编辑的“场景描述”多行输入框，预填封装者给出的默认场景，支持恢复默认。人物、产品、动作、声音等固定保留约束由模板保存，准备任务时只替换 `{{ScenePrompt}}`，核对弹窗和历史档案展示当次描述及完整提示词。

契约：`rules.inputMode = "video_scene_text"`、`rules.scenePromptDefault`，模板 `defaultKind = "scene"`；不绑定素材包，媒体清单只有原片。prepare 显式携带 `videoTemplateId` 与 `scenePrompt`，省略描述使用默认值，空白或带模板标记拒绝。该模式拒绝混入素材包、散图、范围和补充要求，普通人物/产品/图片换场景沿用已有契约。

会话封装说明及完整酒店会客厅示例见 [新版调用指南](AI_TEMPLATE_PACKAGER_V2.md)。本轮验证：后端全量 1172 passed / 12 skipped；前端 523 passed，类型检查、lint、构建通过。只登记模板，未执行真实视频生成。

## 素材封装（当前规则）

封装一个替换目标 = 一组同类参考图 + 每张图的用途 + 必备用途 + 该目标已审核的替换范围。草稿可以随时保存、允许不完整，**只有发布的版本才会出现在模板库**；发布是不可变版本，普通用户只能选到它。

用途按类型固定：产品 `front`（主图）/`multiview`（多视结构）/`detail`（细节）/`back`/`side`，人物 `front`/`side`/`fullbody`，场景 `main`/`angle`/`detail`。基准用途（产品与人物为 `front`、场景为 `main`）永远是必填且不可取消，其余用途由封装者按目标实际情况勾选为必填。

发布前服务端校验：名称、1–10 张不重复参考图、必备用途齐全、产品必须有至少一个替换范围、逐图 sha256 与登记值一致。缺项直接返回“发布前请补齐：…”，并在草稿列表里以“还差：…”提前显示，不需要等到点发布才知道。封装编辑器按“左侧选图 → 右侧逐图定用途 → 勾必填 → 勾范围 → 保存并发布”组织，顺序即提示词编号顺序，可上下移动调整。

用途不是装饰：生成时每张图按用途写出说明（主图定外观、多视图只补结构、细节图只补局部），封装时填的补充说明追加在其后；同一 `target_product` 下的多张参考不再共用一句笼统描述。产品的替换范围由包决定，模板页只展示 `supportedScopes` 内的位置，服务端同样校验，越界请求直接拒绝。缺省 `replacementScope` 不再由服务端补默认值（旧默认串里含已废弃的“屏幕商品”），调用方必须按包内审核结果显式给出，否则回 400 并列出可选项。

接口变化：`PUT /templates/replacement-packs/draft` 增加 `bindings`（与 `referenceIds` 同序）、`requiredRoles`、`supportedScopes`；`GET /templates/replacement-packs` 每项增加 `gaps`；`GET /templates/replacement-catalog` 增加 `packRoles`、`baseRoles`、`packScopes` 供前端渲染选项。旧迁移包没有用途时按基准用途补齐，不改变 `referenceIds` 顺序，因此历史包仍能正常读取、编辑与发布。已发布但内容不全的历史版本（旧模型发布时丢失过用途与范围）在目录里标记 `available: false`，页面禁用该卡片并提示先在素材库补齐，不会让用户点进去后才卡在范围步骤。

验证：前端 `npm run verify` 通过（51 个测试文件、375 项测试、类型、Lint 和构建），覆盖旧包用途回退、缺项禁用发布、保存并发布时用途与参考图同序提交；后端 `pytest` 310 项通过，覆盖旧包补齐、非法用途拒绝、用途与图片数量不一致、缺必备用途、缺产品范围、缺省范围被拒、同一提交重试幂等、完整包发布后进入可用列表。前端用途表与 `core/pack_roles.py` 的一致性由测试断言，两边改一处不同步会直接失败。未对线上素材包执行发布或修改。

## 单视频任务（当前选择规则）

模板库每次仅选择 1 条原片、准备 1 个任务、核对并确认 1 个任务。点击其他视频替换当前选择，再次点击当前视频或移除/清空可取消选择；上传合规原片成功后，该视频成为唯一当前选择，上传失败或不合规时保留原选择。人物、产品与场景替换均沿用此规则。

页面状态只保存单个 `sourceId` 和单个核对任务 ID，准备和提交不再循环处理任务。
`sourceId` 只有 URL 一个来源：选择、移除/清空、深链带入都改同一个查询参数，因此刷新不会复活已清空的选择，
“带入的原片不可用”提示也始终指向当前选择。
主入口保持单视频流程；批量准备是可展开的辅助入口（勾选多条已选原片后逐条准备与逐条核对），
两者共用同一套准备/上传/提交互斥锁。
无原片时显示选择引导；准备/上传/提交期间锁定选择与配置。准备失败显示可重试错误，同配置复用请求 ID；执行失败在核对弹窗保留错误与原任务，不把请求失败解释为生成未提交。服务端费用门禁、归属校验、历史档案、评价和回收站保持原规则，历史任务逐条查看和继续。

本轮验证：类型检查、Lint 和生产构建通过；全量前端 `verify` 通过（50 个测试文件 / 369 项），后端 `pytest` 280 项通过。
前后端权限目录各 28 个权限点、无差异。实际页面连续点击两条视频后仅第二条被选中，配置区为“已选 1 条”；未准备或提交真实生成任务。

## 核对弹窗与任务管理（当前规则）

任务命名统一为“原片视频名前缀 · 替换类型 · 目标名称 · #任务 ID 前 8 位”，例如“火车站行李袋裂变-丕6036白 · 人物替换 · 爆款人物-2 · #1C916E7A”。原片名去掉末尾 MP4 扩展名，超过 18 个 Unicode 字符时保留前 18 个并加省略号；只截取视频名，类型、目标与任务短码保留。标题悬停显示完整名称，列表原片行单行省略并可悬停查看原名；快照文件和下载文件不改名。人物名是替换目标，不是任务唯一标识；短码用于快速识别，完整调用 ID 才是唯一编号。列表、核对弹窗、档案详情和删除确认共用同一命名规则；列表额外显示归档原片、创建时间与完整编号。上游生成 ID 单独标注，未提交也有本地任务编号。新建调用产生新编号，继续查询或重试原任务沿用原编号，不按当前列表顺序编号，不因筛选、删除或恢复而改名。历史档案没有原片摘要时明确显示未记录，不使用当前选择代替。

用户是已经准备或生成过替换任务的创作者。本轮解决确认弹窗内容贴边、长内容挤压操作区，以及任务记录只能查看不能整理的问题。价值动作是清楚核对实际输入后再确认生成，以及把不需要的记录移入回收站并可恢复。

- 核对流程：准备输入 → 核对原片、目标主图和替换范围 → 确认生成。已准备的历史任务可从调用档案重新打开核对；只打开核对不会提交生成。正文带内边距、独立滚动，底部保留返回与确认按钮；长文件名和提示词可换行，目标缩略图完整显示。
- 管理流程：搜索目标/原片/任务 ID、筛选任务状态 → 查看档案 → 确认移入回收站 → 在回收站恢复。列表显示创建时间和档案 ID，默认只展示未删除记录。任务与其调用档案作为一个记录管理，不拆开删除关联数据。
- 保存：`records/management.json` 保存 `deletedAt`（UTC）与 `changedBy`，使用文件锁和原子写入；历史记录缺少文件时默认未删除。生成清单、输入快照、评价、调用回执及成片保持完整，索引修复不会清除删除标记。
- 契约：`DELETE /api/v1/templates/replacement-runs/{id}` 移入回收站；`POST /api/v1/templates/replacement-runs/{id}/restore` 恢复。要求 `template:read`、`template:use` 且任务归属本人；重复操作幂等。目录返回 `deletedAt`，页面分成任务记录与回收站。回收站任务需先恢复才能手动继续执行。

页面状态：首次请求未返回时显示加载骨架；无记录显示首次引导，回收站无记录显示“回收站为空”，搜索/筛选无匹配单独提示。后台刷新保留旧记录；失败显示错误和重试。管理请求中禁用对应确认/恢复按钮，写入失败保留当前记录与确认区并显示错误，成功后更新列表、计数和成功提示。弹窗执行错误留在弹窗内；生成请求被接收不等于成片完成。

完成标准：桌面与窄屏可完整查看核对内容和确认操作；管理操作有确认、权限与归属校验；回收站刷新后仍持久保留，恢复后回到任务列表，原始文件不受影响。移入回收站不会取消在途生成或退回积分；本轮不提供永久删除、清空文件、批量管理或上游任务取消。

验证：前端 `npm run verify` 通过（48 个测试文件、359 项测试、类型、Lint 和构建），覆盖长视频名截断、Unicode 字符完整保留、完整标题回显，以及同一人物两次调用的识别、档案定位与删除确认；后端 264 项测试、Ruff、mypy 通过，覆盖持久化、重复操作、归属/权限、锁冲突与在途任务保留。实际页面使用已有“爆款人物-2”待核对任务验证弹窗，390px 窄屏下展开长提示词仍保留底部操作；任务管理确认区已检查并取消，统一命名已在实际列表核对。未触发真实生成，未对现有用户档案执行删除；生成效果与取消功能不在本轮验收范围。

## 2026-11-06：素材包主图选择

当前页面流程：选择原片 → 点击已封装素材包的主图 → 设置替换范围 → 下一步确认替换 → 确认生成 → 查看、下载与评价。

目标产品只显示产品正面图，图片下标记产品名称。页面不再要求重复选择素材包名称、填写目标名称或手动勾选参考图，也不展示其他视角。选中主图即绑定整个素材包；多视角和细节图仍参与生成与输入归档，确认页只展示主图用于核对。

`coverReferenceId` 指定封面；历史素材包优先按 `bindings` 中 `front` 的位置映射到当前 `referenceIds`，无该信息时兼容使用第一张。不会使用迁移前的素材 ID。素材包缺失、引用图片缺失或类型不符时，页面禁止继续，并提供刷新和素材库入口。切换替换类型会清除原素材包选择。

已选原片明细和补充要求默认折叠；桌面配置内容独立滚动，下一步按钮固定在面板底部。当前页面只接受已封装素材包，`POST /templates/replacement-runs` 同样强制要求 `packId` + `packVersion`，散图请求直接拒绝；只有 service 层 `prepare()` 的 `require_pack=False` 分支保留给历史档案与测试，HTTP 接口不暴露该分支。

验证：前端 `npm run verify` 通过（41 个测试文件、301 项测试及构建）；后端 260 项测试通过，Ruff 和 mypy 通过。覆盖正面图不在第一张、历史包回退、完整引用提交、无素材包、失效素材包及切换类型清空选择。未触发真实模型生成，成片效果仍由实际任务验收。

## 2026-11-06：操作与人工验收闭环

模板页包含步骤指引、当前原片摘要与移除、实际归档输入核对和状态处理建议。核对时展示的参考图读取当次快照，不用当前素材代替历史输入；旧版批量选择与逐条批量反馈已由顶部单视频规则替代。

生成成功且成片已保存后，可使用“评价成片”标记可用 / 不可用，不可用必须选原因，说明选填。评价不修改生成状态；允许修改，历史、操作人、UTC 时间和版本保存在单个 `review/review.json` 中。版本冲突返回 409，页面刷新最新结论并保留草稿，重新核对后才能保存。归档文件缺失或写入失败不会显示保存成功。

新增 `template:review` 权限，评价还要求 `template:read` 及任务归属本人。首次创建的内置 editor 角色包含该权限；已有角色配置保持不变，请管理员在角色权限界面显式授予需要评价的用户。超级管理员沿用通配权限。

新增接口：

- `PUT /api/v1/templates/replacement-runs/{id}/review`：`status`（accepted / rejected）、`expectedVersion`、`reasonCodes`、`comment`；返回更新后的任务与真实评价状态。
- `GET /api/v1/templates/replacement-runs/{id}`：包含 `review`（版本、结论、原因、说明及历史）、`reviewStatus`、`inputSummary`。
- `GET /api/v1/templates/replacement-runs/{id}/inputs/{asset_id}`：读取归档输入副本，仅可读取当前用户任务绑定的文件。

历史任务没有输入摘要时明确提示缺失；缺少评价文件时保留已有验收结论或默认待验收。不会因旧 pending 占位文件抹掉既有非 pending 状态。

当前接口与页面已通过隔离测试；真实模型生成及效果验收仍需实际用户操作验证，下方旧验证记录保留其原时间和范围。

模板库的默认入口为三种替换模板。旧版支持手动选择参考图；当前页面以本文件顶部的素材包主图流程为准。

人物支持仅替换身份 / 同时替换参考服装；产品支持上身、手持、盒内与屏幕商品范围；场景替换保留原片人物、商品与前景交互。每次仅为当前原片独立归档一个任务。

## 唯一素材根目录

本机 `api/.env` 设置 `MEDIA_ARCHIVE_ROOT=Z:/yufan_ai_studio`，通用存储另设 `STORAGE_LOCAL_ROOT=Z:/yufan_ai_studio/managed`。程序读取 Z 盘失败时直接报错，不将素材写到 E 盘。测试使用隔离临时目录，不访问真实 Z 盘。

```text
Z:\yufan_ai_studio\
  inbox/                       人工放入的待登记文件，登记时保留原件
  incoming/                    浏览器上传临时文件，成功或失败后清理
  library/
    source/YYYY/MM/             完整原片
    character/YYYY/MM/          人物参考图
    product/YYYY/MM/            产品参考图
    scene/YYYY/MM/              场景参考图
    audio/YYYY/MM/              音频
    previews/                  派生缩略图（<id>.jpg）与浏览器兼容预览（<id>.mp4）
  catalog/
    assets/                    素材索引、归属、相对路径、SHA-256、媒体信息
    packs/                     已发布素材包配置与新素材 ID
    runs/                      调用档案指针
    legacy-import.json         旧项目复制迁移的 ID 对应关系
  runs/YYYY/MM/DD/<类型>-<调用ID>/
    00_本次调用说明.md
    run.json
    inputs/                    原始视频、实际参考视频/图片、配置及最终提示词
    outputs/generated.mp4      模型原始结果，不覆盖为后处理版
    records/                   编译、上传审核、估价、提交回执、状态
    review/review.json         独立人工验收状态，默认 pending
    code/                      编译器、提示词模板及调用代码快照
  managed/                     其他模块通过通用存储协议写入的文件
```

未真正提交前不生成冒充实际请求的 `inputs/request.json`。上传到 LibTV 后才生成真实请求和 URL 字段对应关系；临时鉴权凭据脱敏，浏览器登录态不复制到档案。

## LibTV 执行

固定版适配器（libtv-canvas v1.2.0）随代码入库在 `api/src/infra/ai/libtv_runtime/vendor/`，按源仓库原相对路径存放，`vendor/manifest.json` 记录逐文件 sha256。桥接层启动先按清单校验完整性，再从该目录加载上传、提交与查询组件；**不需要任何指向外部项目的路径配置**，校验失败按 `RELEASE_INVALID` 处理。`LIBTV_NODE` 可设置 Node 22+ 的绝对路径。浏览器登录态继续由已登录的专用浏览器提供（`LIBTV_BROWSER_URL`），登录过期时在该浏览器正常登录，不导出 Cookie。

积分加速适配器（`vendor/libtv/adapters/billing.mjs`）已接入。源仓库 main 没有这个文件，2026-11-09 通过已登录浏览器的 CDP 逆向「0 积分畅用」开关补齐，同样纳入 `vendor/manifest.json` 逐文件哈希校验。开关分两层：**账号级**「优先使用 0 积分畅用」一律不动；**单次请求级**由 `params.infiniteSwitch` 控制（`0` = 走 0 积分畅用的免费队列，`1` = 本次关掉畅用走积分队列），同时在请求顶层与 `params` 内带上 `disableBenefitTags`（账号当前生效的 0 积分权益标签，如 `rateLimit_powerZero_20261111`），并重新生成 `requestId`。标签由 `GET https://api2.liblib.art/api/www/commerce/activity/benefit?productTypes=3` 用浏览器同一套登录头读取，只取 `benefitStatus` 为 1 且未过期的 `powerZero` / `rateLimit_powerZero_*` 项；查不到有效标签时明确报错，不静默排回免费队列。已提交的任务照常查询。

- 免费队列提交仍用零积分门禁：必须取得有效的数字估价，且严格等于 0；积分队列反过来要求估价严格为正（报 0 说明标签没关掉，停 `service_error` 不提交）。
- 免费模式下非零报价进入 `waiting_free`，由用户稍后点击继续；不自动切换积分或付费加速。
- `submitting` 先持久化再调用上游；没有 task_id 且回执不明时标记 `submission_unknown`，禁止自动重提。
- 已有 task_id 只查询；后台每 20 秒延迟查询，重启从 Z 盘索引恢复。
- 新工作台通过独立监督进程串行访问账号，账号级互斥用归档盘上的 `catalog/locks/account-executor.lease`（跨机器一致），不再依赖任何旧项目的进程单例锁。同一账号有任务在上传/提交时显示 `waiting_executor`，仅对非计费准备阶段延迟重排；不会中止在途任务。
- API 重启不会终止监督进程或 Node。恢复任务先取得同一租约，等待在途提交保存回执；取得 task_id 后只续查。只有确认原 Node 已退出且持有两把独占租约，才清理遗留 worker.lock。没有执行记录或退出状态不明的旧锁仍需人工核对。
- 准备输入前先写调用档案和索引。失败记录为 `prepare_failed`，前端保留请求 ID，同一配置可在原目录续做；内容不符的输入快照禁止覆盖。
- `records/executor-result.json` 记录执行阶段、PID 与退出分类，`executor-error.json` 仅记录白名单错误码，不保存 Cookie 或原始异常文本。
- 生成成功与人工效果验收分别记录。本次开发不自动创建真实模型任务，不将编译测试当作生成效果验证。

## 旧素材迁移

在 `api/` 下运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/import_legacy_replacement_assets.py
.\.venv\Scripts\python.exe -X utf8 scripts/import_legacy_replacement_assets.py --execute
```

默认只读旧数据库，复制已登记原片与已发布素材包所需图片到 Z 盘；校验旧哈希，记录新 ID，重复执行复用已迁移记录。默认归属本地演示管理员 `u_001`，可用 `--owner` 指定新工作台中的用户。不会迁移旧账号权限、登录态、历史生成队列或整棵历史目录，也不删除旧素材。

封装记录不手写 JSON，统一由下面这个脚本经 `PackService.save` + `publish` 写入，避免字段对不上被读成草稿：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/sync_legacy_packs.py            # 只预演，不写盘
.\.venv\Scripts\python.exe -X utf8 scripts/sync_legacy_packs.py --execute  # 保存并发布
```

它读旧库的已发布版本，按 `sha256` 把旧素材对到新平台媒体，连同逐图用途、补充说明、必备用途与替换范围一起写成新封装。重复执行会跳过内容已一致的包；旧库 `variant`（颜色/发型）折进封装名称，因为新契约没有单独字段。`import_legacy_replacement_assets.py --execute` 在复制完素材后会调用同一段逻辑收尾。

素材页面支持预览、下载、分类上传与 inbox 文件登记；生成结果区直接读取每个调用档案中的原始成片。当前不提供素材删除和共享编辑，保留输入快照及审计记录。

## 代码落点与验证

后端业务在 `modules/template/replacement_*`，素材 HTTP 在 `modules/asset/archive.py`，素材权限与编排在 `archive_service.py`、索引在 `archive_repository.py`；通用文件协议在 `infra/archive/base.py`（工厂 `infra/archive/`，驱动 `infra/archive/local.py`）、媒体探测在 `infra/media_probe.py`，无跨模块业务 import。生成适配器在 `infra/ai/libtv.py`，通过 `replacement_pipeline.py` 注册到原框架，复用提交意图、轮询、超时与 `generation.*` 事件。`AI_PROVIDERS=echo,libtv` 启用该模型；模板专用模型从替换模板入口使用。

前端在 `features/replacement-templates`，API 类型使用自动生成的 `shared/api/schema.ts`。素材页（`features/asset-library`）只读 `/assets` 的 DB 线；归档接口 `/assets/archive/*` 现在**只服务于替换模板**，不再有独立的归档库界面。

检查：后端 `pytest -q`、`ruff check .`、`mypy src`；前端 `npm run verify`。接口改动后运行 `app export-openapi`，一并提交四份生成物（`openapi.json` / `API.md` / `schema.ts` / `permissions.ts`）。

素材索引的真相源是 `assets` 表；`catalog/assets/*.json` 只是给同步 worker 读的派生投影，由 `modules/asset/mirror.py` 幂等维护，漂移会自动收敛（`app rebuild-mirrors` 可全量重建）。

前端请求函数、Query hooks 和类型各自独立；Query keys 包含当前用户，分类与搜索存入 URL，详情按 ID 从最新查询结果派生。

## 原片预览与时长口径

`GET /assets/archive/{id}/stream` 返回 H.264 8-bit + AAC 的 360p 派生预览（首次访问按需生成，落在 `library/previews/<id>.mp4`）。`{id}/file` 始终是原始归档字节，用于下载和生成输入，两者不混用。原因是归档原片可能来自 HEVC Main10 或 H.264 High10（10-bit），浏览器 `<video>` 无法解码；同时避免悬停预览下载 10–24MB 原片。

容器 `Duration` 含 AAC 帧尾部填充，15 秒原片常报 15.02 / 15.07 秒，会被 1–15 秒门禁误判为超长。`infra/media_probe.py` 的 `normalize_duration` 只把不足 0.1 秒的尾数归整到整秒，真实超长素材仍原样保留并拒绝，不静默截断画面。

已有索引可用维护脚本补齐两类派生数据，默认只读：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/refresh_archive_media.py
.\.venv\Scripts\python.exe -X utf8 scripts/refresh_archive_media.py --execute
```


## 本次适配验证（2026-11-05）

- 后端全量 184 项通过；Ruff、Mypy 与 OpenAPI 契约一致性检查通过。
- 前端 83 项通过；类型检查、ESLint 与生产构建通过。
- 浏览器验证原片选择、人物/产品/场景配置切换、素材分类与预览入口；未提交真实上游生成。
- Z 盘 21 份既有素材共 160,303,186 字节，逐份 SHA-256 校验通过；5 个已发布素材包可读取。
- 旧适配器 v1.3.0 的 21 个固定文件校验通过。当前旧执行器持有账号锁，新工作台等待释放。
  （以上为迁移前架构的验收记录；2026-11-09 起固定版适配器已入库，按 `vendor/manifest.json` 的 9 个文件校验，账号锁改为归档盘自有租约。）

## 2026-11-06：执行器账号锁与积分加速

### 「账号执行器正在占用或旧锁需核对」是怎么来的

任务中心这句提示来自 `infra/ai/libtv_process.py`：监督进程在 `upload` 阶段抢锁失败，
把任何 `Conflict` 压成 `EXECUTOR_BUSY` 再原样透出。实测档案（`product-3cd06fa6…`）
里 7 条阶段回执全部是 `EXECUTOR_BUSY`、`node_pid` 恒为 `null`、
`records/executor-result.json` 从未生成——**Node 适配器一次都没启动，卡在拿锁**。

根因是锁的语义用错了。旧夜间工作台的 `run_libtv_worker`（`apps/libtv_workbench/
management/commands/run_libtv_worker.py`）用 `with process_lock(): while True: tick()`
把 `libtv/state/workbench-worker.lock` 从启动持有到进程退出，那是一把**旧进程单例锁**。
新工作台早期把同一个文件当成「账号执行器互斥锁」，于是旧 worker 只要还活着，
每一次上传、提交、轮询都会被判成「账号被占用」，重试 5 次后永远停在 `waiting_executor`。

现在的规则：

- 账号级串行用运行工作区旁 `.locks/account-executor.lock`（`REPLACEMENT_RUNS_ROOT` 指向共享盘，
  跨机器一致），由 `lock_path()` 定位；不能用 `storage.resolve()`——归档切到 S3 后没有本地路径；
- 只有 `upload`、`submit` 进账号锁；`poll`、`download` 是只读的，
  否则同一个人开两条任务轮询就会互相判忙；
- 失败原因拆成三类，任务中心不再只给一句「或旧锁需核对」：
  - `EXECUTOR_BUSY`：别的任务正在用这个账号执行器，自动退避重试；
  - `STALE_LOCK`：`records/worker.lock` 存在但原执行进程未确认退出，**需要人核对**；
  - `EXECUTOR_IO_FAILED`：归档盘或执行器文件不可用。
- 不再检测旧 worker 的锁：账号互斥完全由本仓库的自有租约承担，旧进程不会让新任务无限等待。

旧项目 `E:\yufan-ai` 的 `run_libtv_worker` 已停止；它不再占用账号锁。
旧项目的 Web 界面（`scripts/lan.py serve`）不持这把锁，需要时仍可单独运行。

### 积分加速（本次任务用积分，不改账号开关）

页面两处入口：

1. **核对弹窗**：勾选「用积分加速」后确认，这条任务按积分队列提交；
2. **任务记录**：免费队列报价非 0 而停在 `waiting_free` 的任务，显示
   「加速生成（约 N 积分）」，点开有二次确认。

语义与边界：

- 加速只关掉**这一条请求**的 0 积分权益标签（`disableBenefitTags` +
  `params.infiniteSwitch=1`），走正常积分队列；`billing-default` 那种账号级开关
  **一律不动**，同账号的其他人下次提交仍走免费队列。
- 估价必须给正数才算切换成功。仍报 0 说明标签没关掉，任务标成 `service_error`
  并停下等核对——否则等于「以为花钱插队，实际又排回免费队列」。
- 不设客户端硬上限（按产品要求），估价只在提交前展示；**实际扣费以任务记录里
  上游回执的 `credits` 为准**，前端不承诺估价即账单。
- 已提交上游的任务不允许再切换计费模式（`set_billing_mode` 抛 409）：
  那一笔是按当时队列发出去的，事后改档案只会让核对失去依据。
- 切换模式会清掉旧报价——免费队列的 0 和积分队列的正数是两条队列的数，不能互用。

接口：`POST /templates/replacement-runs/{id}/execute` 可选请求体
`{"billingMode": "free" | "credits"}`，不传等价于 `free`；`ReplacementRunOut`
新增 `billingMode` 供页面标注「积分加速」。

验证：后端 315 项通过（含加速桥接的 40 积分放行 / 0 积分拦截两条、
计费模式只能在提交前切换、从 `waiting_free` 切换后恢复可执行），
Ruff 与 Mypy 通过；前端 377 项通过，类型检查、ESLint 与生产构建通过。
**本轮未执行真实积分扣费**，加速链路的实际账单仍需一次真实任务核对。

## 自动执行反复失败：三层故障与修复（2026-11-06 下午）

现象：任务中心点「继续」总是回到 `service_error`，提示「上传准备未完成」，
但手工在归档目录跑同一个 bridge 却能成功。

按顺序排掉三层，**三层都必须修，少一层都仍然调不通**：

1. **任务在回收站**：`records/management.json` 有 `deleted_at`，
   `execute` 直接 409「请先从回收站恢复任务」。先 `POST /restore`。
2. **run 目录缓存了旧 bridge**：`run_node` 原来只在文件不存在时
   `shutil.copyfile`，老任务永远跑第一次复制进去的副本。改了桥接层却
   行为不变。改为每次阶段开始覆盖（持排他锁，无并发写）。
3. **锁文件名撞上校验遍历**：执行器锁原叫 `records/executor.lease`，
   而桥接层 `update()` 会给 records 下每个文件算 sha256，只跳过
   `.lock/.download/.tmp`。于是它去读自己刚锁住的字节，SMB 归档盘返回
   `EBUSY: resource busy or locked, read`，uploads.json 永远写不出来。
   改名为 `records/executor.lock`（常量 `libtv_state.RUN_LOCK`）。

配套改动：子进程显式下发 `JS_REVERSE_BROWSER_URL`（不再依赖 API 进程继承
环境变量）；stderr 落到 `logs/executor-stderr.log`（放 logs/ 而不是
records/，避免被 checksum 遍历）；`executor-error.json` 增加脱敏后的
`detail`，否则所有故障只有一句「did not complete」，无法定位。

验证：后端 317 项通过（新增「旧 bridge 必须被覆盖」「锁名必须命中排除规则」
两条回归）；真实任务已提交成功，上游 task_id `20261106170503245877197`，
报价 0 积分、benefitTag `rateLimit_powerZero_20261111`，排队约 705 秒。

## 积分生成实测与三个收尾修复（2026-11-06 17:20–17:35）

用同一素材包下已准备未提交的任务 `e9b9e5b9…` 走了一次真实积分提交，
同批的免费任务 `3cd06fa6…` 作对照。**两条都生成成功**：

| 任务 | 计费 | 估价 | 实际扣费 | 上游 task_id | 成片 |
|---|---|---|---|---|---|
| e9b9e5b9 | credits | 240 | **240** | 20261106171803516527197 | 20.9 MB |
| 3cd06fa6 | free | 0 | 0 | 20261106170503245877197 | 17.8 MB |

积分链路验证点：`billingMode=credits` 只改这一条请求（`infiniteSwitch=1` +
`disableBenefitTags`），账号 `billing-default` 未动；免费队列报 0、积分队列报 240，
说明标签确实生效，不是「以为在花钱其实又排回免费队列」。

### 修复一：轮询把真实扣费改写成 0（最严重）

`progress.power` 在排队/生成阶段恒为 0，只有 `create` 回执的 `data.power` 是结算值。
旧轮询写 `credits: progress.power ?? meta.credits`，一进 running 就把 240 抹成 0，
档案从此「看起来没花钱」。改为：只在拿到正数时更新；已被抹成 0 时回落到
`records/submission.json` 的 `data.power` 自愈。回归测试
`test_poll_never_overwrites_the_charged_credits_with_zero`。

### 修复二：`records/`、`review/` 下还有两个 `.lease` 锁

`management.lease`、`review.lease` 与 `executor.lease` 是同一类雷：桥接层 `update()`
遍历 records/review 算 sha256 时只跳过 `.lock/.download/.tmp`。删除任务或保存评价
撞上轮询就会 EBUSY。统一改名为 `.lock`，常量收敛到 `MANAGEMENT_LOCK` / `REVIEW_LOCK`，
回归测试一次性断言三个锁名都命中排除规则。

### 修复三：归档盘原子替换的瞬时缺失

SMB 上 `rename` 没有 POSIX 语义，桥接层每次 `update()` 重写 run.json，覆盖瞬间
路径可能短暂不存在（实测轮询期间读到 FileNotFoundError，接口跟着 500）。
`LocalArchiveStorage.read` 加重试（3 次，约 150ms 窗口）。

### 交互修正

- 「已提交上游，不能再切换计费模式」原来附带一句「请核对当前档案后继续」，
  和事实相反（继续也改不了）。文案改为说明原因并给出出路：新建一条任务再勾加速。
- 核对弹窗检测到 `taskId` 时不再显示加速勾选，按钮退化为「继续查询进度」。

## 重复点「确认生成」不再报错（2026-11-06 18:10）

现象：任务其实已经提交并在上游生成，用户再点一次确认，界面弹出红字
「执行请求失败：任务已提交上游，不能再切换计费模式」，于是判断「没生成成功」。

原因：`set_billing_mode` 把「弹窗沿用档案模式再提交一次」也当成了换队列。
点确认时前端带的是档案里的模式（已加速 = credits），后端却先比 task_id，
直接 409。

规则改成按「是否真的想换队列」判断：

- 已提交 + 想改成 `credits`（免费 → 积分，要花钱）→ 仍然 409，并说明要新建任务；
- 已提交 + 其他任何模式 → 视为「继续查询」，不报错、不改档案。

前端反馈也改为跟状态走：返回 running 就说「任务已在执行中（进度 N%），
状态已刷新；重复点击不会重复提交」，返回 success 就直说成片可播放。

## 视频模板：封装后就是「全部模板」里的一张卡片（2026-11-09）

**用户口径**：封装结果要和 `0507 高端姐` 完全一样 —— 出现在「全部模板」、
同一张原片卡片、可点选；选中后因为这条模板只封装了产品替换，替换配置默认
就是产品替换。单独的「视频模板管理」区块不是交付物，已收到底部折叠。

落点分三层：

1. **卡片**：样片先走 `POST /api/v1/assets/upload?category=source` 登记成
   `source` 原片，再把 asset id 写进视频模板的 `sourceVideoAssetId`
   （`POST /templates/video-templates/{id}/attach-source`，或封装表单里直接选）。
   卡片本身仍由 `useArchiveAssets('source')` + `SourceTemplateCard` 渲染，
   只多一枚「视频模板 · 产品替换」标识。
2. **选中后的默认配置**：`useReplacementWorkbench.selectSource` 在**同一次
   URL 写入**里把 `sourceId` + `kind` + `packId` 一起带上（两次 patch 会互相覆盖）。
   `kind` 取绑定素材包的 `kind`，包未加载时兜底到模板的 `defaultKind`
   （默认 `product`）。深链带 `sourceId` 进来时补一次默认配置，用 ref 记住
   「这条原片已补过」，不随轮询覆盖用户后来手动改的类型。
   `ReplacementConfig` 只有在替换类型没被改、且用户确实选了一个包时才显示
   「固定提示词生效」；**包 id 不锁死**，换产品包仍套用同一条模板。
3. **生成链路**：`ReplacementService.prepare` 用
   `video_template_store.find_for_run(sourceId, packId, kind)` —— 按**原片**
   命中，`packId` 只决定「同一原片封了多条模板时优先取哪条」，不是硬门槛。
   这就是「提示词动态」的落点：模板绑定哪个包只是选中卡片时的默认值，
   用户在替换配置里换成别的产品包，提示词原文一个字都不用改。命中才：
   - `spec.references` 顺序改成「原片 + 按模板 `mediaOrder` 声明的**用途**
     从当前素材包取图」：`{{Mixed 1}}` 是视频，`{{Mixed 2}}` 永远是所选包的
     正面主图（front）、`{{Mixed 3}}` 永远是三视图（multiview）、
     `{{Mixed 4}}` 是细节图（detail）；没声明用途或包里多出来的图按原序补齐，
     一张都不少。封装时 `mediaOrder` 只决定**顺序**，每张图的用途一律按素材包
     的 `bindings` 补齐（界面不传 role，照抄空值会让「按用途取图」全部落空），
     所以换包后 `{{Mixed 2}}` 仍是新包的正面主图；未命中的普通原片
     （含 0507 高端姐）仍是原来的「参考图 + 原片」顺序；
   - `spec.fixed_prompt` 交给 `compile.mjs`，编译器照常产出完整 `bindings`
     并做 mixedList 类型/顺序校验，只把最终文案换成模板原文，
     同时把 `compiler_version` 标成 `+fixed-video-template-v1`、
     `template_sha256` 换成固定提示词自身的摘要；
   - `generation.ratio` / `duration` 覆盖档位（9:16；样片 5.31s → 5 秒、
     6.04s → 6 秒）。封装与生成同一口径，都是 2–15 秒：封装时允许 30 秒、
     生成时截到 15 秒，等于模板上写的档位是假的。

固定提示词允许「3 张图全插入、只点名其中两张」，所以**不能**套用通用编译器
「每个上传素材都必须在提示词里被点名」那条校验；这也正是模板不调用
`compilePrompt` 模板渲染、但仍复用它产出的 bindings 的原因。

`_check_video_template(template, roles, kind)` 在 prepare 前只拦两件事：当前素材包
凑不齐模板声明的用途（直接说缺「正面主图 / 三视图 / 细节图」里的哪一张，
提示换包）、提示词里出现越界 `{{Mixed N}}`（上界按**当前包**的图片数算，
不是封装时那份清单：换到图更少的包，`{{Mixed 5}}` 就指向一个不存在的槽位）。
**换包、升版、换 asset id、包内重排都不再要求重新封装** —— 用途一致就放行，
这正是同一份提示词能动态套不同产品包的保证。

**没有引入 `packMode` 之类的模式开关**：契约仍是「一个模板绑定一个已发布
素材包」的旧形态，动态性全部来自「按用途取图」这一层。

封装入口：`/templates` 底部「视频模板封装管理」，固定蓝本（pinned）可一键
复制；表单里选样片原片、素材包、默认替换类型，并可在「媒体顺序」里上下移动
产品图 —— `{{Mixed N}}` 就是这份清单的第 N 位，要让 `{{Mixed 2}}` 指三视图，
把三视图移到第 2 位即可，提示词原文不用改。`mediaOrder` 留空时才回落到
素材包原序。

**存储与并发**：目录是 `api/var/video_templates/catalog.json`（不入库，原因见
`video_template_store.py` 模块注释）。读改写整段持一把进程内锁，写盘走
`asyncio.to_thread` —— 路由是 `async def`，不能在事件循环里做文件 IO
（`CODE_STYLE.md` §2.3）。这把锁**只在单实例部署下成立**：多 worker 时目录会丢
更新，届时先换仓储表再谈分页，而不是在 JSON 上补 limit/offset。

**审计**：`POST /video-templates/{id}/pin` 切的是全库蓝本，与 pack publish/disable
同一口径写一条 `template.video_template.pin`；`create` / `attach-source` 只算保存，
不记审计。

## 2026-11-09：替换任务与调用档案显示耗时

列表每行和档案详情多了「耗时」，口径是**确认提交 → 这一轮停下**。

为什么不复用已有时间：`created_at` 是准备输入的时刻，`prepared` 的档案可以放几天
再点确认；`updated_at` 会被评价、回收站、补积分推着走，拿它当结束时间等于把
「事后评了一次」也算进生成。两列都不回答「跑了多久」。

落地方式：

- `replacement_runs` 新增 `started_at` / `finished_at`，写入点统一收在
  `modules/template/run_clock.py`，所有会开始/结束一轮的状态赋值都走 `set_status`；
- 起点是 `queued`（用户点确认/继续），终点是 `success` / `failed` /
  `service_error` / `submission_unknown`；`waiting_free`、`waiting_executor`
  只是中途等待，不算结束；
- **重提重开一轮**：失败后隔天重试的任务，耗时属于这一次，不沿用第一次的起点，
  否则会显示成「耗时 20 小时」；
- 出参 `startedAt` / `finishedAt` / `elapsedSec`；未完成时 `elapsedSec` 按到此刻算，
  页面写成「已用 X」而不是最终值；
- 上线前的历史档案两列都是 `NULL`，页面显示「未记录」——不拿别的列猜；
  还没进入提交环节的显示「尚未开始」。

前端：`formatElapsed`（`shared/lib/format.ts`）负责「45 秒 / 1 分 20 秒 / 1 小时 2 分」，
`replacementElapsedText` 负责三种状态的分流，列表与详情共用同一个函数。

验证：后端新增 `tests/test_replacement_timing.py`（9 项）覆盖起点/终点/重提重置/
等待态不算结束/历史行返回 null，全量 `pytest` 通过；前端新增 `formatElapsed`、
`replacementElapsedText` 与列表-详情展示用例，`npm run verify` 通过。
迁移 `b8e4f1c7a902` 与 `alembic check`、`test_migrations` 一致。

## 2026-11-09：档案要自报「这条提示词是谁写的」

现象：调用档案与核对弹窗里，「查看最终提示词与归档路径」展开后第一行是
`\\Z4S-XI5Y\内容中心素材盘\yufan_ai_studio\runs\<run_id>`。用户读到的第一行
就是运行目录，很容易当成「本次提示词 = 那个 runs 目录」，于是质疑封装的
提示词到底有没有生效。

两处一起改：

- **顺序与措辞**：提示词排在前，标题写明「最终提示词（本次实际提交）」；
  归档路径退到最后一行、降一档字色，文案改成「归档路径（排查用，非本次提示词）」。
  路径本身仍要留着——排查时它是唯一坐标。
- **来源要说清**：`ReplacementRunOut` 新增 `promptMode`（`fixed` / `compiled`）
  与 `videoTemplateName`。`fixed` 表示逐字提交封装时写下的原文，弹窗直接写
  「来源：视频模板「X」的固定提示词，逐字提交，未改写」；`compiled` 则写明
  「按本次素材与替换范围自动编译」，免得用户去模板库里找一条并不存在的模板。
  任务记录列表同时给固定提示词任务打一枚「固定提示词」标记，不点开也能分辨。

`promptMode` 取自 `run.prompt_spec.promptMode`（`fixed_video_template` 才算固定）；
`videoTemplateName` 是 prepare 时写进 spec 的，早期档案只有 `videoTemplateId`，
这时才按 id 反查模板库（`_template_name`，带 `lru_cache`），查不到就只报
`fixed` 不报名字，展示层自己降级。

验证：`tests/test_video_template_binding.py::test_prompt_origin_tells_where_the_prompt_came_from`
覆盖现编/固定/老档案反查/模板已删四种情况；前端
`ReplacementTemplates.test.tsx` 新增两条用例（提示词在前且标来源、列表标记只给固定提示词任务）。

## 2026-11-10：一条原片替换「多位人物」（多槽位人物模板）

需求：米兰之夜原片里有三位女性，要一次把三张脸都换掉。人物包是一人一包、
各一张正面图，所以「3 个槽位」不可能由一个包填满。

**契约上怎么表达**：同一用途在 `bindings` 里声明多次即可，不需要新字段——

```json
{"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "front",
 "{{Mixed 3}}": "front", "{{Mixed 4}}": "front"}
```

初版曾要求视频第1位。当前用途绑定契约允许唯一的视频在任意连续编号位置，
「1/2/3=三个人物、4=视频」可直接封装，保留原提示词和编号；旧模板不自动重写。

**封装（三个入口同一套规则）**：`order_reference_ids` 填不满时不再一律报错，
而是看这是不是多槽位声明——
- 同一用途只声明一次（产品/单人模板）：照旧报「缺少足够的『正面身份图』」，那是包配错了；
- 同一用途声明多次（多人物）：能填几个填几个，剩下的留**空槽位**
  （`assetId=null`，`name="运行时选正面身份图"`），它是「运行时由第 N 张人物卡填」的声明。
当前仅多人换脸允许默认包只填第1位人物，其余身份待用户分别选卡；同一人物包的
额外正面图只能做该人物的辅助参考。实现位于 `video_template_store._resolve_media`
以及封装器 `template_packager/reuse.py`。产品、场景保持单包，不使用多人放宽规则。

**运行时怎么补位**：`PrepareReplacement.extraPackIds` 放第 2 位起的素材包
（第1位仍走 `packId` + `packVersion`），`extraPackVersions` 逐位锁定额外人物的发布版本。
服务端 `_selected_pack_inputs` 分别从每张人物卡的发布快照取第一张 `front`，
直接按人物序号对应提示词槽位，辅助图完整保留各自所属人物，不新建或合并人物包。
视频位置以 referenceBindings 为准，不能用同一人的第二张正面图占另一人的位置。
同一张卡填两个槽位会在准备前被拦（「同一张人物卡不能填多个槽位」）。
任务名取「人物A、人物B、人物C」，档案里三张图的顺序就是槽位顺序。

**工作台**：多人换脸优先按 `rules.referenceBindings` 中声明的 `front` 槽位数确定人数；旧模板回落到 `mediaOrder` 中被提示词引用的正面图，未引用的辅助正面图不计人数。
在「选择目标人物」的同一人物卡片网格中按出镜顺序多选；选中卡显示 1、2、3 等序号及已选人数，再次点击取消，后续编号连续前移。人数未满不能准备，选满后未选项暂时禁用，取消后可换人。全部取消仍保持多人选择模式。产品和场景始终单选。

验证：`tests/test_replacement_archive.py::test_multi_character_slots_are_filled_by_extra_packs`
覆盖「封装留空槽 → 三包按序填位 → 同卡重复被拒」；前端
`ReplacementTemplates.test.tsx`「三人模板在目标人物中按点击顺序多选」覆盖
未选满不可提交、序号、选满限制、取消重排、全部清空、辅助图不计人数及按序提交 `extraPackIds`；产品/场景单选另有回归覆盖。

第一条实例：`138e6cd3c85c47baac78e2d08ac57967`「米兰之夜 · 三人换脸」，
原片 `ac1d588d…`（与产品替换模板同一张卡），默认包「年轻女人 黑长波浪发」v1。

## 2026-11-10：模型渠道可选（LibTV / 阿里云百炼北京）

LibTV 排队不稳定时，可以在准备任务前换渠道。渠道是**每条任务冻结**的配置，
写进 `configuration.channel`，也进幂等指纹——换渠道等于换请求，不会复用旧任务；
已有任务不回写渠道，历史里没有该字段的一律按 LibTV 解释，不需要数据迁移。

| 渠道 id | 界面文案 | 上游模型 | 计费 |
| --- | --- | --- | --- |
| `libtv` | LibTV · Wan 3.0 · 720P | `libtv-wan3-replacement` | LibTV 积分队列 |
| `bailian` | 阿里云百炼 · Wan 3.0 · 北京 · 720P | `wan3.0-video` | 阿里云 API 按次 |
| `bailian-prime` | 阿里云百炼 · Wan 3.0 高速版 · 北京 · 720P | `wan3.0-video-prime` | 阿里云 API 按次 |

**界面**：单条与批量配置里都有「模型渠道」下拉，数据来自
`GET /templates/replacement-catalog` 的 `channels`；服务端认为不可用的渠道置灰并
给出原因（未配密钥 / 未注册供应商）。核对弹窗、历史列表、详情弹窗显示本次冻结的
渠道与计费方式；百炼任务不显示「积分加速」勾选（那是 LibTV 的概念）。
重试沿用原渠道，不静默换道。

**示例与占位符**：LibTV 的 `{{Mixed N}}` 与百炼的「图N/视频N/音频N」编号不同。
百炼在准备阶段按素材类型分别计数重编译提示词（`core/prompt_refs.translate_prompt`，
替换链与驱动共用同一份），并递增 `compiler_version`，避免两类编号混用。

**配置**（`api/.env`，都已写进 `.env.example`）：

```
# 百炼不进 AI_PROVIDERS：它在 drivers/ 里自动载入（2026-11-11 迁入）
AI_PROVIDERS=echo,libtv
BAILIAN_BASE_URL=https://llm-cz0qm1j8cl2weo19.cn-beijing.maas.aliyuncs.com/api/v1
BAILIAN_API_KEY=sk-...
BAILIAN_TIMEOUT_SEC=45
BAILIAN_DOWNLOAD_TIMEOUT_SEC=45
```

> `agnes` / `bailian` **都不要写进 `AI_PROVIDERS`**：它们是模型驱动
> （`modules/model_driver/drivers/`），启动时自动载入。写进去会因「同名但模型清单
> 不同」或重复注册导致启动失败。百炼的渠道可用性由驱动自己回答
> （`InputPreparer.unavailable_reason`），没配密钥时渠道目录直接标不可用。

`BAILIAN_BASE_URL` 里第一段是业务空间 ID，必须与密钥同属北京地域，跨地域会失败。

**为什么不直接重试**：百炼按次计费，提交回执（`records/bailian-submission.json`）
先落盘再发请求。网络不确定时只允许查询已记录的 `task_id`，绝不自动重提；
确认上游确实没有任务后，才由人工「重新确认」把未知回执归档再提交。
成片下载到 `outputs/generated.mp4`，沿用既有归档与发布流程。

**验证**：`tests/test_bailian_provider.py`（编号重编译、上传契约、状态归一、回执复用、
不确定提交、下载、素材约束、纯文生视频不带 `media` 字段）、
`tests/test_replacement_channels.py`（默认渠道、非法渠道、渠道冻结、回执恢复、
计费隔离）、前端 `ReplacementTemplates.test.tsx`「单条和批量准备保存所选渠道，
换渠道使用独立请求」。北京节点用 `GET /uploads?action=getPolicy` 做过免计费鉴权探测。

## 2026-11-10：改固定提示词只能原地改

需求是「把某条模板的提示词换成新的」。上一版没有这个入口，只有创建 / 设固定 /
绑原片，于是很容易想到「再封一条覆盖它」——这条路是错的：`find_for_run` 在同原片
同类型下按 `(pinned, createdAt)` 排序取第一条，**新建的那条永远排在旧条后面**，
改了等于没改；同一枚原片还会因此挂着两份同类型模板，排查时说不清跑的是哪份。

所以补的是原地改：

- `PATCH /api/v1/templates/video-templates/{id}/prompt`，body 只有 `prompt`；
  `video_template_store.update_prompt()` 负责改。
- 媒体清单、素材包、名称、档位、原片绑定都不动，只换原文与由此派生的引用关系：
  - `promptSha256` 重算；
  - 新原文点名了更靠后的槽位时，`rules.referenceBindings` 向后扩展
    （用途取自该位媒体自身的 role），**不缩短**——没被点名的图仍完整插入接口，
    只是 `inPrompt=false`；
  - `rules.promptTokenAllowlist` 换成新原文实际引用的编号；
  - 原文不再引用 `{{ReplacementScope}}` 时清掉 `rules.scopeOptions`：
    固定提示词是逐字提交的，留着范围选择器只会点了没反应。
- 校验沿用创建时那套：越界 `{{Mixed N}}`、只引用图片不引用原片、一个图片都不点名、
  引用了 `{{ReplacementScope}}` 但模板没声明范围、非法占位符，全部当场拒绝；
  多人换脸的「每人一个 front 槽位」规则也在这里复检。
- **先规范化再算摘要**：`CamelModel` 的 `str_strip_whitespace` 会去掉首尾空白，
  而 `model_copy` 跳过校验。少了这步，目录里会存下带尾换行的原文、却拿它算 sha，
  读出来又是没有换行的版本，两边对不上。

前端暂时没有编辑入口（本次只改数据），要改还是走这条接口或直接调 store。
提示词改动影响此后每一次生成，路由留审计 `template.video_template.update_prompt`。

验证：`tests/test_video_template_prompt.py`（5 项）覆盖扩槽、不缩短、范围参数清理、
五类非法引用、规范化与摘要自洽；全量 `pytest` 1220 通过，`ruff` 干净，
`export-openapi`、`gen:mock-coverage` 已同步。
