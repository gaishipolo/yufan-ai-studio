# 全局待办

> 只放「已知还没做」的事。修完就删掉该行，别改成 ✅——这份文档的价值在于它短。
> 每条都带位置和验收口径，避免下一轮又靠猜。
> **前后端均由 AI 负责（2026-11-07 起）**：「落点」只标改动位置，不是人员分工；
> 「产品决定」表示需用户拍板。
>
> 来源：`CODE_REVIEW_2026-11-06_R11~R14`、`CODE_REVIEW_2026-11-06_FE6`、
> `EXTENSIBILITY_GAPS.md` 的 B1/C1/C2，以及素材分支合并时新发现的项。
> 最后更新：2026-11-07 22:10（扩展性专项：写路由限流策略守卫 + 权限点自动并入内置角色；
> 顺带把模块启动钩子拆成 bootstrap（装配，人人执行）/ startup（恢复，仅消费实例））
> 剩余登记 T31~T33）

## 一眼看完

| 编号 | 事项 | 影响 | 落点 |
|---|---|---|---|
| T2 | 前端不展示 `externalId` / `previousExternalId` | 文案让用户「凭任务号核对」，页面拿不到 | 前端 |
| T3 | `recover()` 遇持续 Conflict 无限重投，无上限 | 僵死租约会每 30 秒吵一次，永不停止 | 后端 |
| T4 | `ReplacementStore` 不写 `submit_mode` 快照 | 删掉模块自有 startup 后会误分类供应商形态 | 后端 |
| T6 | `resume()` 的 `app_env == "test"` 早退 | 恢复链在测试里永不执行，结构性问题藏得住 | 后端 |
| T7 | 权限点不自动进内置角色（B1） | 新权限上线后要人记得跑命令 | 后端 |
| T8 | 轮询节奏全局化（C1） | 视频与生图共用一个间隔 | 后端 |
| T9 | 重试是作业级不是任务级（C2） | 任务重试语义与用户预期不一致 | 后端 |
| T10 | `archivePath` 绝对路径进 HTTP 契约 | 服务端盘符泄漏给浏览器 | 产品决定 |
| T22 | 归档检索与批量写太弱（C 档） | 几十万级素材找不到、整理要靠逐条点 | 产品决定 |
| T23 | 上传秒传/断点、视频派生预热（C 档） | 300MB 白传一遍；首个点开视频的人等转码 | 后端 |
| T24 | `MediaFile.path` 装的是 key，字段名/描述没跟上 | 契约语义漂移，后来者会当路径拼 | 后端（小） |
| T25 | 多实例部署还差三处硬边界 | 配额首次并发、预签名 TOCTOU、运行工作区仍要共享盘 | 架构 |
| T26 | db 队列是轮询式，没有唤醒机制 | 空转查询按 worker 数叠加；万级 QPS 要换 Redis 驱动（协议已备好） | 架构 |
| T27 | 限流额度写死在路由上（只有一个 `RATE_LIMIT_DEFAULT_PER_MIN`），改额度要发版；固定窗口有 2× 边界误差 | 运营想临时放宽/收紧某个入口只能改代码；窗口交界瞬时可达 2 倍额度 | 后端 |
| T29 | 指标是单进程语义，未做多进程聚合 | 多 worker 部署要靠 Prometheus `sum by`；本机 `curl /metrics` 只看到一部分 | 架构 |
| T30 | PG 迁移闸在观察期（`continue-on-error`），尚未成为硬性要求 | PG 特有的默认值/条件索引差异要等它稳定后才真正拦住 | 架构 |
| T31 | worker 没有「当前有多少消费协程在跑」的对外指标 | 协程全停时探针仍绿，只能靠 `jobs_processed_total` 不涨反推 | 架构 |
| T32 | 限流豁免表只在 CI 里核对「有没有写」，不核「写得对不对」 | 某个接口变贵了（加了供应商调用）没人回头改额度/删豁免，表会慢慢失真 | 架构 |
| T33 | `app_meta` 只有「最后写入者胜」，没有版本/审计 | 两个实例同时启动时，快照可能被旧值覆盖一轮；排查「谁改了这份状态」只能翻日志 | 架构 |

---

## T2 · 前端不展示供应商任务号（R13-08）

409（`confirmCharged`）和恢复失败文案都写着「请凭供应商任务号核对结果」，
`schema.ts` 也同步了 `externalId` / `previousExternalId`，但 `studio/src`
除生成物外零引用——用户被要求拿一个页面看不到的东西。

**验收**：生图详情/列表能看到并复制 `externalId`；「已确认扣费重试」之后能同时看到
历史任务号（`previousExternalIds`）。

## T3 · `recover()` 对持续 Conflict 没有重投上限

`modules/template/replacement_worker.py` 的 `recover()`：`except Conflict` 分支
直接 `enqueue_recovery(run_id, delay=30)` 然后返回，**不计次数**。`_bump_retry` 只管
prepare / download 两条线。租约被僵死进程占住时（`clear_finished_lock` 要确认退出才清），
这里会每 30 秒投一次，永不停止。

**验收**：run.json 增恢复重投计数；超过阈值置「待人工核对」并写审计，不再自动入队。

## T4 · `ReplacementStore` 不写 `submit_mode` 快照（R14 观察-5）

R12-05 加 `submit_mode` 的理由是「恢复要解释记录自身的形态，而不是拿当前模型目录重新解析」。
模板的记录一律 `submit_mode=None`，现在靠「不经过框架恢复器」侥幸无事；一旦按框架方向
删掉模块自有 `startup`，`_supports_poll` 就会退回目录解析，而 `LibTVProvider.supports_poll = True`，
echo-only 环境里仍会被误分类。

**验收**：`replacement_pipeline.ReplacementStore` 提交时写 `submit_mode`；有轮询供应商的用例。
## T6 · `resume()` 的 `app_env == "test"` 早退（R14 观察-4）

`replacement_worker.resume()` 开头 `if settings.app_env == "test" ... return`。
R14 的三条问题全部落在被这个早退遮住的函数里——测试套件结构上不可能发现它们。
本轮新增的收敛用例靠 monkeypatch 把 `app_env` 改成 `prod` 绕过，**但函数本身仍早退**，
下一个改这个函数的人大概率还是会踩。

**验收**：删掉环境判断，改用显式注入（如 `resume(force=False)`）或夹具控制共享盘；
用例不再需要 monkeypatch 环境变量。

## T7 · 权限点不自动进内置角色（EXTENSIBILITY_GAPS B1）

已有 `app sync-roles`，但要人记得跑。新增权限点后，`editor` / `viewer` 这些内置角色
不会自动带上，表现为「功能上线了但没人有权限」。

**验收**：`sync-roles` 进启动流程（幂等）或进迁移；或新增权限点时 CI 报错提示。

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

## T26 · db 队列没有唤醒机制（轮询式，吞吐有上限）

`QUEUE_DRIVER=db` 解决了「重启丢任务」和「崩溃任务卡死」，但取任务是**轮询**：
每个 worker 每 `QUEUE_POLL_INTERVAL_SEC`（默认 0.2s）查一次库。10 个 worker 就是
50 次/秒的空查询，小库能扛，万级 QPS 不行；而且入队到被取走有 0–200ms 的固有延迟。

协议层已经留好入口（`infra/queue/base.py` 的映射表 + `docs/EXTENDING.md` §5.2.4），
换 Redis 只需新增 `infra/queue/redis.py`，业务不改。不引 Redis 时的另一条路是
PostgreSQL 的 `LISTEN/NOTIFY`：写完 `NOTIFY`，worker 侧长连接被唤醒——但 sqlite 没有，
会变成「两套取任务路径」，所以没做。

**验收**：`QUEUE_DRIVER=redis` 可切换，且 `tests/test_db_queue.py` 的语义用例在
新驱动上同样通过（协议一致性是硬要求）；或 db 驱动接上 `LISTEN/NOTIFY` 并有
「空转查询数下降」的实测数据。

## T27 · 限流额度的两处已知边界

> 2026-11-07：「写路由未挂限流」的守卫**已做**——`tests/test_route_policy.py`
> 枚举运行期全部写路由，要求挂 `rate_limit` 或在 `api/ratelimit_policy.py` 里
> 写清豁免理由；豁免表里的失效条目同样会红。剩下的是额度本身的边界。

1. **额度不可按接口配置**：只有 `RATE_LIMIT_DEFAULT_PER_MIN` 一个旋钮，
   各路由写的是同一个值（上传额外用 `cost=3`）。要临时放宽某个入口得改代码发版。
   想做成 `RATE_LIMIT_TASK_CREATE_PER_MIN` 那样一入口一配置也可以，但配置面会
   迅速膨胀，且「新入口忘了加配置」又会变成新的静默缺口——目前选择接受。
2. **固定窗口有 2× 边界误差**：窗口最后 1 秒打满、下一秒又是新窗口，
   瞬时可达 `2×limit`。要精确就换滑动窗口（`ZADD` + `ZREMRANGEBYSCORE` + `ZCARD`，
   或用 Lua 保证原子），同样只需替换 `Counter` 实现。

另：计数在 `CACHE_DRIVER` 里，`memory` 时多实例各算各的——这是部署配置问题，
`app doctor` 的 `runtime.cache` 会报驱动名，但不会替你判断额度是否被放大。

**验收**：给某个入口单独的额度配置（例如高成本生图与轻量 PATCH 分开），
并保证新入口漏配时**回落到默认值且有告警**，而不是变成无限额。


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
**验收**：文档写清（已写进 `docs/OPS.md` §8），或提供一个 `--json` 的聚合视图给脚本用。

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


---

## 已复核关闭（别再重做）

2026-11-06 合并后逐条核过，以下都已修好，留在这里防止重复劳动：

| 编号 | 结论 | 证据 |
|---|---|---|
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
| 写路由漏挂限流无人发现 | 已修 | `api/ratelimit_policy.py` + `tests/test_route_policy.py`；8 个花钱/写盘入口补了 `rate_limit`，其余 32 条豁免逐条写理由 |
| 新权限点要人工 `sync-roles`，漏了就非超管 403 | 已修 | `IdentityService.sync_new_permissions` + `app_meta.permissions.catalog` 快照；用例 `tests/test_role_auto_sync.py` |
| 装配钩子塞在 `startup` 里，`WORKER_ENABLED=false` 的实例永不执行 | 已修 | `ModuleSpec.bootstrap`（人人执行）与 `startup`（仅消费实例）分开；identity 改用 `bootstrap` |
| worker 取任务异常导致协程静默死亡 | 已修 | `workers.py` 的 `_loop` 三层兜底 + 退避 + `queue_dequeue_errors_total`；用例 `test_worker_survives_dequeue_errors` / `test_worker_survives_ack_failure` |
| 原 T15 五份文档未提交 | 已提交 | `e928fbb`（`CODE_STYLE` / `REVIEW_CHECKLIST` v2.0 + R11~R14 / FE6 报告） |
| 原 T16 两条旧分支 | 已删 | `merge/pr1`（`4e26035`）、`preview/full`（`59513ba`）`merge-base --is-ancestor` 均通过后 `git branch -d` |
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

`ARCHITECTURE.md` / `EXTENSIBILITY_GAPS.md` 里 A 组 13 条已修 10 条，剩余 3 条即上面的 T7/T8/T9。