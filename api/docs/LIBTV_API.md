# LibTV 上游接口（逆向实抓，2026-11-17）

平台里所有「浏览器里看得到、任务中心看不到」的状态都来自这里：**积分额度、排队位次、
消费明细、批量估价**。本文是那份契约的唯一出处；实现见
[`modules/model_driver/libtv/account.py`](../src/modules/model_driver/libtv/account.py)，
写操作（上传/提交/查询/下载）仍然只在桥接层，不在本文范围。

> 抓法：在 9412 那个已登录的 Chrome 上开 CDP，`Network.enable` 后打开
> `https://www.liblib.tv/canvas?...` 与 `https://www.liblib.art/calculation`，
> 取 `requestWillBeSent` 的 URL/请求体与响应体。字段名照抄上游，不做二次命名。

## 0. 鉴权：不是 Cookie，是 `token` 头

浏览器里的 `usertoken` Cookie 值，**原样放进名为 `token` 的请求头**即可；同一个
token 对 `api.liblib.tv` 和 `api2.liblib.art` 都有效。另外三个头是风控与跨域校验用的：

```
token: <usertoken>
webid: <webid cookie>
x-language: zh
Origin / Referer: 按目标域填（api.liblib.tv → https://www.liblib.tv，api2 → https://www.liblib.art）
```

取 token 走 `Storage.getCookies`（CDP WebSocket），所以 Python 侧起一次
`node -e "...loginHeaders(...)"`，按账号缓存 5 分钟。401/403 时清缓存重取一次。

## 0.1 端点速查（完整基址）

两个基址，别混：**`API_TV = https://api.liblib.tv`**（任务链）、
**`API_ART = https://api2.liblib.art/api/www`**（账号与积分链）。

| 目的 | 方法与路径 |
|---|---|
| 账号资产 | `GET  {API_ART}/member/account?isApp=false` |
| LibTV 免费额度 | `POST {API_ART}/member/libtvFreePower` |
| 0 积分权益 | `GET  {API_ART}/commerce/activity/benefit` |
| 进度 + 排队位次 | `POST {API_TV}/api/task/generation/progress` |
| 批量估价 | `POST {API_TV}/api/task/generation/power/calculator/batch` |
| 消费汇总 | `POST {API_ART}/power/translogs/management/summary` |
| 消费明细 | `POST {API_ART}/power/translogs/management` |

## 1. 账号额度 —— `member/account`

```
GET https://api2.liblib.art/api/www/member/account?isApp=false
```

只挑平台会读的字段（原响应还有会员等级、套餐、空间等）：

| 字段 | 含义 |
|---|---|
| `data.attr.usablePower` | 可用积分（含赠送） |
| `data.attr.freeUsablePower` | 其中赠送积分 |
| `data.attr.usedPower` / `totalPower` | 累计已用 / 累计获得 |
| `data.attr.taskCanSubmit` | 账号当下还能提交几个任务 |
| `data.attr.concurrent` | `-1` = 不限制并发 |
| `data.name` / `accountLevelName` / `endTime` | 套餐名 / 等级 / 到期 |

> `taskCanSubmit` **不是**画布 0 积分队列的名额，两者别混用：
> 排了 5 个免费任务时它仍然是 10，队列名额是下面 `progress` 里的
> `unlimitedQueueLimit`。

## 2. 生成进度 + 排队位次 —— `task/generation/progress`

```
POST https://api.liblib.tv/api/task/generation/progress
{"taskIds": ["20261117193603599744489", "..."]}
```

```json
{"code":0,"data":{"progresses":[{
  "taskId":"20261117193603599744489","status":0,"progressPercent":9,
  "benefitTag":"rateLimit_powerZero_20261111","power":0,
  "queueReason":"UNLIMITED_SERIAL",
  "unlimitedQueueNum":3,"unlimitedQueueTotal":5,"unlimitedQueueLimit":10
}]}}
```

- `status`：`0` 排队/生成中，`1` 生成中，`2` 成功，`3` 失败；
- `queueReason`：
  - `UNLIMITED_SERIAL` —— 0 积分畅用队列。`unlimitedQueueTotal/Limit` 是**这台机器上**
    排这个队列的任务数 / 名额（画布 10），`unlimitedQueueNum` 是这一条的位次；
  - `RATE_LIMIT` —— 被上游限流推迟，此时带 `delayInfo.remainSeconds`；
  - 空串 = 真在生成（进度百分比才有意义）。
- 一条请求可以带多个 `taskId`，这是唯一便宜的看法：桥接层那条 `poll` 是为了拿成片，
  会下载、落库，不能拿来刷页面。

## 3. 批量估价 —— `power/calculator/batch`

```
POST https://api.liblib.tv/api/task/generation/power/calculator/batch
{"list":[{"taskId":"...","disableBenefitTags":["rateLimit_powerZero_20261111"]}],
 "infiniteSwitch":1}
```

返回 `items[].power / originalPrice / discountRatio`。`disableBenefitTags` + `infiniteSwitch:1`
是「按积分队列算钱」；免费队列用 `infiniteSwitch:0` 且不带这个标签。
单条估价用 `POST .../power/calculator`（`create` 之前那一步就是它）。

## 4. 消费明细 —— `power/translogs/management`

```
POST https://api2.liblib.art/api/www/power/translogs/management/summary   {"opTypeCode":2}
POST https://api2.liblib.art/api/www/power/translogs/management           {"page":1,"pageSize":20,"opTypeCode":2}
```

- `opTypeCode=2` 表示消耗（充值/赠送是别的码）；`sourceCode=30` = `LibTV生视频`；
- 明细里 **`originalBizNo` 就是上游 `task_id`**，`transAmount` 是这条扣了多少积分；
- 这是「提交回执未知」时唯一权威的对账口径：拿到任务号去明细里搜，有记录就是真扣了钱。

消费类型字典：`GET https://api2.liblib.art/api/www/power/trans-sources`。

## 5. 0 积分权益 —— `commerce/activity/benefit`

```
GET https://api2.liblib.art/api/www/commerce/activity/benefit
```

返回活动期内的 `powerZero` 权益：`targetName`（模型名）、`endTime`（到期）、
`tags`（任务里的 `benefitTag` 就是它，如 `rateLimit_powerZero_20261111`）。
**到期后同一批任务会突然开始扣积分**，所以额度页要显示它。

另有两个相关端点：`member/libtvFreePower`（POST `{}`，LibTV 赠送额度与到期）、
`commerce/activity/rateLimitBenefit`（限流权益，通常为空）。

## 5.5 智能去字幕（2026-11-20 逆向）

画布里对视频节点点「智能去字幕」，前端发的**不是**新接口，而是一条普通的生成任务
（与文生视频同一条 `create → progress` 链路），只是 `model` 换成了专用的擦除模型：

```json
POST /api/task/generation/power/calculator   （以及随后的 create，body 相同）
{
  "params": {
    "model": "volcano-subtitle-eraser",
    "mode": "Subtitle",
    "videoList":   ["<源视频 mp4 URL>"],
    "videoListV2": [{"url": "<同一个 URL>", "width": 720, "height": 1280, "duration": 4.04}]
  },
  "metadata": {"node_id": "v-xxxx", "project_id": "<当前画布 uuid>"},
  "provider": "volcano",
  "model": "volcano-subtitle-eraser",
  "taskType": "video",
  "requestId": "<16 位随机>"
}
```

- `create` 前要现取 `POST /api/security/client-verification-token`，把 `requestToken`
  放进 `riskControl`（与积分队列同一个防机器人门槛）；
- **计费**：9412 实测 `power: 0`（免费），但接口照常回 `originalPrice/power`，别写死 0；
- **产物**：`<源文件名>_<unix 秒>_lib_upscaled.mp4`，`taskResult.videos[0].previewPath`；
- **耗时**：4 秒源片约 70-90 秒（排队为 0 时）；
- `videoListV2` 的 `width/height/duration` 参与上游切片估算，缺了会报参数错误；
- `metadata.project_id` 必须是**当前画布**的 uuid，否则 `30001 账号状态已切换`；
  `node_id` 只是画布上的落点，可自造（形如 `v-xxxxxxxx`）。

实现见 `api/scripts/libtv_desubtitle.mjs`（独立脚本，先于工作台集成落地）：

```bash
node api/scripts/libtv_desubtitle.mjs --video https://...mp4 --out out.mp4
node api/scripts/libtv_desubtitle.mjs --video https://...mp4 --dry-run     # 只报价
node api/scripts/libtv_desubtitle.mjs --poll <taskId>                      # 只等结果
node api/scripts/libtv_desubtitle.mjs --video ./local.mp4 --width 720 --height 1280 --duration 4
```

本地文件会先走 `MediaUploader`（含视频审核）再提交；输出一行 JSON，供后端/工作台消费。

## 6. 平台怎么用

| 能力 | 入口 | 权限 |
|---|---|---|
| 账号额度 + 0 积分权益 | `GET /api/v1/model-driver/libtv/account` | `model_driver:read` |
| 批量看进度与排队 | `POST /api/v1/model-driver/libtv/progress` | `task:read` |
| 积分消费明细（含 task_id） | `GET /api/v1/model-driver/libtv/consumption` | `model_driver:read` |
| 运维自查（含最近消费） | `app libtv-account --consumption 5 --tasks <task_id>` | 本机命令 |

**进度接口为什么用 `task:read` 而不是 `model_driver:read`**：排队位次是任务的属性
（用户看自己的任务为什么慢），而 `model_driver:read` 在种子角色里没有任何角色拥有——
只有超级管理员的 `*` 兜住，用它等于「这个功能只有超管看得见」。
代价是这条接口不校验任务归属（`model_driver` 模块按架构约定不能 import `task`）：
入参是上游 task_id，回参只有进度与位次、**不含提示词与产物**，并且挂了限流。
真要收紧归属，得把查询挪到任务模块、经注入口调用，那是另一次改动。

只读接口不带任何写操作：不会建任务、不会取消、不会改账号状态。
写路径仍然只有桥接层一条（`runtime/vendor/...`），避免出现第二条能花钱的路。
