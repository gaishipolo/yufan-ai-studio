# 人物 / 产品 / 场景替换与素材归档

模板库的默认入口为三种替换模板。流程：上传或登记素材 → 选原片 → 选已封装素材包或参考图 → 设置替换范围 → 预览提示词并保存输入 → 确认进入零积分队列。

人物支持仅替换身份 / 同时替换参考服装；产品支持上身、手持、盒内与屏幕商品范围；场景替换保留原片人物、商品与前景交互。多选原片时每条原片独立归档。

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

`LIBTV_LEGACY_ROOT=E:/yufan-ai` 指向现有固定 v1.3.0 适配器。执行前调用旧项目固定入口 `node scripts/libtv_video.mjs check` 校验发布哈希，再通过未修改的上传、提交和查询组件执行。旧项目本身的媒体目录不再作为新任务输入/输出位置。

`LIBTV_NODE` 可设置 Node 22+ 的绝对路径。本机继续复用旧项目专用浏览器中的正常 LibTV 登录；登录过期时在原专用浏览器正常登录，不导出 Cookie。

- 提交使用原有零积分门禁：必须取得有效的数字估价，且严格等于 0。
- 非零报价进入 `waiting_free`，由用户稍后点击继续；不自动切换积分或付费加速。
- `submitting` 先持久化再调用上游；没有 task_id 且回执不明时标记 `submission_unknown`，禁止自动重提。
- 已有 task_id 只查询；后台每 20 秒延迟查询，重启从 Z 盘索引恢复。
- 新工作台通过独立监督进程串行访问账号，并与旧执行器使用同一 OS 字节锁。旧执行器运行时显示 `waiting_executor`，仅对非计费准备阶段延迟重排；不会中止旧任务。
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

素材页面支持预览、下载、分类上传与 inbox 文件登记；生成结果区直接读取每个调用档案中的原始成片。当前不提供素材删除和共享编辑，保留输入快照及审计记录。

## 代码落点与验证

后端业务在 `modules/template/replacement_*`，素材 HTTP 在 `modules/asset/archive.py`，素材权限与编排在 `archive_service.py`、索引在 `archive_repository.py`；通用文件协议在 `infra/archive/base.py`（工厂 `infra/archive/`，驱动 `infra/archive/local.py`）、媒体探测在 `infra/media_probe.py`，无跨模块业务 import。生成适配器在 `infra/ai/libtv.py`，通过 `replacement_pipeline.py` 注册到原框架，复用提交意图、轮询、超时与 `generation.*` 事件。`AI_PROVIDERS=echo,libtv` 启用该模型；模板专用模型从替换模板入口使用。

前端在 `features/replacement-templates`，API 类型使用自动生成的 `shared/api/schema.ts`。素材页（`features/asset-library`）只读 `/assets` 的 DB 线；归档接口 `/assets/archive/*` 现在**只服务于替换模板**，不再有独立的归档库界面。

检查：后端 `pytest -q`、`ruff check src tests scripts`、`mypy src`；前端 `npm run verify`。接口改动后运行 `app export-openapi`，一并提交三份生成物。

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
