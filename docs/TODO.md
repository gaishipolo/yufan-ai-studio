# 全局待办

> 只放「已知还没做」的事。修完就删掉该行，别改成 ✅——这份文档的价值在于它短。
> 每条都带位置和验收口径，避免下一轮又靠猜。
> **前后端均由 AI 负责（2026-11-07 起）**：「落点」只标改动位置，不是人员分工；
> 「产品决定」表示需用户拍板。
>
> 来源：`CODE_REVIEW_2026-11-06_R11~R14`、`CODE_REVIEW_2026-11-06_FE6`、
> `EXTENSIBILITY_GAPS.md` 的 B1/C1/C2、素材分支合并时新发现的项，以及
> **2026-11-11 全面审查**（框架 / 资产库 / 模型启动，P0×2 · P1×9 · P2×11 已全修）与 PR#4 评审遗留。
> 最后更新：2026-11-11 20:50 —— **文本资产落地（方案 B+C）**：`kind=text` 仍是一条
> `library_assets` 行，另加不可变快照表 `library_text_versions`；站内新建 / 在线编辑 /
> 版本历史 / 恢复走 `/library/assets/{id}/text...`，引用注入走 `{{Text N}}` +
> `textLibraryIds`（生图、视频、替换链三条链各一份冻结快照）。只收 `.txt`/`.md`、
> 严格 UTF-8、≤1MB，Markdown **不转 HTML**（取件固定 `text/plain` + `nosniff`）；
> 图片 / 视频 / 封装选择器默认排除文本，需显式 `kind=text` 才出现。
> 迁移 `e2b7c4a9f310`，用例 `test_library_text_assets.py` / `test_library_text_refs.py`。
> 早前：资产库标签改**两级**（一级分组、二级才能打标），
> 存量按「产品 → 3030 / T35，角色 → 菜菜」回填，其余留一级；资产库页面字号整体调大一档。
> 口径：`library_tags.parent_id`（空串=一级）、`UNIQUE(name, parent_id)`；
> 计数 / 筛选 / 打标三处同一套口径（`tags.py`），归层命令 `app library init-tag-tree`。
> 早前：T34 补完：参考图不只本地上传，加了素材库侧边悬浮栏
> （搜索 / 标签筛选 / 拖拽到参考图区）。
> 早前：T34 落地（生图参考图：`referenceLibraryIds` →
> `reference_json` → Data URI 内联，前端按模型的 `maxReferences` 出控件）。
> 早前：T33 修复（`ModelSpec.internal` + `public_models()`）；
> T32 落地（`bailian` 迁进 `drivers/`，替换链改走 `InputPreparer` 可选协议）；
> T31 口径已定（素材直传公网 OSS 是有意设计，不是合规遗漏）；T30 拍板**暂不动**（保持观察期）。
> 交接现状见 [`HANDOFF.md`](HANDOFF.md)。
> 剩余：业务侧 T2/T8/T9/T10/T22/T23/T24，架构侧 T25/T26（只剩唤醒）/T29/T30（暂不收口），
> T35（PR#5 自报的六条遗留）

## 一眼看完

| 编号 | 事项 | 影响 | 落点 |
|---|---|---|---|
| T2 | 前端不展示 `externalId` / `previousExternalId` | 文案让用户「凭任务号核对」，页面拿不到 | 前端 |
| T8 | 轮询节奏全局化（C1） | 视频与生图共用一个间隔 | 后端 |
| T9 | 重试是作业级不是任务级（C2） | 任务重试语义与用户预期不一致 | 后端 |
| T10 | `archivePath` 绝对路径进 HTTP 契约 | 服务端盘符泄漏给浏览器 | 产品决定 |
| T22 | 归档检索与批量写太弱（C 档） | 几十万级素材找不到、整理要靠逐条点 | 产品决定 |
| T23 | 上传秒传/断点、视频派生预热（C 档） | 300MB 白传一遍；首个点开视频的人等转码 | 后端 |
| T24 | `MediaFile.path` 装的是 key，字段名/描述没跟上 | 契约语义漂移，后来者会当路径拼 | 后端（小） |
| T25 | 多实例部署还差三处硬边界 | 配额首次并发、预签名 TOCTOU、运行工作区仍要共享盘 | 架构 |
| T26 | db 队列没有**跨进程**唤醒（空转已退避，同进程已能叫醒） | API 与 worker 分进程时，空闲入队到取走最坏 1 秒；万级 QPS 要换 Redis 驱动（协议已备好） | 架构 |
| T29 | 指标是单进程语义，未做多进程聚合 | 多 worker 部署要靠 Prometheus `sum by`；本机 `curl /metrics` 只看到一部分 | 架构 |
| T30 | PG 迁移闸在观察期（`continue-on-error`），尚未成为硬性要求 | PG 特有的默认值/条件索引差异要等它稳定后才真正拦住；**2026-11-11 拍板：暂不动** | 架构 |
| T33 | 替换链专用模型出现在通用模型目录 —— **2026-11-11 已修** | 给 `ModelSpec` 加 `internal`，目录层用 `public_models()` 过滤 | 后端（小） |
| T34 | 生图链路没有参考图，Agnes 实际只能文生图 —— **2026-11-11 已修** | `referenceLibraryIds` + `reference_json` + Data URI 内联，前端按 `maxReferences` 出控件 | 后端 + 前端 |
| T35 | PR#5 自报的六条遗留（409 未进契约、continuation 不查 rowcount、`_absorb` 下界、`resume` 全扫、`publish_output` 失败仍记 success、libtv 监督无超时） | 进度、启动耗时与「成功但没片子」这类静默问题 | 后端 |
| T36 | 2026-11-11 主线复审剩下的九条 P2 —— **2026-11-12 已修** | 都是「不报错但口径不对」；修法与理由见下方段落 | 后端 + 前端 |

---

## T2 · 前端不展示供应商任务号（R13-08）

409（`confirmCharged`）和恢复失败文案都写着「请凭供应商任务号核对结果」，
`schema.ts` 也同步了 `externalId` / `previousExternalId`，但 `studio/src`
除生成物外零引用——用户被要求拿一个页面看不到的东西。

**验收**：生图详情/列表能看到并复制 `externalId`；「已确认扣费重试」之后能同时看到
历史任务号（`previousExternalIds`）。

## T8 · 轮询节奏全局化（C1）

仍读 `settings.generation_poll_interval_sec`，视频（3 分钟一轮）与生图（6 秒一轮）
共用一个值。`ModuleSpec` 已经有 `startup` / `recovery_kinds` 这类声明式插槽，节奏
也可以照抄。

**验收**：驱动或模块能声明自己的轮询间隔；未声明时回落到全局值。

## T9 · 重试是作业级不是任务级（C2）

仍是 `Job.max_attempts`（队列作业级）。用户点「重试」和框架自动重投共用一套计数，
语义混在一起。

**验收**：任务级重试额度（`submit_attempts` 之外单独一列或 run 元数据），
自动重投与人工重试分开计。

## T10 · `archivePath` 绝对路径进 HTTP 契约（R14-04 残留）

`modules/template/replacement_schema.py:45` 的 `archive_path` 直接返回
`Z:\yufan_ai_studio\runs\...`，`features/replacement-templates/ReplacementTemplates.tsx:168/176`
原样显示给用户。合并删掉了归档页之后，**这已经是唯一还在用绝对路径的地方**。

要不要改成相对路径 / 脱敏，是产品决定：它是「归档位置」的直观展示，改掉会改变用户
看到的东西（运维可能真的按这个路径去共享盘找文件）。

**验收**：产品给结论。保留 → 在 schema 字段描述里写明「这是服务端路径，勿用作下载链接」；
改 → 只回 `runId`，前端拼展示文案。

## T22 / T23 · 素材库的产品能力（A/B 做完后剩下的）

A/B 档解决的是「数据不会丢、空间不涨、部署可自检」；这些是**好不好用**，
等真实使用反馈再排期，别提前堆功能。

- **检索**：`list_assets` 只有 category / scope / keyword / state，没有排序、
  时间/大小/分辨率筛选，`MediaFile` 没有 tags；`pageSize` 上限 200 + offset 分页。
- **批量写**：上传有 `upload-batch`，改名/分享/移入回收站仍是单条 PUT。
- **上传**：`_find_by_digest` 已能服务端判重，却没有「先传摘要 → 已存在直接返回」
  的秒传；断线只能整段重传 300MB。
- **派生**：`_derive_clip` / `_create_preview` 在请求内 `to_thread` 跑 ffmpeg，
  首个点开视频的用户要等转码，没有预热队列与失败重试。

**验收**：按真实素材量与失败率定优先级，不要一次全做。

## T24 · `MediaFile.path` 的语义漂移

`core/media.py` 的 `path` 现在装的是内容寻址的 `storageKey`（哈希），
`ArchiveDetail.tsx` 已改叫「存储键」，但契约字段名与描述仍是「路径」。
另外 `ArchiveSearch` 前端手写窄类型与后端 `Literal` 靠注释手工同步。

**验收**：要么在字段描述里写清「这是对象 key，不是文件系统路径」，要么改名并
同步四份生成物；`ArchiveSearch` 改由 `schema.ts` 派生。

## T25 · 多实例部署剩余的三处硬边界（封顶后明确留下，不是遗漏）

本轮把「能不能安全删数据 / 漂移能不能自愈 / 能不能扩展到新介质与多副本」解决了，
但**没有**声称系统已经可以无脑横向扩容。以下三处是结构性边界，各自需要独立工程：

1. **配额不是完全原子**：`archive_repository.lock_owner_for_quota()` 在 PostgreSQL
   下用 `FOR UPDATE` 串行化同一用户的「读 sum → 判上限 → 写行」，但 SQLite 没有行锁，
   且用户**首次**登记时无行可锁。彻底原子要引入 `usage` 表的条件 UPDATE
   （`SET used = used + :n WHERE used + :n <= quota`），代价是多一处需要维护的双写。
2. **预签名有 TOCTOU**：`ArchiveAssetService._serve()` 先 `exists()` HEAD 再 307，
   另一个实例的 GC 可以在这两步之间删掉字节。要消除需要「签名指向版本化 key」或
   删除改标墓碑、由后台统一回收。
3. **运行工作区仍是共享盘**：`REPLACEMENT_RUNS_ROOT` 必须所有实例挂同一份，
   `catalog/runs` 只是指针。执行器（bridge.mjs / ffmpeg）需要真实本地文件，云化等于
   重写执行器输入层，不是换配置能解决。

`LEADER_ELECTOR=backend` 只解决「不可逆周期任务全集群只跑一次」，不解决上述三条。
`app doctor` 会在 GC 开启而选主仍是 local 时直接 FAIL。

**验收**：三处各有方案文档 + 多实例集成测试（至少两个进程并发登记/删除/取件）。

---

## T26 · db 队列没有跨进程唤醒（同进程已经有了）

`QUEUE_DRIVER=db` 解决了「重启丢任务」和「崩溃任务卡死」，但取任务是**轮询**。

**已做的两半**：

1. **空转退避（2026-11-07）**：间隔从 `QUEUE_POLL_INTERVAL_SEC`（0.2s）翻倍到
   `QUEUE_IDLE_MAX_INTERVAL_SEC`（1.0s）封顶，领到任务立刻复位。稳态下每个 worker 的
   空转查询从 5 次/秒降到 1 次/秒，可用 `queue_polls_total{outcome="miss"}` 实测。
2. **进程内唤醒（2026-11-08）**：`enqueue(delay=0)` 成功后置位本进程的 `asyncio.Event`，
   `dequeue` 用 `wait_for(event.wait(), timeout=退避间隔)` 代替纯 `sleep`。
   默认部署（`WORKER_ENABLED=true`，API 与 worker 同进程）下，入队 → 取走从
   「最坏 1 秒」变成微秒级。`delay > 0` 入队**不唤醒**——那些行在 `run_at` 之前不可见，
   而「短 job + 延迟重投」正是延迟入队的大户（由查询次数断言守住）。

**还缺的是跨进程**：另一个实例的 event loop 不在这个进程里，`Event.set()` 够不着。
多实例（API 与 worker 分进程）的最坏延迟仍由退避封顶值决定。真要 0 延迟只能靠
PostgreSQL 的 `LISTEN/NOTIFY`（写完 `NOTIFY`，worker 侧长连接被唤醒）或换 Redis——
前者 sqlite 没有，会变成「两套取任务路径」，所以没做。

协议层已经留好入口（`infra/queue/base.py` 的映射表 + `docs/EXTENDING.md` §5.2.4），
换 Redis 只需新增 `infra/queue/redis.py`，业务不改。

**验收（剩下的）**：`QUEUE_DRIVER=redis` 可切换，且 `tests/test_db_queue.py` 的语义
用例在新驱动上同样通过（协议一致性是硬要求）；或 db 驱动接上 `LISTEN/NOTIFY` 并有
「入队到取走的 P99」实测数据。

---

## T27 · 限流额度的两处已知边界 —— 已修（2026-11-08）

1. **额度不能按接口配**：现在有两层覆盖，都不改代码——
   `RATE_LIMIT_OVERRIDES='{"task.create": 120}'` 按 scope 单点调，
   `RATE_LIMIT_SCALE` 整体缩放（`0` = 全关）。
   「新入口忘了加配置」不再是缺口：没配就用路由上的基线。
   配置写错（非法 JSON、次数不是非负整数、scope 带冒号）启动即抛，
   写了代码里不存在的 scope 由 `app doctor` 的 `runtime.rate_limit` 判 FAIL。
2. **固定窗口 2× 边界误差**：换成两个桶加权的滑动窗口
   （`estimate = curr + prev × (1 − 已过比例)`），跨整点不再凭空多出一份额度。
   没有引入 `ZADD`/`Lua`——那会把 Redis 专有命令写进核心逻辑，
   断掉「以后只换驱动」这条路。

顺带修掉两个在同一条链路上一旦出事就很难查的问题：

- **被拒的请求也计数**：会导致「照着 `retryAfter` 重试越试越超」，小额接口永远出不来。
  现在被拒时显式回滚，`retryAfter` 是能兑现的承诺（`test_retry_after_really_unblocks`）。
- **`get_settings()` 清缓存后返回新对象**：所有 `from core.config import settings`
  过的地方还绑着旧对象，症状是「改了环境变量一半生效一半不生效」。
  改成原地刷新单例（`reload_settings()`），一个进程只有一份配置。

**验收已完成**：`tests/test_ratelimit.py` 28 个用例（含边界不加倍、回滚、承诺兑现、
配置对账），`app doctor` 多一项 `runtime.rate_limit`。

---

## T28 · 备份没有调度与保留策略 —— 已修（2026-11-07）

`@periodic("core.backup")` 每日自动备份并清理过期备份，`leader_only=True`
（多副本各备一份纯浪费磁盘与读锁，抢不到租约这一轮跳过即可）。
配置：`BACKUP_AUTO_ENABLED`（默认关）/ `BACKUP_DIR` / `BACKUP_INTERVAL_HOURS` /
`BACKUP_RETENTION_DAYS`（0 = 永不删）。

清理的三条自我约束（都有用例）：只认 `backup-*` 且带 `manifest.json` 的目录、
按清单 `created_at` 判断而非目录 mtime（`cp` 过的备份 mtime 会变新）、
`retention_days <= 0` 直接返回。`create_backup` 的 dump 改走线程，
避免 sqlite backup API 阻塞事件循环。

## T29 · 指标是单进程语义

计数器在进程内存里：多 worker / 多副本时每个进程各暴露一份，
`curl localhost/metrics` 只看到其中一个。Prometheus 侧用 `sum` 聚合同名序列没问题，
但任何「直接读端点做汇总」的脚本都会算错。

刻意不做多进程聚合（需要共享存储或 pushgateway，都会引入新的故障域）。
**验收**：文档写清（已写进 `docs/OPS.md` §9），或提供一个 `--json` 的聚合视图给脚本用。

## T30 · 迁移漂移守卫只覆盖 sqlite

`tests/test_migrations.py` 用临时 sqlite 跑 `upgrade head` + `compare_metadata`。
这能抓住 99% 的「改了模型没写迁移」，但 sqlite 没有 PG 的条件索引、部分方言类型、
`server_default` 表达差异——只在 PG 上才会出现的漂移仍要靠 CI 里的
`alembic check`（需要 PG service）兜。

**已加（观察期）**：`.github/workflows/ci.yml` 的 `postgres-migrations` job，
真实 `postgres:16-alpine` 上跑 `upgrade head` → `check` → `downgrade base` → `upgrade head`
（最后两步顺带验证迁移可回滚）。当前 `continue-on-error: true`：没有历史基线时先用它
暴露问题而不阻塞合并。

**收紧条件**：连续 3 次 CI 通过后去掉 `continue-on-error`，之后它就是硬性要求。
**2026-11-11 状态**：该 job 已连续 3 次通过（run #62 / #63 / #64，其中 #63 整体红是因为
pytest 里一条用例，PG job 本身 success）——条件已满足，**只差拍板**。去掉它之前先确认
「`downgrade base` 清库重升」对共享环境没有副作用（job 内是临时 service，无副作用）。

**2026-11-11 拍板：暂不动。** 保持 `continue-on-error`，观察期继续。也就是说这道闸
目前只负责**暴露** PG 方言差异，不阻塞合并；看到它红了要自己去看，别当成必过项。
条件与前置动作仍如上，等下一轮要收紧时直接照做。

## T31 · 模板链素材直传阿里云 OSS（2026-11-11 已定：保持直传）

**结论：这是有意设计，不是合规遗漏。** 部分视频模型（Wan 3.0）**只接受公网 URL**
作参考素材，不认本地文件；要让它们工作，素材必须先在公网可达的地方，所以替换链把
素材直传到阿里云 OSS，再把 `oss://` 直链交给模型。素材因此离开本机是**功能前提**，
不是可选优化。

留档说明（供合规沟通时引用）：

- 直传目标由百炼签发（`GET /uploads?action=getPolicy`），走的是**临时素材目录**
  （`upload_dir` 带有效期，提交时校验 `< 47 小时`），不是我们的长期存储；
- 上传不带我们的 `Authorization`，凭据是签发的一次性 policy/signature；
- 素材只服务该次生成，归档盘与素材库的路由不经过这里；
- 三个渠道里只有 `bailian*` 走这条路；`libtv` 跑本地执行器、不上传素材，`agnes` 的
  参考图由调用方以 URL 形式给出。

若将来要换成「不出本机」，唯一可行的是让上游支持从我们的对象存储拉取（或支持
Base64 直传），届时改 `drivers/bailian.py` 的 `prepare_inputs` 一处即可；现有代码
已把「上传」与「提交」分开，不需要动生成语义。

## T33 · 替换链专用模型出现在通用模型目录（T32 顺带发现）

**2026-11-11 已修**：`ModelSpec.internal: bool = False`，`bailian` / `libtv` 的三条
模型声明都带上；`ModelRouter.public_models()` 是给人挑的唯一来源
（`GET /models`、`GET /model-driver/models`、`task/params.py` 与 `image/service.py`
的「可用模型」提示都改用它）；`resolve()` / `provider_for()` 仍走 `models()`，
替换链不受影响。用例 `tests/test_internal_models.py`（5 条，含「默认值是内部模型时
不得把默认标记挪给别的模型」）。口径与注意事项见 `MODEL_DRIVER_STANDARD.md` §2.5。

原始记录（供追溯）：`GET /models?capability=video` 直接返回 `router.models()` 全量，于是
`libtv-wan3-replacement`、`bailian-wan3-replacement`、`bailian-wan3-prime-replacement`
会出现在工作台的视频模型下拉里。选中提交会立刻 400「调用档案标识不匹配」——这两个
驱动要求 `params.archiveRunId` 等于任务 id，那是替换链的调用约定，通用入口不提供。

**为什么当时才显形**：`bailian` 迁进 `drivers/` 之前，本机 `.env` 的
`AI_PROVIDERS=echo,libtv` 里没有它，目录里看不到；`libtv` 那条其实一直都在，只是
没人从生成页去选它。

## T34 · 生图链路没有参考图 —— **2026-11-11 已修**

> 落地口径记在这里，避免以后又按「往 `paramSchema` 塞一个图片字段」的思路改回去：
>
> - **入参** `GenerateImageIn.referenceLibraryIds`（素材库 v2 资产 id，按顺序生效）。
>   刻意不叫 `referenceAssetIds`：那个名字在架构守卫里等于「往自己表里存了旧
>   `assets.id`」，会被要求声明 `asset_reference_sources` —— 而这里存的是 v2 资产 id，
>   删除保护走资产库自己的机制。
> - **落库** `ImageJob.reference_json`（`[{assetId, storageKey, mimeType}]`）。存
>   `storageKey` 是因为构造请求那一步（`build_request(record)`）没有数据库会话。
> - **受理前校验** `ImageService._resolve_references`：张数上限取模型的
>   `max_references`、`kind` 必须是 image、`storage_key` 能推出 png/jpeg/webp、
>   单张 ≤ 6MB，可读性走新加的 `LibraryLookup.readable_ids`（判据仍是
>   `library/access.py` 那一份）。**资产 id 不是所有权凭证**，不查这一道就等于
>   让人把别人的私有素材交给供应商复制一份再取回来。
> - **提交** `build_request` 读字节转 Data URI（供应商拿不到我们的私签链接）；
>   `archive_request` 的快照带 `referenceLibraryIds`。
> - **前端** `entities/image/ui/ReferenceImages.tsx`：两条入口落到同一份资产 id ——
>   本地上传（先入库换 id 再提交）与素材库悬浮栏（拖拽 `/` 点 ＋）。原本只有前者，
>   等于「库里已有的素材要再传一遍」，所以 2026-11-11 补了后者。
>   入口是否出现由 `ModelSpec.max_references`（契约 `maxReferences`）决定。
>   参考图**不进** `paramSchema`：那边的值只能是标量。
> - **悬浮栏** `entities/library/ui/LibraryFlyout.tsx`：**不加遮罩**。Modal 那层
>   backdrop 会盖住左侧参考图区，拖拽就落不下去 —— 这也是它与 `LibraryPicker`
>   的分工差别（后者带确认按钮，适合一次挑一批再回别处）。只查
>   `GET /library/selection`（硬过滤 `state=active`），前端固定 `kind=image`：
>   参考图只收 png/jpeg/webp，把视频摆出来只会让人拖了才被拒。
> - **拖拽契约** `shared/lib/libraryDrag.ts`（`application/x-yufan-library-asset`）：
>   放 `shared` 而不是某一边的 `model/`，否则 `entities/image` 要反向依赖
>   `entities/library`。用自定义 MIME 是因为 `text/plain` 谁都能拖进来 —— 拖一段
>   提示词就会被当成拖了一张素材。载荷带 `name`/`url` 只为立刻画缩略图，
>   提交的仍只有 id。

---

## T35 · PR #5 自报的六条遗留（合并时原样接受，未修）

这六条是 PR 作者自己列的「未处理」，合并时逐条看过，都成立且都不是本轮引入的
回归。按代价从低到高排：

1. **`409 state_changed` 没写进 OpenAPI 契约**（小）：前端已按 `details.reason`
   判定（`shared/api/types.ts` 的 `isStateChanged`），契约里看不到这个响应码。
   补法是给相关写路由声明 `409` 响应，再重新生成四份生成物。
2. **continuation staging 不查 rowcount**（小）：`next_job` 提升为 `pending` 时
   没校验受影响行数，缺 0 行只会静默不推进。要么查 rowcount 报 warning，要么
   加一条「连续 N 轮没推进」的指标。
3. **`_absorb` 用 `state_version` 做单向下界**（中）：长上传期间的一次旁写会让
   合法的 `uploaded` 回执被判成旧版本丢掉。
4. **`resume()` 仍扫全部「失败史」**（中）：无 limit、无分批，失败记录积累多了
   每次重启都变慢。
5. **`publish_output` 失败仍记 success**（中）：成片没进归档时 `output_key` 为空，
   状态却是成功，用户以为有片子。
6. **`libtv` 监督进程 `communicate` 没有外层超时**（中）：卡住会一直占用线程池
   槽位，与调度器 handler 超时那轮的修法可以参考。

**验收**：逐条补用例后从本表删掉。

---

## T36 · 2026-11-11 主线复审剩下的九条 P2

**2026-11-12 已全部修完，九条从本表删除。** 上一轮复审共留 18 条，前 8 条
（`b7c4e2a` 迁移放宽 `library_asset_generation.prompt`、`runner._finish_success`
移出轮询超时预算、队列续接查 rowcount、参考图判 `state=="active"`、限流
`limit >= cost` 与「缩放不能算成 0」、封装封面按两列数引用、投放区接住桌面文件、
选择器确认按 `value` 顺序且不丢跨页项）在 2026-11-12 推的 `e12bb98..6110a06` 里。

这九条的处理方式（留在这里防止重复劳动，也说明**为什么这么改**）：

| # | 结论 | 落点 |
|---|---|---|
| 1 | 站内新建同内容**返回已有那条**（秒传口径，不新建第二份）；编辑撞另一条同名正文时报 409 并在 `details` 里给 `duplicateId`/`duplicateName` | `modules/library/text_assets.py` |
| 2 | 「解 A 同时在同一位绑 B」→ token **原地保留**指向新绑的；判据是「**新位是不是新加的**」，否则换序时会出现两个 token 指向同一份 | `features/library/textRefs.ts` |
| 3 | `/library/tags` 加 `kind` 参数，计数与列表的类别筛选同口径 | `modules/library/tags.py` + `LibraryFlyout.tsx` |
| 4 | NUL 在**落盘前**拦（`encode_text` 是所有文本落盘路径的唯一出口），用户看到 400 而不是 500 + 孤儿 blob | `infra/media_probe.py` |
| 5 | 读路径（轮询/重试/恢复）**不抛错**：找不到正文就把占位符原样留着。受理期仍由 `validate_text_refs` 严格拦 | `core/library_text.py` |
| 6 | 注入总量上限 `MAX_TEXT_REF_BYTES = 256KB`，与「几份」两个闸门缺一不可 | `core/library_text.py` |
| 7 | 重复声明时 `max_series` **取 min**，上限不再取决于 import 顺序 | `core/metrics.py` |
| 8 | `/model-driver/models` 补 `maxReferences`；`/model-driver/tasks` 按 `spec.max_references` 校验张数 | `modules/model_driver/` |
| 9 | 三条历史链路声明 `strict_model = True`；新增 `ModelUnavailable` 异常类型，轮询侧**当场判失败**而不是退避到超时。**但判不判失败由流水线声明**（`fail_on_model_gone`）——替换链跑的是已付费任务，渠道停用只说明联系不上供应商，判失败会诱导重试→重复计费，它声明 `False` | `generation/pipeline.py` + `generation/runner.py` |

顺带修掉的：停机收尾改成**每一步各自兜住**（`app._shutdown_step`）——之前
调度器那一步抛非 `TimeoutError` 的异常会让 `finally` 中断，`worker.stop()` /
`close_http()` / `dispose_engine()` 一个都不执行。

---

## 已复核关闭（别再重做）
2026-11-06 合并后逐条核过，以下都已修好，留在这里防止重复劳动：

| 编号 | 结论 | 证据 |
|---|---|---|
| T32 百炼是否迁进 `drivers/` | 已迁（2026-11-11） | `modules/model_driver/drivers/bailian.py` 自动载入，不进 `AI_PROVIDERS`；替换链改走可选协议 `infra.ai.base.InputPreparer`（上传 + 自报可用性），提示词重编号下移 `core/prompt_refs.py`；回执路径 `records/bailian-submission.json` 不变。用例 `tests/test_bailian_provider.py`（29 条，含「自动载入」「未配密钥在选择渠道时就说清」「提交预算覆盖上传」） |
| FE6-01 骨架常量与注册表脱钩 | 已修 | `src/app/router.test.ts:120` 断言 `HOME_PATH` 指向的路由确实存在 |
| FE6-02 `bare` 路由丢 `permissions` | 已修 | `create-registry.test.ts:270` 改成「声明即报错」 |
| FE6-03 生成物字节比对在 Windows 假红 | 已修 | `.gitattributes` `* text=auto` + `ROUTES.md` / `MOCK_COVERAGE.md` 钉 `eol=lf` |
| FE6-04 注册期校验两空档 | 已修 | `create-registry.test.ts:254/261` 两条用例；实现见 `create-registry.ts:175` |
| FE6-05 `MOCK_COVERAGE.md` 无一致性守卫 | 已修 | `scripts/gen-mock-coverage.mjs` 已支持 `--check` |
| FE6-06 `Slot` 用数组下标做 key | 已修 | `shared/registry/Slot.tsx:26` 用 `moduleId:seq` |
| R13-01~07 / R14-01~03 | 已修 | `dd5fbd2`、`787b88a`、`cad40a9`，见 `.workbuddy/memory/2026-11-06.md` |
| R12-* / R11-* | 已修 | `aa9c5e4` |
| T28 备份无调度与保留 | 已修 | `db/backup.py` 的 `backup_loop` / `prune_backups`，用例见 `tests/test_backup.py` 后三条 |
| 就绪探针 down 却返回 200 | 已修 | `modules/health/router.py` 在 `runtime.down` 时置 503；用例 `tests/test_fault_tolerance.py`（函数级 + HTTP 级两条） |
| 限流豁免表只核「有没有写」（原 T32） | 已修 | `Exemption(why, review_by)`：理由里反引号引用的 `.py` 与 `资源:动作` 权限码由守卫解析存在性（文件/权限点没了就红），`review_by` 过期或超过 180 天也红；用例 `test_exempt_reviews_are_current_and_honest` / `test_exempt_reasons_cite_real_code`；复核方法写进 `docs/OPS.md` §9（按 P95 与调用量找变贵的接口）|
| 队列租约只靠「注释说它够长」（原 T26 外的发现） | 已修 | 执行期间按 `lease_sec / 4` 续租（`workers.py` 的 `_renew_lease`）+ `queued_jobs.lease_token` 领取凭据（`ack`/`nack`/`renew` 都要对得上）；用例 `test_worker_keeps_the_lease_alive_while_running` / `test_stale_ack_cannot_delete_the_new_owner_row` / `test_worker_stops_the_handler_when_the_lease_is_lost` |
| 过载被报成 bug：池耗尽返回 500，且默认挂 30 秒（原 T25 外的发现） | 已修 | 池耗尽 → 503 + `details.reason=db_pool_exhausted` + `retryAfter`（`api/errors.py`）；`DB_POOL_TIMEOUT` / `DB_POOL_RECYCLE` / `DB_STATEMENT_TIMEOUT_MS` 三项配置，sqlite 文件库也真正用上了池（原来被「sqlite 不支持连接池参数」这句假注释跳过）；`app doctor` 加 `runtime.db_pool`。用例 `tests/test_db_pool.py`（13 条，含反向验证） |
| 死信「留档给人看」却没地方看（原 T26 外的发现） | 已修 | `app jobs [--state dead] [--json]` 看得到 `last_error` 与租约；`--redrive <id>` 只接受死信（在途重投会被拒绝），`--drop <id> --force` 拒绝删除 `running` 行；内存驱动直接说明「查不到」而不是打印空表。用例 `tests/test_cli.py` 后四条 |
| 写路由漏挂限流无人发现 | 已修 | `api/ratelimit_policy.py` + `tests/test_route_policy.py`；8 个花钱/写盘入口补了 `rate_limit`，其余 32 条豁免逐条写理由 |
| `app_meta` 只有「最后写入者胜」（原 T33） | 已修 | `AppMeta.version`（ORM `version_id_col` 乐观锁）+ `updated_by` + `app_meta_history`（同事务、每键留 50 条）；`set_json(expected_version=)` 做 CAS，`merge_json` 冲突后重读重算；`sync_new_permissions` 自动路径取 `declared ∪ previous`（只增不减），收缩走 `app sync-roles --prune-snapshot`，`app meta [key]` 看轨迹。用例 `test_write_against_stale_version_is_refused`（test_app_meta.py）、`test_old_instance_cannot_shrink_snapshot` / `test_conflict_makes_it_recompute_instead_of_overwrite`（test_role_auto_sync.py）+ 迁移用例 `test_upgrade_keeps_existing_rows` |
| 新权限点要人工 `sync-roles`，漏了就非超管 403 | 已修 | `IdentityService.sync_new_permissions` + `app_meta.permissions.catalog` 快照；用例 `tests/test_role_auto_sync.py` |
| 装配钩子塞在 `startup` 里，`WORKER_ENABLED=false` 的实例永不执行 | 已修 | `ModuleSpec.bootstrap`（人人执行）与 `startup`（仅消费实例）分开；identity 改用 `bootstrap` |
| worker 取任务异常导致协程静默死亡 | 已修 | `workers.py` 的 `_loop` 三层兜底 + 退避 + `queue_dequeue_errors_total`；用例 `test_worker_survives_dequeue_errors` / `test_worker_survives_ack_failure` |
| `resume()` 的 `APP_ENV` 早退让恢复链在测试里永不执行（原 T6） | 已修 | 删掉环境判断，改 `resume(*, repo=None)` 显式注入；用例 `test_resume_takes_repository_as_explicit_input`（并在 test 环境下断言 `app_env == "test"` 跑通） |
| worker 没有「消费协程还活着吗」的对外指标（原 T31） | 已修 | `workers.py` 的 `worker_consumers` 的 alive / desired 两个取值与 `worker_jobs_in_flight`：前者分辨「协程死了」，后者分辨「活着但卡住」；用例 `test_worker_exposes_consumer_metrics` / `test_worker_busy_counts_running_job`，OPS 加了第七条告警 |
| `ReplacementStore` 不写 `submit_mode` 快照（原 T4） | 已修 | `replacement_repository.ReplacementManifest.submit_mode` + `mark_submitted` 用 `MODEL_ID` 解析后落盘（解析不出留空=安全侧），`schedule_state` / `reconfirm_state` 在新一次提交前作废旧快照；用例 `test_submit_records_provider_shape_for_recovery` / `test_unresolvable_catalog_leaves_snapshot_empty` / `test_reschedule_drops_stale_snapshot` |
| `recover()` 对持续 Conflict 无上限（原 T3） | 已修 | 轮次随 payload 带过去，退避 30s→600s 共六轮，用完打 `replacement_recovery_lock_giveups_total` 并放手（拿不到锁就不写档案）；用例 `test_recover_waits_for_run_lock_with_bounded_retries` |
| 原 T15 五份文档未提交 | 已提交 | `e928fbb`（`CODE_STYLE` / `REVIEW_CHECKLIST` v2.0 + R11~R14 / FE6 报告） |
| 原 T16 两条旧分支 | 已删 | `merge/pr1`（`4e26035`）、`preview/full`（`59513ba`）`merge-base --is-ancestor` 均通过后 `git branch -d` |
| 框架层复审：承诺 vs 实现 | 已修 | ①outbox 调永不抛的 `publish`，订阅者失败被记成 `delivered`（`attempts`/`failed` 恒 0、退避是死代码）→ 新增 `EventBus.publish_strict`，测试改用**真实 bus**；②选主接管走「读过期→remove→create」，删除非原子 → 两实例同时选上；改为「只由被接管租约决定」的接管键，单赢家（对比实测 2→1）；③指标路由模板用子串替换，`/api/v1/tasks/1` 变成 `/api/v{task_id}/tasks/{task_id}` → 按 `/` 切段右对齐；④`current_user` 请求间不复位 → `reset_request_context()`；⑤`JWT_ALGORITHM=none` 可静默关掉签名校验 → 算法白名单 |
| 原 T16/T17 归档远端化 | 已修 | 协议收 key + `list_keys` 枚举；`infra/archive/runs.py` 把运行工作区分出归档；`app migrate-archive` 幂等搬家；`ARCHIVE_DRIVER=s3` 实测起/传/取全通 |
| T5 502 表达配置状态 | 已修 | `Unavailable`（50300 / 503）；`schedule()` 改抛它，文案点名 `WORKER_ENABLED` |
| T12 存储基类注释说错工厂 | 已修 | `infra/storage/base.py` 头注释改指 `infra/loader.py` 的 `load_driver()` |
| T13 运维脚本绕过 `ARCHIVE_DRIVER` | 已修 | 两个脚本改 `get_archive_storage()` + key 语义；`test_archive_driver.py` 按 AST 拦直接实例化 |
| T18 孤儿 blob 无 GC | 已修 | `app prune-blobs`：引用集合 = 库行 + 镜像 JSON，默认只报告，`--min-age-hours` 默认 24 |
| T19 视频流全量下载 | 已修 | `ArchiveStorage.download_url()`；远端驱动 307 预签名、本地仍 `FileResponse`，出参收敛到 `modules/asset/serving.py` |
| 素材库 A/B 档（数据正确性与长期可运行） | 已修 | 引用检查改多来源聚合（task + template）并接入回收站；`DELETE /assets/archive/{id}` 与 `prune-blobs --include-trash` 共用 `archive_purge.py` 彻底删除；`verify-blobs` 读回重算摘要；`app doctor` 12 项自检；GC 挂 `@periodic`（默认关闭）；状态变更/发布写审计；`ARCHIVE_QUOTA_BYTES_PER_USER` 配额 |
| T14 反模式表补两条 | 已修 | `REVIEW_CHECKLIST.md §6` 新增 19–22：fail-closed 例外、双写必须有反向重建（本轮），以及 R13-01 门控按语义集合对照、R14-01 守卫要验「通不通」（原验收口径），各带真实反例 |
| 素材库基建封顶（扩展性 / 健壮性） | 已修 | ① 引用来源由 `ModuleSpec.asset_reference_sources` 声明 + AST 守卫对账写入点 + 运行时 fail-closed（缺来源抛 503，不再静默放行删除）；② `modules/asset/mirror.py` 把 DB 定为唯一事实来源，写入顺序统一 commit→投影，`stateVersion` 幂等对账（`@periodic("asset.mirror")` + `app rebuild-mirrors`）自动补缺/更新/清孤儿，doctor 的反向建议已改正；③ `infra/blob.py` 让两条 S3 通路共用一份 boto3 client 与凭据，`S3_BUCKET_MODE=shared` 支持单桶前缀隔离；④ `core/coordination.py` 的 `LeaderElector`（local / 基于 `create()` CAS 租约的 backend）注入 Scheduler，GC 标 `leader_only`；⑤ 配额在 PG 下 `FOR UPDATE` 串行化。修复了 `S3_ARCHIVE_CACHE_DIR` 字段名与文档长期不符（实际读的是 `S3_CACHE_DIR`） |
| 横向能力第二批（队列 / 事件 / 限流） | 已修 | ① `QUEUE_DRIVER=db`：`db/jobs.py` + `infra/queue/db.py`，条件 UPDATE 领取 + 租约接管崩溃任务 + 部分唯一索引管幂等键 + 死信留档；② `db/outbox.py`：`record()` 与业务同事务落库，`@periodic("core.outbox")` 至少一次投递 + 退避 + 终态清理，`modules/task/worker.py` 的失败通知已改走它（原来 commit 后 publish 会静默丢事件）；③ `core/ratelimit.py` + `api.deps.rate_limit()`：基于 `Cache.incr` 的固定窗口，缓存故障降级放行，`POST /tasks` 已挂；④ Redis 未引入，但协议映射表、`*_DRIVER` 开关、`DriverSettings` 自动巡检三个入口都已备好；doctor 16→17 项（队列可达性） |

| 三条纵向主线（数据 / 运维 / 工程） | 已修 | ① 数据线：`tests/test_migrations.py` 在临时库上 `upgrade head` + `compare_metadata` 断言零漂移，CI 另跑真实 `alembic upgrade head && alembic check`；`test_routers_do_not_manage_transactions` 禁止路由层碰事务。② 运维线：`core/metrics.py`（自研 Prometheus 文本暴露）+ `api/instrumentation.py` + `/metrics`（`METRICS_TOKEN`，生产无 token 403）+ worker 指标；`queued_jobs.trace_id` / `outbox_events.trace_id` 让异步段日志接回原请求；`/health/ready` 改三档（ok/degraded/down）+ 逐项 checks；`app backup/restore`（sqlite backup API、迁移清单、恢复前留现场）；`core/flags.py` 功能开关；doctor 17→21 项。③ 工程线：脚手架补缓存/HTTP/队列/outbox/限流/开关/指标用法与写接口限流示例，`new-module` 提示补迁移与 `sync-roles` |

| 框架层覆盖式审查（调度 / 备份 / 归档 / 存储） | 已修 | ① `Scheduler` 的 handler 加了执行上限（默认 `max(60, every*2)`，与 `leader_only` 的租约 TTL 同一个数）：原来顺次 `await` 且无超时，一个**回不来**的 handler（卡住的 `pg_dump`、没有读超时的第三方调用）会让进程内全部周期任务永久停摆，且日志里一条错都没有；② `db/backup.py` 的 `pg_dump` / `pg_restore` 补 `BACKUP_COMMAND_TIMEOUT_SEC`（默认 3600）——取消协程不会杀子进程，卡死一次就永久占住一个线程；`backup_loop` 改捕 `Exception`（原来只捕 `BackupError`/`OSError`，库连不上抛的是 `OperationalError`，于是清理被一起跳过）；③ S3 归档物化缓存的临时文件名从 `{sha1}.{pid}.part` 改成带 `uuid4`：取件走 `to_thread`，同进程两个线程并发下载同一份字节会互相踩（Windows `PermissionError`、POSIX `FileNotFoundError`）；`_evict` 容忍删不掉的缓存文件（Windows 上正在被读的文件不许删）；④ `LocalArchiveStorage.create()` 改成「临时文件 + `os.link`」：`open("xb")` 独占但不原子，崩在中间会留下截断 JSON，而 `create` 之后永远返回 `False`，版号成了永久黑洞；⑤ `LocalStorage.delete()` 用 `missing_ok`，去掉「先 `exists` 再 `unlink`」的竞态（违反幂等承诺 → 500）；⑥ `media_probe.inspect()` 捕 `TimeoutExpired` → `BadRequest`，探测超时不再报成 500 |

`ARCHITECTURE.md` / `EXTENSIBILITY_GAPS.md` 里 A 组 13 条已修 10 条，剩余 3 条即上面的 T7/T8/T9。