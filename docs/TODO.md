# 全局待办

> 只放「已知还没做」的事。修完就删掉该行，别改成 ✅——这份文档的价值在于它短。
> 每条都带位置和验收口径，避免下一轮又靠猜。
>
> 来源：`CODE_REVIEW_2026-11-06_R11~R14`、`CODE_REVIEW_2026-11-06_FE6`、
> `EXTENSIBILITY_GAPS.md` 的 B1/C1/C2，以及素材分支合并时新发现的项。
> 最后更新：2026-11-07 03:20（归档取件改预签名 307、孤儿 blob 回收、配置类 503 之后）

## 一眼看完

| 编号 | 事项 | 影响 | 谁来做 |
|---|---|---|---|
| T1 | `AssetReferenceChecker` 注入口无人实现，删素材一律放行 | 会删掉别人还在用的素材 | 后端（等引用方落地） |
| T2 | 前端不展示 `externalId` / `previousExternalId` | 文案让用户「凭任务号核对」，页面拿不到 | 前端 |
| T3 | `recover()` 遇持续 Conflict 无限重投，无上限 | 僵死租约会每 30 秒吵一次，永不停止 | 后端 |
| T4 | `ReplacementStore` 不写 `submit_mode` 快照 | 删掉模块自有 startup 后会误分类供应商形态 | 后端 |
| T6 | `resume()` 的 `app_env == "test"` 早退 | 恢复链在测试里永不执行，结构性问题藏得住 | 后端 |
| T7 | 权限点不自动进内置角色（B1） | 新权限上线后要人记得跑命令 | 后端 |
| T8 | 轮询节奏全局化（C1） | 视频与生图共用一个间隔 | 后端 |
| T9 | 重试是作业级不是任务级（C2） | 任务重试语义与用户预期不一致 | 后端 |
| T10 | `archivePath` 绝对路径进 HTTP 契约 | 服务端盘符泄漏给浏览器 | 产品决定 |
| T11 | `ArchiveList.root` 合并后已无前端消费 | 契约里多一个没人用的绝对路径 | 后端（可删） |
| T14 | `REVIEW_CHECKLIST` 缺两条反模式 | 同类问题第五次出现 | 文档 |
| T15 | 生成结果 URL 落库（`image_jobs.output_url`） | 换存储驱动后历史链接全部失联 | 后端 |
| T20 | 远端驱动下发布锁退化为本机锁 | 多实例并发发布可能发出两个版本 | 后端（条件写/行锁） |
| T21 | 运行工作区本机化，历史任务不跨机器 | 换台机器打开旧任务显示「档案不存在」 | 产品决定 |

---

## T1 · `AssetReferenceChecker` 未实现，删除素材一律放行

`api/src/core/ports/assets.py` 定义了协议，`api/src/api/ports.py` 已登记进
`PORT_CATALOG`，**但没有任何模块实现它**，所以删除素材时不查引用、直接放行。

这是有意的取舍：接上之前一律拒绝删，会让删除功能完全不可用，而用户只看到
「删不掉」。代价是现在「模板 / 分镜 / 镜头还在引用某个素材」时也能删掉，
删完那些页面会出现空白图。

**验收**：`has_asset_reference_checker()` 为真；删除被引用素材返回 409 并列出引用方
（模块 + 资源 + id）；启动日志里 `asset-references` 从「注入口未注册」变成已注册。

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

## T11 · `ArchiveList.root` 合并后已无前端消费（新发现）

`modules/asset/archive_schema.py:16` 仍然返回 `root: str`，但消费它的
`features/archive-library/ArchiveLibrary.tsx` 已被素材库（`features/asset-library/`）取代，
全仓 `studio/src` 已无 `.root` 引用（`grep -rn "\.root\b" studio/src` 为空）。

**验收**：删字段并重跑 `app export-openapi`（四份生成物）；若决定留给运维诊断页，
在字段描述里写明用途，别再让下一个人重新查一遍「这字段还有没有人用」。
## T14 · `REVIEW_CHECKLIST` 缺两条反模式

R13 与 R14 各自归纳出一个新形状，都没进清单：

1. **门控/排除条件要按语义集合对照**，而不是只覆盖触发这次修复的那一种实现
   （R13-01：`worker_enabled` 只包住框架恢复器，自带 `startup` 全在门控外）。
2. **守卫要验证「通不通」，不是「有没有」**（R14-01：`test_every_kind_has_a_user_retry_exit`
   被 `"retry_count" in text` 这个关键字骗过，而那个字段计的是自动重试）。

**验收**：`CODE_STYLE.md §6` 或 `REVIEW_CHECKLIST.md` 的反模式表加两条，各带一个反例。

## T15 · 生成结果 URL 落库，换存储驱动即失联（切 S3 时发现）

`image_jobs.output_url` / `cover_url` 存的是**生成那一刻** `url_for()` 的产物：本地
驱动下是 `/static/images/<id>.png`。而 `/static` 只在 `STORAGE_DRIVER=local` 时挂载
（`app._mount_static`），切到 s3 之后这批历史地址没有任何端点应答 —— 生图页整屏
破图，且**不改代码就修不回来**。

现在已经有的临时兜底：`app migrate-storage` 会把这两列的 `/static/<key>` 改写成当前
驱动的 `url_for(key)`。但这是「每次换驱动都要记得跑一次」的人肉约定，不是结构保证：
- 桶如果是私有的，`url_for` 拼出来的直连地址照样 403 —— 本轮验证是把桶设成公开读
  才通的。
- 换到第三个驱动（OSS/COS）时，存量地址又会失联一次。

**验收**：读路径不再依赖落库的绝对 URL。方向二选一：
(a) `output_url` 只存**对象 key**，出参统一拼「本服务的下载端点」，由端点按驱动
    决定 `FileResponse` 还是 307 预签名（与 `AssetService.resolve_file` 同一套）；
(b) 保留落库 URL，但新增 `app rewrite-storage-urls`（本次的迁移逻辑抽出来）并在
    启动日志里探测「库里有一批地址不属于当前驱动」时告警。
(a) 是正解，(b) 只是把坑做成可见的。

## T20 · 远端驱动下发布锁退化为本机锁

`infra/media_catalog.py:catalog_lease` 优先用归档存储里的文件锁（`catalog/locks/
access.lease`），远端驱动没有本地路径，退化为 `runs_root()/.locks/access.lease`。
单机部署没问题，**多实例并发发布同一个包时不再互斥**，可能发出两个版本号。

**验收**：用 S3 条件写（`If-None-Match` 抢 `<version>.json`）或 DB 行锁（`SELECT …
FOR UPDATE`）替代；或显式在启动日志里声明「本部署只允许单实例发布」。

---

## T21 · 运行工作区本机化后，历史任务不跨机器

`REPLACEMENT_RUNS_ROOT` 留空时：本地驱动 = `<归档根>/runs`（沿用旧布局），远端驱动 =
`api/var/runs`。也就是说切到 S3 之后，**索引指针还在桶里（列表能看到），但
`run.json`/成片在本机**——换台机器打开就是「调用档案不存在」。

这是有意的取舍：桥接层与 ffmpeg 要真实路径，把 run 目录塞进桶会把执行器拆成两半。
代价是「API 多实例 + 每台各自跑任务」时历史只有本机可见。

**验收**：明确部署形态。要跨机器就把 `REPLACEMENT_RUNS_ROOT` 指到共享目录（仍是本地
路径语义），或者把成片产物在完成时转存对象存储、`runs/` 只保留 7 天过程文件。

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
| 原 T15 五份文档未提交 | 已提交 | `e928fbb`（`CODE_STYLE` / `REVIEW_CHECKLIST` v2.0 + R11~R14 / FE6 报告） |
| 原 T16 两条旧分支 | 已删 | `merge/pr1`（`4e26035`）、`preview/full`（`59513ba`）`merge-base --is-ancestor` 均通过后 `git branch -d` |
| 原 T16/T17 归档远端化 | 已修 | 协议收 key + `list_keys` 枚举；`infra/archive/runs.py` 把运行工作区分出归档；`app migrate-archive` 幂等搬家；`ARCHIVE_DRIVER=s3` 实测起/传/取全通 |
| T5 502 表达配置状态 | 已修 | `Unavailable`（50300 / 503）；`schedule()` 改抛它，文案点名 `WORKER_ENABLED` |
| T12 存储基类注释说错工厂 | 已修 | `infra/storage/base.py` 头注释改指 `infra/loader.py` 的 `load_driver()` |
| T13 运维脚本绕过 `ARCHIVE_DRIVER` | 已修 | 两个脚本改 `get_archive_storage()` + key 语义；`test_archive_driver.py` 按 AST 拦直接实例化 |
| T18 孤儿 blob 无 GC | 已修 | `app prune-blobs`：引用集合 = 库行 + 镜像 JSON，默认只报告，`--min-age-hours` 默认 24 |
| T19 视频流全量下载 | 已修 | `ArchiveStorage.download_url()`；远端驱动 307 预签名、本地仍 `FileResponse`，出参收敛到 `modules/asset/serving.py` |

`ARCHITECTURE.md` / `EXTENSIBILITY_GAPS.md` 里 A 组 13 条已修 10 条，剩余 3 条即上面的 T7/T8/T9。