# 全局待办

> 只放「已知还没做」的事。修完就删掉该行，别改成 ✅——这份文档的价值在于它短。
> 每条都带位置和验收口径，避免下一轮又靠猜。
> **前后端均由 AI 负责（2026-11-07 起）**：「落点」只标改动位置，不是人员分工；
> 「产品决定」表示需用户拍板。
>
> 来源：`CODE_REVIEW_2026-11-06_R11~R14`、`CODE_REVIEW_2026-11-06_FE6`、
> `EXTENSIBILITY_GAPS.md` 的 B1/C1/C2，以及素材分支合并时新发现的项。
> 最后更新：2026-11-07 15:30（分工约定废止，「谁来做」改为「落点」；上一轮素材库
> A/B 档收口：引用检查、彻底删除与 GC、完整性校验、doctor、审计、配额）

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
| T14 | `REVIEW_CHECKLIST` 缺两条反模式 | 同类问题第五次出现 | 文档 |
| T22 | 归档检索与批量写太弱（C 档） | 几十万级素材找不到、整理要靠逐条点 | 产品决定 |
| T23 | 上传秒传/断点、视频派生预热（C 档） | 300MB 白传一遍；首个点开视频的人等转码 | 后端 |
| T24 | `MediaFile.path` 装的是 key，字段名/描述没跟上 | 契约语义漂移，后来者会当路径拼 | 后端（小） |

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

## T14 · `REVIEW_CHECKLIST` 缺两条反模式

R13 与 R14 各自归纳出一个新形状，都没进清单：

1. **门控/排除条件要按语义集合对照**，而不是只覆盖触发这次修复的那一种实现
   （R13-01：`worker_enabled` 只包住框架恢复器，自带 `startup` 全在门控外）。
2. **守卫要验证「通不通」，不是「有没有」**（R14-01：`test_every_kind_has_a_user_retry_exit`
   被 `"retry_count" in text` 这个关键字骗过，而那个字段计的是自动重试）。

**验收**：`CODE_STYLE.md §6` 或 `REVIEW_CHECKLIST.md` 的反模式表加两条，各带一个反例。

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
| 素材库 A/B 档（数据正确性与长期可运行） | 已修 | 引用检查改多来源聚合（task + template）并接入回收站；`DELETE /assets/archive/{id}` 与 `prune-blobs --include-trash` 共用 `archive_purge.py` 彻底删除；`verify-blobs` 读回重算摘要；`app doctor` 12 项自检；GC 挂 `@periodic`（默认关闭）；状态变更/发布写审计；`ARCHIVE_QUOTA_BYTES_PER_USER` 配额 |

`ARCHITECTURE.md` / `EXTENSIBILITY_GAPS.md` 里 A 组 13 条已修 10 条，剩余 3 条即上面的 T7/T8/T9。