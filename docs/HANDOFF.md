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
| HEAD | 与 `origin/main` 一致，本轮提交 hash 见 `git log -1`（未跟踪的只有 `var/`、快捷方式 `.lnk`、`vite.config.ts.timestamp-*.mjs`，都不该进提交） |
| CI | 此前连续多轮全绿（含 `bc0940e`）；本轮推送后照 §4 核对。PG 迁移仍在观察期（`continue-on-error`） |
| 本机门禁 | 后端 **1515 passed / 12 skipped**（约 220s）；`npm run verify` 全绿（**614 passed / 74 files**）；`app export-openapi --check` 四份无差异；`gen:routes` / `gen:mock-coverage` 两个 `--check` 同步 |
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
| `cfc0508` | `ModelSpec.internal` + `ModelRouter.public_models()`：替换链专用模型不再出现在目录（T33） | 视频下拉里不再有选中即 400 的 `*-replacement` |

表格之后又连续落了几件事（提交见 `git log --oneline -12`）：PR#5 语义合并 →
T34 参考图（含素材库侧边悬浮栏 + 拖拽）→ 标签两级分类 → worktree
`main-7b123d58` 线性合并（Agnes Video 2.5 Flash 驱动）→ **文本资产 B+C**。
最后这件的产品口径是用户拍板的：形态 **B+C**（既是可编辑的站内文本资产，
也要能「挑去用」进提示词）、只收 `.txt`/`.md` 且 ≤1MB、按推荐定为
「UTF-8 纯文本、Markdown 不转 HTML」。实现要点全在 §5 的三条不变量里。

两个值得记住的结论：

- **CI 从 `48ac435` 起每次都是 failure，只有 mypy 一步红**，而且红了很久没人看——
  类型门禁实际上等于没有。push 完要主动看一眼这一步。
- 全量测试一半时间在算口令哈希：`admin_headers()` / `make_user()` 走真实登录，
  每拿一次 token 就是一次 scrypt。这不是「某个用例慢」，所以只能改成本参数，不能改用例。

## 2. 行为变化（会打到前端与运维）

| 变化 | 谁受影响 | 说明 |
|---|---|---|
| 未在 `param_schema` 声明的参数 → **400** + `availableParams` | 前端/脚本 | 前端参数区本来就由 `GET /models` 渲染，无需改动；手写参数的地方会立刻看到 400 |
| 回收站里的条目改名 / 换标签 / 改成员 → **40088** | 前端 | 要先恢复再改（「删除」的语义是冻结） |
| `TRUSTED_PROXIES=0.0.0.0/0` → **启动失败** | 运维 | 等于信任任意伪造 `X-Forwarded-For` |
| agnes 提交预算 **120s → 210s**（驱动声明 180 + 余量 30） | 运维/上游 | 以前上游慢于 120s 会被框架取消并判「提交结果未知」 |
| `APP_ENV=test` 时 scrypt 用 `2**10`，其他环境仍 `2**14` | 测试 | 参数编码在哈希串里，存量口令不用迁移 |
| 指标超限丢弃可在 `/metrics` 看到 `app_metrics_series_dropped_total{metric}` | 运维 | 以前是静默丢 |
| `bailian` 不再是 `AI_PROVIDERS` 里的名字：`.env` 若还写着它，**启动会失败**（`infra/ai/bailian.py` 已删） | 运维 | 删掉那一段即可，驱动会自动载入；只需留 `BAILIAN_API_KEY` 等私有配置 |
| 百炼渠道可用性改由驱动回答（`InputPreparer.unavailable_reason`） | 前端/运维 | 没配密钥时渠道目录返回「服务端尚未配置百炼密钥」，文案与以前一致 |
| `GET /models` 不再返回 `internal=True` 的模型（`bailian-wan3-*` / `libtv-wan3-*`） | 前端/脚本 | 它们是替换链专用（要 `archiveRunId`）。按 id 仍能提交；只是不再出现在「可选清单」和「可用模型」报错提示里 |
| 资产库多了 `kind=text`：`POST /library/text-assets` + `/assets/{id}/text{,/versions,/restore,/raw}` | 前端/脚本 | 只收 `.txt`/`.md`、UTF-8、≤1MB；`coverUrl` 恒为 null，卡片改画 `textExcerpt`。老资产读到 `textExcerpt/textVersion/textUrl` 分别是 null / 0 / null |
| 生图 `GenerateImageIn`、视频 `CreateTaskIn`、替换 `PrepareReplacement` 各多 `textLibraryIds` + 出参 `textBindings` | 前端/脚本 | 提示词里写 `{{Text N}}`，提交时冻结正文注入；出参只回「哪几份、第几版」不回正文。替换工作台只认 `{{Text N}}`，别的手写花括号会被前端拦 |
| 上传接口 `kind=text` | 前端/脚本 | 请求体上限从 300MB 降到 **1MB**（`LibraryTextTooLarge` 413）；非 UTF-8 一律 400，不做替换字符兜底 |
| `library_assets` + 三张生成表各加列、新增 `library_text_versions`（迁移 `e2b7c4a9f310`） | 运维 | 存量行靠 `server_default` 天然是「没有文本引用」，**无需回填**；purge 由 `lifecycle._purge_row` 显式删版本行 |

## 3. 没做完的（直接从这里开工）

完整清单在 [`TODO.md`](TODO.md)，这里只标需要拍板 / 刚变动的：

1. **T30 · PG 迁移闸收紧**：`postgres-migrations` job 已连续 3 次（#62/#63/#64）通过，
   TODO 里的收紧条件已满足，但 **2026-11-11 拍板「先不动」**——保持 `continue-on-error`，
   这道闸目前只负责暴露方言差异、不阻塞合并。哪天要收紧，照 TODO 的前置检查做。
2. **被移出小组后 owner 看不到素材**（产品口径待定，暂按现状）。
3. 其余存量：T2/T8/T9/T10/T22~T26/T29，均带验收口径。

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

## 4. 新会话怎么接手

```bash
# 起服务（改完代码必须带 --reload，否则 /docs 看不到新接口）
cd api && ./.venv/Scripts/app.exe serve --reload

# 后端门禁：basetemp 每轮必须换一个全新目录（复用到旧目录会出成片假错误）
cd api && ./.venv/Scripts/python.exe -m pytest -q --basetemp=var/pytest-$(date +%s)
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy --platform win32 src     # 与 CI 同口径

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
| `CODE_REVIEW_*.md` / `*_PLAN.md` / `*_REQUIREMENTS.md` / `*_DESIGN.md` | **历史记录**，只有决策价值，不要当现状读 |

## 8. 已知偶发（不是回归，别急着改代码）

| 现象 | 判据 |
|---|---|
| `test_archive_blobs` / `test_archive_index` 报 `OSError: SHFileOperationW 失败: 0x78` | safe-delete shim 回收站失败，**单跑必过**；同一轮里其余用例照常 |
| `test_scheduler::test_leader_only_lease_call_does_not_block_the_event_loop` 偶红（`worst` 80ms vs 50ms 阈值） | 负载敏感的计时断言，单跑 11 passed；机器空闲时复跑即绿 |
| 全量 3~6 分钟而不是 200s | 机器上有别的负载（浏览器/开发服务器）。看 `--durations` 有没有异常项再判断 |

判断顺序：**单跑复验 → 换全新 `--basetemp` 复跑 → 才怀疑代码**。反过来做会浪费一整轮。
