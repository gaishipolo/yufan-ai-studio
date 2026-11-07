# 运维手册 · 后端

> 面向「服务已经在跑，现在要判断它健不健康、出事怎么办」。
> 怎么加功能看 `EXTENDING.md`，怎么理解代码结构看 `ARCHITECTURE.md`。
>
> 最后更新：2026-11-07（补齐指标 / trace / 备份恢复 / 功能开关）

---

## 1. 上线前五分钟

```bash
cd api
alembic upgrade head          # 生产建表只走迁移，不要依赖 create_all
app doctor                    # 21 项，有 FAIL 直接非零退出
app doctor --strict           # 有 WARN 也退出：首次上线建议这样跑一遍
```

`doctor` 里三条最容易被忽略、代价最大的：

| 检查 | 失败意味着 |
|---|---|
| `schema.head` | 库里没有迁移版本、或停在旧版本 → 第一条访问新列的查询 500 |
| `runtime.outbox` | 事件投不出去（周期任务没跑、订阅方抛异常）→ 通知/计费静默丢失 |
| `runtime.metrics` | 生产没配 `METRICS_TOKEN` → 抓取全是 403，面板空的 |

还要人工确认的两件（脚本测不了）：

- `METRICS_TOKEN` 已配，抓取配置里是 `Authorization: Bearer <token>`；
- 备份定时任务已开，并且**演练过恢复**（见 §5）。

### 升级到「权限自动同步」这一版的额外一步

这一版新增了 `app_meta` 表（迁移 `dbbea50e7828`）。**第一次启动**会：

1. 读不到 `permissions.catalog` 快照 → 认为是从老库升级上来；
2. 做一次**全量补齐**：把 `BUILTIN_ROLES` 里声明、但库里缺的权限点全部加回去；
3. 写入快照，此后只补「这一版新声明的」权限点。

第 2 步可能把管理员手工移除的旧权限加回来（**只此一次**）。如果现网权限形状
是精心调过的，升级后立刻在「角色与权限」界面复核 `editor` / `viewer`；
想先看会发生什么，可以：

```bash
app sync-roles --dry-run   # 只打印会补什么，不写库
```

这条命令和启动流程用的是同一份 `BUILTIN_ROLES`，所以它的输出就是升级后会被加回来的东西。

---

## 2. 探针：`/health` 与 `/health/ready` 不是一回事

| 端点 | 回答什么 | 依赖 | 建议用途 |
|---|---|---|---|
| `GET /health` | 进程还活着吗 | 不碰任何依赖 | `livenessProbe`：失败就重启容器 |
| `GET /health/ready` | 现在能正确服务请求吗 | DB、队列、outbox、缓存 | `readinessProbe`：失败就从负载均衡摘掉 |

`/health/ready` 的 `status` 有三档，**必须区别对待**：

| status | HTTP | 含义 | 编排系统该做什么 |
|---|---|---|---|
| `ok` | 200 | 依赖全可用 | 正常接流量 |
| `degraded` | 200 | 能服务，但有东西在漏（outbox 积压超阈值、死信超阈值、缓存不可用） | **继续接流量**，同时告警 |
| `down` | **503** | 数据库或队列不可用 | 摘掉这台实例 |

`down` 必须落到 **503**，这是这一节最容易被忽略的一条：编排系统读的是状态码，
不是 body。如果 down 也返回 200，包在 body 里的结论再详细也没人执行——
「数据库断连的实例继续接流量」正是这种写法造成的。

`degraded` 则必须留在 200。把 `degraded` 当 `down` 处理（例如 readinessProbe
只要非 200 就摘）会让「事件晚投递几分钟」升级成「整批实例下线」——
摘掉一个实例，剩下的就要扛它那份流量，越摘越容易雪崩。

```json
// 200（degraded）：结论在 body 里
{"code":0,"data":{"status":"degraded","database":"ok","queueSize":12,
 "checks":[{"name":"outbox.pending","ok":false,"detail":"待投递 1832（阈值 1000）","required":false}]}}
```

```json
// 503（down）：状态码说明「别给我流量」，body 说明「哪一项挂了」
{"code":0,"data":{"status":"down","database":"down","queueSize":-1,
 "checks":[{"name":"database","ok":false,"detail":"OperationalError: connection refused","required":true}]}}
```

阈值在 `HEALTH_OUTBOX_PENDING_LIMIT` / `HEALTH_DEAD_JOB_LIMIT`。

### 2.1 探针不覆盖的东西

`/health/ready` 是**浅检查**：每几秒被调一次，不能跑全表扫描或对象存储连通性测试
（慢探针被判定超时，表现和「服务挂了」完全一样）。深检查用 `app doctor`，
或者接一条定时任务跑 `app doctor --strict` 把结果推到监控。

特别地，**worker 是否还活着不在探针里**——探针被调得太频繁，不能每次去问后台协程。
它在指标里：`worker_consumers{state="alive"}` 是实际存活的消费协程数，
`{state="desired"}` 是配置值，两者不等就是有协程死了（三层容错也拦不住的那种）；
`worker_jobs_in_flight` 长期等于并发数则是另一种形态——协程活着但卡在某个 handler 上。

`jobs_processed_total` 不涨 + `queue_jobs{state="pending"}` 在涨仍然是最直观的征兆，
但它是**事后**信号（要等到积压），而且分不清上面两种故障，所以别拿它代替那两个 gauge。

---

## 3. 指标

`GET /metrics`（Prometheus 文本格式，不进 OpenAPI、不走业务鉴权）。
凭据：`Authorization: Bearer $METRICS_TOKEN`；生产未配 token 时 403；
`METRICS_ENABLED=false` 时 404。

### 3.1 暴露了什么

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `http_requests_total` | counter | method, route, status | `route` 是**路由模板**（`/api/v1/tasks/{task_id}`），未匹配记 `unmatched` |
| `http_request_duration_seconds` | histogram | method, route | 分桶 5ms~10s |
| `http_requests_in_flight` | gauge | — | 正在处理的请求数 |
| `jobs_processed_total` | counter | name, outcome | outcome ∈ success / error / timeout / cancelled / unknown |
| `worker_consumers` | gauge | state | alive = 实际存活的消费协程数，desired = 配置值。alive < desired = 协程死了 |
| `worker_jobs_in_flight` | gauge | — | 正在执行的任务数；长期等于并发数 = 有 handler 卡住 |
| `queue_dequeue_errors_total` | counter | — | worker **取任务**失败的次数（队列驱动异常）。持续增长 = worker 在空转，不是某个任务有问题 |
| `queue_polls_total` | counter | outcome | 取任务轮询次数：hit = 队列里有活，miss = 白查一次。`rate(...{outcome="miss"})` 就是空转查询速率——退避生效后它应该远低于 `worker_consumers{alive} / QUEUE_POLL_INTERVAL_SEC` |
| `replacement_recovery_lock_giveups_total` | counter | — | 替换任务恢复**抢不到档案锁**而放弃等待的次数。>0 说明有进程长时间握着 `.lock`，那条 run 停在原地等人看 |
| `job_duration_seconds` | histogram | name | 100ms~900s |
| `queue_jobs` | gauge | state | db 驱动按 pending / running / dead 拆分；其它驱动只有 active |
| `outbox_events` | gauge | status | pending / delivered / failed |
| `app_build_info` | gauge=1 | env, 驱动名 | 确认抓的是哪个进程、跑的什么驱动 |

计数类在事件发生时打点；积压类在**抓取时**去查真实来源，所以重启后立刻是对的。

### 3.2 建议的七条告警

```promql
# 5xx 比例（按路由定位）
sum by (route) (rate(http_requests_total{status=~"5.."}[5m]))
  / sum by (route) (rate(http_requests_total[5m])) > 0.02

# P95 延迟（模板级，不看单条 URL）
histogram_quantile(0.95,
  sum by (le, route) (rate(http_request_duration_seconds_bucket[5m]))) > 3

# 队列积压（持续增长才有意义）
max_over_time(queue_jobs{state="pending"}[10m]) > 200

# 死信 / 投递失败：只要有就应该有人看一眼
queue_jobs{state="dead"} > 0 or outbox_events{status="failed"} > 0

# worker 取不到任务：worker 还活着但队列驱动在报错（数据抖动 / 连接池耗尽）。
# 这一条和「任务失败」不是一回事——它意味着后台整体在空转，堆到 backlog 都不动。
rate(queue_dequeue_errors_total[5m]) > 0

# 替换任务恢复抢不到档案锁、等满六轮后放手：那条 run 的状态不会再自己往前走。
# 恢复作业不会再自己重投（有上限），所以这条不告警就没人知道。
increase(replacement_recovery_lock_giveups_total[30m]) > 0

# 消费协程死了：探针永远不会因此变红，只有这条能发现「进程活着、后台全停」。
# 用 max_over_time 而不是瞬时值：滚动重启期间短暂不等是正常的。
max_over_time(worker_consumers{state="alive"}[5m])
  < max_over_time(worker_consumers{state="desired"}[5m])
```

任务成功率建议用 `increase` 而不是 `rate`，量小时 rate 会抖：

```promql
sum(rate(jobs_processed_total{outcome="success"}[15m]))
  / sum(rate(jobs_processed_total[15m])) < 0.9
```

**不要**给 `queue_jobs` 做「等于 0 才是健康」的告警：空闲时它就是 0，
而积压时你希望看趋势，不是看瞬时值。

---

## 4. 日志与 trace：用一个 id 串完整条链路

每条日志都是 JSON，带 `request_id`（`X-Request-Id`，客户端可以传，不合法就重新生成）。

异步之后不再断链：入队（`queued_jobs.trace_id`）与写 outbox
（`outbox_events.trace_id`）时会快照当时的 `request_id`，worker 执行与事件投递时
恢复上下文。所以：

```bash
# 用户在 20:03 报「生成失败」，前端响应头里的 X-Request-Id 是 7f3a91c2
grep 7f3a91c2 api/var/log/app.log
# 命中的既有 API 入口日志，也有 worker 里的失败日志与事件订阅方日志
```

排查顺序建议：`HTTP 状态码 → 该 request_id 的全部日志 → job id → queued_jobs 行`。
`queued_jobs.last_error` 与 `outbox_events.last_error` 是最后一道线索，
它们存在就说明「框架试过了，失败原因写在这里」。

---

## 5. 备份与恢复

### 5.1 命令

```bash
app backup --out /backup/yufan --note "上线前"
# 输出：backup-20261107-200300/{database.dump, manifest.json}

app restore --from-dir /backup/yufan/backup-20261107-200300 --force
```

- sqlite 用 **backup API** 取一致快照。直接 `cp app.db` 在 WAL 模式下会拿到
  「主文件还没 checkpoint」的半份数据——这种备份恢复时才炸。
- postgres 走 `pg_dump --format=custom` / `pg_restore --clean`，要求这两个命令在 PATH。
- 恢复前会把现库另存为 `<db>.pre-restore-<时间戳>`，恢复错了还能退回去。
- `--force` 不是形式：没有它，目标库已存在就拒绝执行。

### 5.2 自动备份（别只靠手敲）

```bash
BACKUP_AUTO_ENABLED=true
BACKUP_DIR=var/backups
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_DAYS=7      # 0 = 永不删除
```

`@periodic("core.backup")` 每 `BACKUP_INTERVAL_HOURS` 小时跑一次，`leader_only=True`：
多副本只会有其中一个真的执行（抢不到租约就跳过这一轮，下一轮再抢）。
这一条和 `core.outbox` 的取舍相反是有理由的——outbox 重复投递只是冗余，
备份重复执行是实打实的磁盘 + 读锁成本。

清理是**保守**的，三条约束都不要绕：

- 只删形如 `backup-*` 且**带 `manifest.json`** 的目录。`BACKUP_DIR` 指错到 `var/`
  这种大目录时，通配符删除会把整个数据目录带走。
- 按清单里的 `created_at` 判断，**不看目录 mtime**。恢复演练时会把备份 `cp` 到别处，
  mtime 变成今天；按 mtime 排序正好留着最老的一份、把其余全删。
- `BACKUP_RETENTION_DAYS=0` 直接返回。删备份不可逆，不做默认删除。

**每天看一眼有没有新目录**。自动备份失败不会让服务挂（`logger.error` + 调度器记一笔），
所以它可能连续失败而没人知道。建议的告警：`BACKUP_DIR` 下最新目录的 `created_at`
超过 2×`BACKUP_INTERVAL_HOURS`。

### 5.3 演练（每季度至少一次）

```bash
# 1. 备份当前库
app backup --out /tmp/rehearsal
# 2. 恢复到一个临时库（不要覆盖生产）
DB_URL=sqlite+aiosqlite:////tmp/rehearsal/app.db \
  app restore --from-dir /tmp/rehearsal/backup-xxxx --force
# 3. 对账：manifest.json 里各表行数 vs 实际
DB_URL=sqlite+aiosqlite:////tmp/rehearsal/app.db app doctor
```

`manifest.json` 里有 `schema_head`：恢复出来的库版本与当前代码要求的 head 不一致时，
先 `alembic upgrade head` 再启动服务。

### 5.4 边界（写在最前面，免得出事才发现）

- **对象存储的字节不在这份备份里**。归档盘/素材桶靠存储侧策略（版本化、跨区复制）。
  恢复后如果对象缺失，`app verify-blobs` 会列出缺失的 key。
- **运行工作区（`REPLACEMENT_RUNS_ROOT`）也不在**。它是指向归档的指针集，
  恢复 DB 后 `app runs-status` 能看出「指针在、工作区不在本机」的档案数。
- 恢复期间**不要**同时跑 `app prune-blobs` / GC：两份进程都认为自己是权威。

---

## 6. 功能开关与回滚

```bash
FEATURE_FLAGS='{"upload.multipart": false}'   # 关掉一个功能，重启生效
FEATURE_FLAGS=new.upload,new.editor           # 逗号写法等价于全部打开
```

- 开关只决定**分支**，不决定装配。`WORKER_ENABLED`、`METRICS_ENABLED`、
  `ARCHIVE_GC_ENABLED` 这类仍在 `Settings` 里。
- 值在启动时读入，**不支持热更新**。要「改完立刻生效」就得有配置中心，
  而那会把「这个请求走了哪条分支」变成只有日志能回答的问题。
- `app doctor` 的 `runtime.flags` 会列出全部已声明开关。

什么该用开关、什么该回滚：**单个功能行为**用开关；**数据结构/契约**变更必须回滚
部署（开关关不掉已经写进库的新字段）。

---

## 7. 运行期状态：`app_meta` 与写入轨迹

`app_meta` 存的是「上一次运行时系统认定的事实」（目前是权限目录快照），既不进缓存
（会过期）也不进配置（运行期写出）。它的写入很少，但**每次都是一次决策**，所以每次
写入都带版本号、写入者和一句人话摘要。

```bash
app meta                        # 有哪些键、第几版、谁写的、值长什么样
app meta permissions.catalog    # 这个键的值 + 最近 20 次写入轨迹
```

三条语义：

1. **版本号是 CAS 凭据**。`version` 同时是 ORM 的乐观锁列：任何 `UPDATE` 都自带
   `WHERE version = <读到的值>`，被别人抢先改过就抛 `StaleDataError`，而不是盖掉。
   调用方用 `expected_version` 写，失败就重读重算——**决策必须跟着数据一起重来**。
2. **自动路径只增不减**。每次启动跑的引导不允许把集合改小：滚动发布时旧版本实例
   只认识 29 个权限点，让它写就会把快照退回 29，下一次启动把这些当成「新增」补进
   内置角色，管理员手工摘掉的权限被悄悄加回来。要收缩（这一版确实删了权限点）由人跑
   `app sync-roles --prune-snapshot`。

   `--prune-snapshot` 在**权限目录为空时拒绝执行**（只记一条 ERROR）：那种状态只有
   一种解释——模块没被发现（CLI 不走 `create_app()`，必须先 `discover_modules()`）。
   真踩过：命令把整张快照抹成 `[]`，`app meta permissions.catalog` 的轨迹里留下
   「29→0 移除 …」和随后的「0→29 新增 …」，两次写入的 `actor` 是同一个 `host:pid`。
   看到这种掉到 0 再涨回来的轨迹，先怀疑命令的环境，不是怀疑有人手滑。

3. **审计与主写入同事务**。主写入回滚，`app_meta_history` 的记录也消失——否则会留下
   「改过但没改成」的痕迹，比没有更误导。每个键只保留最近 50 条。

排查「权限快照被改小了」的顺序：`app meta permissions.catalog` 看轨迹里的 `actor`
（`host:pid`）与摘要 → 去那台机器翻启动日志 → 确认它跑的是哪个镜像版本。

---

## 8. 故障速查

| 症状 | 先看 | 大概率原因 |
|---|---|---|
| 页面全空、接口 200 但没数据 | `queue_jobs{state}` + `outbox_events` | worker 没起（`WORKER_ENABLED=false` 但没单独起 worker） |
| 「任务一直排队」 | `queue_jobs{state="pending"}` 与 worker 日志 | 队列驱动是 `memory` 而 worker 在另一个进程 |
| 通知/计费偶发丢失 | `outbox_events{status="failed"}`、`app doctor` 的 `runtime.outbox` | 订阅方抛异常把重试用尽 |
| 第一条查询报 `no such column` | `app doctor` 的 `schema.head` | 漏跑 `alembic upgrade head` |
| `/metrics` 403 | `METRICS_TOKEN` 是否配置 | 生产环境刻意 fail-closed |
| 面板上路由只有 `/health` 这种短路径 | 指标 `route` 标签 | 旧版本；现在用完整路径 + `{param}` 模板 |
| 限流看起来没生效 | `CACHE_DRIVER` | memory 驱动下多实例各算各的 |
| 非超管点新功能 403，超管正常 | `app meta permissions.catalog`、该角色权限 | 新权限点没并进内置角色：启动钩子被跳过或没重启；`app sync-roles` 手工补 |
| 素材点开 404 | `app verify-blobs` | 对象被 GC/迁移遗漏，或桶策略变了 |
| 管理员删掉的权限又回来了 | `app meta permissions.catalog` 的写入轨迹 | 滚动发布时旧版本实例启动过（轨迹里的 `actor` 是 `host:pid`）：它的目录更小、把快照写回去了；升级顺序上别让旧实例重启 |

---

## 9. 已知边界（刻意没做，不是遗漏）

1. **指标是单进程的**：多 worker / 多副本各暴露一份，聚合靠 Prometheus。
   没有 pushgateway，也不落盘。
2. **trace 只有一个 id**，没有 span 树与采样。要按阶段拆分耗时看日志里的
   `X-Process-Time-Ms` 与 job 耗时直方图。
3. **限流默认是依赖级**：每个写路由自己声明额度，全局兜底
   `RATE_LIMIT_GLOBAL_PER_MIN` 默认 0（关闭，按客户端 IP 计）。
   「写路由漏挂限流」已经由 `tests/test_route_policy.py` 拦住（必须挂
   `rate_limit`，或在 `api/ratelimit_policy.py` 里写清豁免理由）；
   仍未解决的是**固定窗口的 2× 边界误差**（见 T27）。

   **复核豁免表**（每半年一次，`review_by` 到期会让 CI 红）不要只重读理由，
   那只是把上一版再抄一遍。用两条查询找出「变贵了」的接口，再去改额度或删豁免：

   ```promql
   # 写路由的调用量（豁免表里那些接口是不是已经变成热路径）
   sum by (route) (rate(http_requests_total{method=~"POST|PUT|PATCH|DELETE"}[1h]))
   # 写路由的 P95（变慢通常等于开始调外部资源）
   histogram_quantile(0.95, sum by (le, route)
     (rate(http_request_duration_seconds_bucket{method=~"POST|PUT|PATCH|DELETE"}[1h])))
   ```

   `http_requests_total` 带 `method` / `route` / `status`，`http_request_duration_seconds`
   带 `method` / `route`，所以上面两条查询里的 `method` 筛选项是现成的。
4. **备份不含对象存储**（见 §5.4）。自动备份失败只会记日志，不会让服务挂——
   所以「备份是否真的在跑」需要外部告警盯（见 §5.2 末尾）。
5. **功能开关不支持热更新与灰度**（见 §6）。
