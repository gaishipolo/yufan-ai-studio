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
| HEAD | 本地领先 `origin/main`：`3aaf40f` 起多笔**未推送**（含「任务中心接生图」与「模板封装迁移阶段 A/B/C1」，推送前先问用户）；笔数以 `git rev-list --count origin/main..HEAD` 为准，hash 见 `git log --oneline origin/main..HEAD`。未跟踪的只有 `var/`、临时脚本（如 `var_fix_fe.py`）、快捷方式 `.lnk`、`vite.config.ts.timestamp-*.mjs` 与用户私有的 `EXCHANGE_TOPICS_2026-11-12.md`，都不该进提交 |
| CI | 此前连续多轮全绿（含 `bc0940e`）；本轮推送后照 §4 核对。PG 迁移仍在观察期（`continue-on-error`） |
| 本机门禁 | 本轮（2026-11-14 真机实测封装链）改 `modules/template_pack/manifest_service.py`，按 §3.1 定级 **`targeted`**：4 个测试文件 **89 passed / 0 failed**；`ruff check src tests` 0；`mypy --platform win32` 293 文件 0；`export-openapi --check` 四份无差异（接口零变化）。前端本轮未改代码（上一笔 `dad6873` 的 `npm run verify` 全绿） |
| 规模 | 12 个业务模块 · **118 条路径 / 148 个端点**（文本资产 +7）· **57 个权限码**（本轮未新增）· **4 份**契约生成物 |
| 后端 | `api/.venv/Scripts/app.exe serve --reload` → 8080，前缀 **`/api/v1`**（裸 `/health` 会返回 40400） |
| 前端 | `cd studio && npm run dev` → 5173，`/api` 已代理到 8080 |

## 1. 这一轮（11-11）做了什么

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

## 7. 文档地图（哪份是活的）

| 文档 | 状态 |
|---|---|
| `ARCHITECTURE.md` / `EXTENDING.md` / `CODE_STYLE.md` / `REVIEW_CHECKLIST.md` | **活文档**，改结构/约定时同步 |
| `TODO.md` | **活文档**，唯一待办清单 |
| `MODEL_DRIVER_STANDARD.md` | **活文档**，含 §11 老路径豁免登记 |
| `OPS.md` | **活文档**，部署与告警 |
| `ROUTES.md` / `MOCK_COVERAGE.md` | 生成物，`--check` 校验，别手改 |
| `TEMPLATE_LIBRARY_V2.md` | **活文档**，模板库 v2 的五向 I/O 契约与待拍板项（2026-11-13 新写） |
| `TEMPLATE_SPEC.md` | **设计定稿、待实施**（2026-11-14）：模板封装泛化为 "槽位 + 两段提示词 + 模型绑定"，封装定表 / 模板库填表 / task 执行三层；替换链收编放在 P3。与 `TEMPLATE_LIBRARY_V2.md` 无关（那份是任务中心的旧计划） |
| 历史评审 / 计划 / 需求 / 设计文档 | **2026-11-13 已清理**，只留 git 历史；不要从 git 里捞回来当现状读 |

## 8. 已知偶发（不是回归，别急着改代码）

| 现象 | 判据 |
|---|---|
| `test_archive_blobs` / `test_archive_index` 报 `OSError: SHFileOperationW 失败: 0x78` | safe-delete shim 回收站失败，**单跑必过**；同一轮里其余用例照常 |
| `test_scheduler::test_leader_only_lease_call_does_not_block_the_event_loop` 偶红（`worst` 80ms vs 50ms 阈值） | 负载敏感的计时断言，单跑 11 passed；机器空闲时复跑即绿 |
| 全量 3~6 分钟而不是 200s | 机器上有别的负载（浏览器/开发服务器）。看 `--durations` 有没有异常项再判断 |

判断顺序：**单跑复验 → 换全新 `--basetemp` 复跑 → 才怀疑代码**。反过来做会浪费一整轮。
