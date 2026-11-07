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

---

## 2. 探针：`/health` 与 `/health/ready` 不是一回事

| 端点 | 回答什么 | 依赖 | 建议用途 |
|---|---|---|---|
| `GET /health` | 进程还活着吗 | 不碰任何依赖 | `livenessProbe`：失败就重启容器 |
| `GET /health/ready` | 现在能正确服务请求吗 | DB、队列、outbox、缓存 | `readinessProbe`：失败就从负载均衡摘掉 |

`/health/ready` 的 `status` 有三档，**必须区别对待**：

| status | 含义 | 编排系统该做什么 |
|---|---|---|
| `ok` | 依赖全可用 | 正常接流量 |
| `degraded` | 能服务，但有东西在漏（outbox 积压超阈值、死信超阈值、缓存不可用） | **继续接流量**，同时告警 |
| `down` | 数据库或队列不可用 | 摘掉这台实例 |

把 `degraded` 当 `down` 处理（例如 readinessProbe 只要非 200 就摘）会让
「事件晚投递几分钟」升级成「整批实例下线」。所以探针仍然返回 **200**，
结论在 body 里：

```json
{"code":0,"data":{"status":"degraded","database":"ok","queueSize":12,
 "checks":[{"name":"outbox.pending","ok":false,"detail":"待投递 1832（阈值 1000）","required":false}]}}
```

阈值在 `HEALTH_OUTBOX_PENDING_LIMIT` / `HEALTH_DEAD_JOB_LIMIT`。

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
| `job_duration_seconds` | histogram | name | 100ms~900s |
| `queue_jobs` | gauge | state | db 驱动按 pending / running / dead 拆分；其它驱动只有 active |
| `outbox_events` | gauge | status | pending / delivered / failed |
| `app_build_info` | gauge=1 | env, 驱动名 | 确认抓的是哪个进程、跑的什么驱动 |

计数类在事件发生时打点；积压类在**抓取时**去查真实来源，所以重启后立刻是对的。

### 3.2 建议的四条告警

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

### 5.2 演练（每季度至少一次）

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

### 5.3 边界（写在最前面，免得出事才发现）

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

## 7. 故障速查

| 症状 | 先看 | 大概率原因 |
|---|---|---|
| 页面全空、接口 200 但没数据 | `queue_jobs{state}` + `outbox_events` | worker 没起（`WORKER_ENABLED=false` 但没单独起 worker） |
| 「任务一直排队」 | `queue_jobs{state="pending"}` 与 worker 日志 | 队列驱动是 `memory` 而 worker 在另一个进程 |
| 通知/计费偶发丢失 | `outbox_events{status="failed"}`、`app doctor` 的 `runtime.outbox` | 订阅方抛异常把重试用尽 |
| 第一条查询报 `no such column` | `app doctor` 的 `schema.head` | 漏跑 `alembic upgrade head` |
| `/metrics` 403 | `METRICS_TOKEN` 是否配置 | 生产环境刻意 fail-closed |
| 面板上路由只有 `/health` 这种短路径 | 指标 `route` 标签 | 旧版本；现在用完整路径 + `{param}` 模板 |
| 限流看起来没生效 | `CACHE_DRIVER` | memory 驱动下多实例各算各的 |
| 素材点开 404 | `app verify-blobs` | 对象被 GC/迁移遗漏，或桶策略变了 |

---

## 8. 已知边界（刻意没做，不是遗漏）

1. **指标是单进程的**：多 worker / 多副本各暴露一份，聚合靠 Prometheus。
   没有 pushgateway，也不落盘。
2. **trace 只有一个 id**，没有 span 树与采样。要按阶段拆分耗时看日志里的
   `X-Process-Time-Ms` 与 job 耗时直方图。
3. **限流是依赖级**，没有全局兜底：新接口忘了挂 `rate_limit()` 就是完全不限。
   架构守卫目前只拦「路由层写事务」，没拦「写路由漏挂限流」。
4. **备份不含对象存储**（见 §5.3）。
5. **功能开关不支持热更新与灰度**（见 §6）。
