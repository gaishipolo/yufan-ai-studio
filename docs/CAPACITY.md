# 容量评估：能扛多少人、同时几条生成

> **2026-11-22 首次整理**。数字全部来自当前配置与代码路径，**没有做过压测** ——
> 凡是推算出来的都标了「估」，凡是有配置项的都给到 `文件:行`。
> 配置一改这份就该跟着改。

## 一句话

能同时**打开页面用**的人很多（局域网 50–100 在线不是问题）；能同时**跑生成任务**的
只有 2 条。天花板不在服务器性能，在 `WORKER_CONCURRENCY` 和 LibTV 账号数。

## 1. 三个硬上限

| 层 | 上限 | 依据 |
|---|---|---|
| 页面 / 接口 | 单进程 uvicorn + 数据库连接 30 | `api/src/core/config.py:119-120`（`DB_POOL_SIZE=10` + 溢出 20，池等待 5s） |
| 后台任务 | **2 个槽位**，所有 job 共用 | `config.py:256`（`WORKER_CONCURRENCY=2`）；`api/src/workers.py` 的 `Worker._loop` 起 N 个协程 |
| LibTV 上游 | **每个账号 1 条**，两账号可并行 | `api/src/modules/model_driver/libtv/process.py:59`（`ACCOUNT_STAGES={upload,submit}`）+ `account_lock_name()` 按 `host-port` 分锁 |

2 个槽位是**所有** job 的总量：`generation.submit` / `generation.poll` /
`libtv.download` / `export.build` / `library.download.build` 全排这两条队。
百炼（`bailian-wan3-*`）没有账号锁，只受槽位限制；`libtv-wan3-replacement` 两层都受。

## 2. 具体能扛多少

### 2.1 只用页面（挑模板、填素材、逛资产库）

- 局域网 **50–100 人同时在线**不会有明显问题（估）。每人每秒不到 1 个请求，
  单进程异步 + 30 个连接够用。
- 唯一体感瓶颈：静态资源由 uvicorn 发且**没有 gzip**（`WEB_DIST_DIR` 那套），
  一个页面首次加载约 500KB。100 人同时打开会明显变慢，但不会挂。
- 大文件上传/预览走 API + 预签名，会占连接与线程池，不占 worker 槽位。

### 2.2 同时点「生成」

| 同时提交 | 表现 |
|---|---|
| 1–2 人 | 顺畅，两条槽位正好够 |
| 3–4 人 | 开始排队，后提交的停在「已提交 / 排队中」 |
| 5 人以上 | 排队时间可能撞上退避上限（见下），超了才判「提交生成失败」 |

账号被占**不会直接失败**：`EXECUTOR_BUSY` 在 `api/src/modules/model_driver/drivers/libtv.py`
里被翻译成 `RateLimited`，框架按 `GENERATION_THROTTLE_DELAYS=10,20,30,60,90,120,180,240`
（`config.py:442`）重新排队，任务保持 queued。**8 次退避累计约 12.5 分钟**，
超过才报失败 —— 这就是「最多排多久」的实际数值。

### 2.3 吞吐（估）

一条替换任务占槽位的大头是上传（超时 1500s）+ 提交（100s）+ 下载（240s），
实际经验 5–8 分钟/条。2 个槽位 → **10–25 条/小时**。
这是估算，不是压测值；想知道真实数字看 §4。

## 3. 风险清单

| 风险 | 现状 | 处置 |
|---|---|---|
| 一个人连点几十条把队列顶满 | **已处理**（2026-11-22）：`TASK_MAX_IN_FLIGHT_PER_USER=5`，判据 queued + running | `service._assert_in_flight_room()`，create 与 retry 两处都拦，超限 429 并说清上限与在途数 |
| 生图同样能刷队列 | **未处理**：`/images` 只有 60 次/分钟的频率限制，没有在途上限 | 生图是秒级任务、金额小，暂不加；要加得先给 task/image 一个共享计数口（两个模块不能互相 import） |
| 全局限流关闭 | `RATE_LIMIT_GLOBAL_PER_MIN=0`（按 IP，`middleware.py`） | 内网默认不开；对外暴露前必须开，否则脚本刷接口没有兜底 |
| 一个卡住的上传吃掉一半能力 | 上传超时 1500s（25 分钟），且它占的是**账号锁** | 出现时按 `run/records/executor-*.json` 定位；降 `STAGES["upload"]` 要权衡大素材 |
| 单实例 | `QUEUE_DRIVER=db` + `LEADER_ELECTOR=backend` | 多副本见 README §8；注意**每个副本都会各起 2 个 worker**，副本翻倍不等于槽位翻倍 |

## 4. 怎么量（别信 §2.3 的估算）

1. `app doctor --strict` 里 `runtime.queue` 一行直接给在途条数与死信：
   `在途 N 条（pending x / running y），死信 z 条`。
2. `/metrics`（生产要带 `METRICS_TOKEN`）：
   - `worker_jobs_in_flight` —— 长期等于并发数，说明要么真忙、要么有 handler 卡死；
   - `worker_consumers{state="alive"|"desired"}` —— 两者不等说明有协程悄悄退出了。
3. 压测：找 5 个人同时提交同一个模板，记录「提交 → 开始跑」的等待时长、429 次数、
   任务列表里 `queued` 停留时间。

## 5. 想扩，方向要对

- **调大 `WORKER_CONCURRENCY` 对 LibTV 几乎无用**：账号锁才是瓶颈，多出来的槽位
  只会让更多任务反复抢锁空转（然后按退避表重排）。对百炼有用 —— 它没有账号锁。
- **扩 LibTV = 加账号**：起第三个 CDP 浏览器（9413）、加进 `LIBTV_BROWSER_URLS`。
  账号锁按 `host-port` 自动分开，不用改代码。这是唯一能提高上游并发的手段。
- **扩 API = 多副本**（README §8），但每个副本各起 worker，且 `CACHE_DRIVER` 只有
  memory，多副本时限流会变成 N 倍。
- **人多时页面慢**：换 nginx 发静态（README §3 的 B 方案）拿 gzip，或者上 CDN ——
  但内网这点量不值得。

## 6. 这次改了什么

- `TASK_MAX_IN_FLIGHT_PER_USER`（默认 5，`0` = 关）：create 与 retry 都过
  `service._assert_in_flight_room()`，超限 429。用例在
  `api/tests/test_task_in_flight_cap.py`（含「重试同样受限」与「一条跑完就释放」）。
- 测试环境把它置 0（`tests/conftest.py`）：套件里有大量「连造十几条任务」的场景，
  限着会让它们莫名 429。这道闸门由上面那个文件自己调小后测。
