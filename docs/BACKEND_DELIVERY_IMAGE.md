# 生图 · 后端交付说明（给 `studio/`）

> 交付方：后端 · 日期：2026-11-06 · 涉及 `api/` 与四份生成物
>
> **先读第一节。** 那里两件事会让你现在这个页面失败：第一件（尺寸）让**每一次**提交都失败
> ——下拉里三个选项全是错的；第二件（张数）在选 2 张或 4 张时失败。
> 服务端已经改成「受理时直接 400」，所以你不会再看到「提交成功、转圈没人管」，
> 但用户选了合法选项却被拒，仍然是前端要修的东西。

## 一、两件必须先改的事（都实测过）

### 1.1 尺寸用了全角 `×`，供应商只认半角 `x`

`studio/src/pages/image/ImagePage.tsx:9`：

```ts
const SIZE_OPTIONS = ['1024×1536', '1024×1024', '1536×1024'].map(...)
```

这里的 `×` 是**乘号**（U+00D7，中文输入法里常被叫「全角乘号」），
供应商接口要的是**英文字母 x**（U+0078）。两个字符长得像，报错时还会被字体渲染得几乎一样。

改之前的实测（用你页面上的默认值提交）：

```
POST /api/v1/generations/image  {"prompt":"…","size":"1024×1536","count":1}
→ 201 已提交，status=queued
→ 3 秒后 status=failed
   errorMessage = "提交生成失败：Agnes 参数校验失败（400）size 请使用英文字母 x，不要使用乘号 ×"
```

**为什么后端不能只靠前端改**：`size` 字段原来只校验了长度（`max_length=32`），
非法值会被原样转发给供应商，失败发生在异步阶段——用户看到的是「生成中」，
几秒后变成一张占位图，原因藏在 `errorMessage` 里而页面上没有任何地方渲染它。

现在后端在**受理时**按模型声明的白名单校验：

```
POST {"size":"1024×1536"} → 400
{"code":40000,"message":"尺寸 '1024×1536' 不可用于模型 'agnes-image-2.5-flash'，
 可选: ['1024x1024', '1024x1536', '1536x1024']（注意是英文字母 x，不是乘号 ×）"}
```

**改法不是把 `×` 换成 `x`**，而是别再写死（见第三节）。真要用常量，也必须是半角 `x`。

### 1.2 数量给了 1 / 2 / 4，但这个模型只支持 1 张

`ImagePage.tsx:10` 的 `COUNT_OPTIONS` 有 `2 张` 和 `4 张`。Agnes 只接受 `n=1`：

```
POST 直连供应商 {"n": 4} → 400 {"error":{"message":"n 必须为 1"}}
POST 走我们的接口 {"count":4} → 201 已提交 → 3 秒后 failed
   errorMessage = "提交生成失败：Agnes 参数校验失败（400）n 必须为 1"
```

根因不只是前端：后端 `GenerateImageIn.count` 声明的是 `1 ≤ count ≤ 4`，
而供应商做不到——**声明了一个不存在的能力**。这个已经改掉两处：

- 供应商适配器里 `count` 的上限改成 `1`（`GET /models` 会如实返回）；
- 受理时按模型声明的范围校验，`count=4` 立刻 400：
  `张数 4 超出模型 'agnes-image-2.5-flash' 的范围（1）`。

> 注意这里有个**模型之间的差异**：`seedream-3` 的 `count` 上限是 4，`agnes-image-2.5-flash` 是 1。
> 所以「能不能选多张」是**按模型变**的，前端不能写死，要读 `paramSchema`。

## 二、生图是异步的，你现在当同步在用

`POST /generations/image` 返回 **201 + `status: "queued"`**。它只表示「受理了」，
图片还没生成（实测 3–12 秒后才有 `outputUrl`）。

前端现在的写法：

```ts
// image.api.ts
export function useGenerateImage() {
  return useMutation({
    mutationFn: (payload) => imageApi.generate(payload),
    onSuccess: () => void qc.invalidateQueries({ queryKey: imageKeys.all }),
  });
}
```

`invalidateQueries` 只会立刻重拉一次——那一刻记录还是 `queued`，所以：

- 列表里新记录 `coverUrl` 是空字符串，`CoverImage` 用 `seed` 画了个占位图，
  看起来像「已经生成好了」；
- `ImagePage.tsx:36` 的 toast 写的是「图片生成中，稍后刷新查看」，
  用户不手动刷新就永远看不到结果；
- 失败完全不可见（`errorMessage` 没渲染）。

### 要补的三件事

**① 有未完成的记录时才轮询**（`@tanstack/react-query` v5）：

```ts
const PENDING = new Set(['queued', 'running']);

export function useImages(page = 1, pageSize?: number) {
  return useQuery({
    queryKey: imageKeys.list(page),
    queryFn: () => imageApi.list({ page, pageSize }),
    // 全部落定就返回 false，避免空转；有 queued/running 才每 3 秒问一次
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((j) => PENDING.has(j.status ?? '')) ? 3000 : false,
  });
}
```

**② 提交成功后停在第 1 页并触发一次轮询**——新记录在最前面，
`setPage(1)` 之后 `useImages(1)` 会因为列表里有 `queued` 自动开始轮询。
不要在这里 `await` 生成结果，接口不会等。

**③ 按 `status` 渲染**，这是用户在页面上唯一能理解进度的依据：

| status | 展示 |
|---|---|
| `queued` | 占位图 + 「排队中」角标 |
| `running` | 占位图 + 进度条（`progress` 0–100） |
| `succeeded` | `outputUrl`（不是 `coverUrl`） |
| `failed` | 占位图 + 失败角标，`title` / 详情里放 `errorMessage` |

`outputUrl` 一定优先于 `coverUrl`：成图会被后端**落到自己的存储**再回填
（`/static/images/<id>.png`），这是长期地址。

这里原本有个坑，已经修掉了：`coverUrl` 以前直接透传供应商的**签名地址**，
而过期后就是裂图——你页面现在正好用 `job.coverUrl` 渲染卡片，等于放了一颗定时炸弹。
现在图片类的封面会跟着产物一起换成落盘地址（两者是同一张图时），视频类封面是另一张图、
仍可能是供应商地址。**稳妥写法仍是 `outputUrl ?? coverUrl`**，别只认 `coverUrl`。

### ⚠️ `outputUrl` 是相对路径，dev 下会 404

`/static/images/<id>.png` 是相对路径。后端直连是 200（实测 1.3 MB），
但 `studio/vite.config.ts` 的 proxy 只代理了 `/api`，所以 `<img src={outputUrl}>`
在 dev 下会打到 `localhost:5173/static/...`，**404，看起来像图挂了**。

两条路选一条：

```ts
// vite.config.ts —— dev 够用，生产同域反代时天然可用
proxy: proxyTarget
  ? {
      '/api':    { target: proxyTarget, changeOrigin: true },
      '/static': { target: proxyTarget, changeOrigin: true },
    }
  : undefined,
```

或者在渲染时自己拼后端 origin。注意这个路径是**公开可读**的（不需要令牌），
所以别把敏感内容当成图片路径的一部分。

## 三、尺寸和数量都别写死：读 `GET /models`

`GET /api/v1/models?capability=image` 返回每个模型的 `paramSchema`，这是**渲染表单的依据**：

```json
{
  "id": "agnes-image-2.5-flash",
  "label": "Agnes Image 2.5 Flash",
  "isDefault": true,
  "paramSchema": {
    "size":  { "type": "enum", "options": ["1024x1024", "1024x1536", "1536x1024"], "default": "1024x1024" },
    "count": { "type": "int", "min": 1, "max": 1, "default": 1 }
  }
}
```

写死会在三个地方咬人：换模型（`count` 上限不同）、换供应商（`size` 白名单不同）、
后端收紧校验（前端还按老常量渲染，用户选什么都被 400）。

顺带一个好处：模型下拉直接来自这里，`model` 字段留空即用默认模型，
所以「不选模型」也是合法请求，不用在前端编一个默认值。

## 四、权限点：手抄的镜像可以删了

后端 `app export-openapi` 现在产出**四份**生成物，第四份是给前端的权限点清单：

```
api/docs/openapi.json
api/docs/API.md
studio/src/shared/api/schema.ts
studio/src/shared/api/permissions.ts      ← 新增
```

它来自 `ModuleSpec.permissions` 的汇总，和 `GET /identity/permissions` 同源。
现在有 22 个权限点，和你们手抄的 `entities/role/model/permission-catalog.ts`
**逐条一致**（code / name / description 都对过，顺序也一致）。

### 改法：把 `permission-catalog.ts` 变成薄壳

```ts
// entities/role/model/permission-catalog.ts
export type { PermissionGroup, PermissionItem } from '@/shared/api/permissions';
export { PERMISSION_CODES, PERMISSION_GROUPS } from '@/shared/api/permissions';
export type { PermissionCode } from '@/shared/api/permissions';

import { PERMISSION_CODES } from '@/shared/api/permissions';

// 注意 Set 的泛型：生成物是 `as const` 的 readonly 字面量元组，
// `new Set(PERMISSION_CODES)` 会推断成 Set<PermissionCode>，`has(string)` 直接报错
const KNOWN = new Set<string>(PERMISSION_CODES);

export const isKnownPermission = (code: string): boolean => KNOWN.has(code);

export function isResolvablePermission(permission: string): boolean {
  if (permission === '*') return true;
  if (permission.endsWith(':*')) {
    const prefix = permission.slice(0, -1);
    return PERMISSION_CODES.some((code) => code.startsWith(prefix));
  }
  return KNOWN.has(permission);
}
```

### `permission-catalog.test.ts` 里的后端比对可以删掉

那条 `it.skipIf(!existsSync(MODULES_DIR))('与后端 ModuleSpec 逐条一致')` 会解析
`api/src/modules/**` 的 `PermissionSpec`。它有两个问题：

1. 只在前端单独检出时**静默跳过**——守卫在 CI 的某些流水线上是绿的，等于没有；
2. 它做的是「比对」，红了还得人去改；生成物是「不用改」。

现在这条由 `api/tests/test_openapi_contract.py::test_permission_catalog_is_up_to_date`
保证（字节比对，`app export-openapi --check` 同样会拦）。留着的另外三条
（无重复、分组非空、通配解析）请保留，它们测的是前端自己的用法。

## 五、已知缺口与这次新增的重试接口

**没有单条查询接口。** `POST` 的 docstring 里写着「客户端轮询 `GET /images` 或明细接口」，
但 `GET /images/{id}` **不存在**（`openapi.json` 里只有 `GET /images` 和 `POST /generations/image`）。
这是我的措辞失误，不是你的问题。

现在的代价：判断一条记录是否完成，只能拉整页列表（24 条/页）。
按第二节的轮询写法，请求量可接受——**先这么用，不要为了拿单条去加 `?id=` 之类的私有参数**。
补 `GET /images/{id}` 会单独提，接口形状定了再通知。

另外一处行为要知道：`count` 将来若支持多张，`outputUrl` 会先变成数组或新增 `outputUrls`。
现在的接口只有单张，前端按单张实现即可，别提前做多图布局。

### 已补：`POST /images/{id}/retry`（带二次确认开关）

R11 复核后新增，失败任务的卡片现在可以放「重试」按钮；R12 复核后给它加了
一道费用闸门，**前端要按 `externalId` 分两种交互**。

```
# A. 普通失败（submitted 但没拿到供应商任务号）
POST /api/v1/images/{id}/retry
→ 200 {"code":0,"data":{"id":"…","status":"queued"},"message":"已重新排队"}

# B. 记录带 externalId（供应商已受理、可能已扣费）
POST /api/v1/images/{id}/retry                      → 409
POST /api/v1/images/{id}/retry?confirmCharged=true → 200
```

- 仅 `failed` 可重试，其它状态返回 409，记录不存在返回 404；
- 权限要 `image:create`（重试会再向供应商提交、再产生费用）；替别人重试还要
  `image:retry:any`，两个开关互相独立；
- 响应**只有 `id` / `status`，不含 prompt**：`retry:any` 不蕴含 `read:all`；
- **B 类的默认 409 不是错误**：`errorMessage` 是「生成结果未能落库…本次提交
  可能已产生费用，请凭供应商任务号（externalId）核对结果后再决定是否重试」
  时，说明图可能已经生成、钱可能已经花了。请把 `externalId` 展示给用户/
  客服去核对账单，只有明确要重做才带 `confirmCharged=true`。
  409 的 `message` 里带任务号和说明，可直接用作二次确认弹窗的文案；
- 确认后后端把旧任务号搬到 `previousExternalId`（列表里可见，用于对账），
  清掉 `errorMessage` / `submitAttempts` / `finishedAt` / `externalId` 后
  重新入队，前端当成一条新的排队记录处理即可。

`ImageJobOut` 因此多了两个字段：`externalId`（供应商任务号）与
`previousExternalId`（上一次提交的任务号）。两者都可为 `null`，
`schema.ts` 已同步。

典型场景：`errorMessage` 是「提交结果未知…请核对后手动重试」时走 A，
人工核对账单后直接点；B 类要先展示任务号再确认。以前这些记录会永久停在
`failed`（文案承诺了出口，实际没有），现在有了。

新权限点 `image:retry:any` 已进生成物 `permissions.ts` 的 `PERMISSION_CODES`
（现在是 23 条）。按钮显隐照第四节从生成物读，别再手抄一份。

## 六、验收脚本

```bash
# 1. 后端起来（仓库根目录）
cd api && ./.venv/Scripts/python.exe -m uvicorn main:app --app-dir src --port 8080

# 2. 拿令牌
TOKEN=$(curl -s -X POST http://127.0.0.1:8080/api/v1/identity/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin@12345"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['accessToken'])")

# 3. 这两个应该 400（改前分别是「201 然后异步失败」）
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/v1/generations/image \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"prompt":"x","size":"1024×1536"}'          # 400

curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/v1/generations/image \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"prompt":"x","size":"1024x1024","count":4}' # 400

# 4. 正常提交 → 201 queued，3–12 秒后 succeeded 且有 outputUrl
curl -s -X POST http://127.0.0.1:8080/api/v1/generations/image \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"prompt":"一只白猫坐在窗台上，柔和自然光","size":"1024x1024","count":1}'

# 5. 成图地址能直连（相对路径 → 拼上后端端口）
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' \
  http://127.0.0.1:8080/static/images/<刚才的 id>.png     # 200 + 约 1.3MB
```

页面上自查两件事：

1. 提交后**不刷新**，3 秒内应该看到卡片从「排队中」变成成图。
   还是「稍后刷新查看」就说明轮询没接上。
2. 第 5 步 200 但页面是裂图 → 就是上面那个 `/static` 代理没配。

## 七、这次没变的（省得你去翻 diff）

- **接口形状有变化，请按最新生成物对齐**：新增 `POST /images/{id}/retry`
  与权限点 `image:retry:any`、响应模型 `ImageRetryOut`（只有 `id` / `status`）；
  R12 之后 **retry 多了可选 query `confirmCharged`**，`ImageJobOut` 多了
  `externalId` / `previousExternalId` 两个可空字段。带 `externalId` 的记录
  不带该参数重试会返回 409（见第五节）。`GenerateImageIn` 的形状、原有路径、
  状态码、错误码 `40000` 没变。`openapi.json` / `API.md` / `schema.ts` 已重新生成，
  `permissions.ts` 本轮无变化（仍是 23 条）。
- `GET /images` 的数据范围仍由 `image:read:all` 决定，**前端不用传 `scope`**：
  有该权限就看全站，没有就只看自己的。
- `POST /generations/image` 需要 `image:create`，列表需要 `image:read`。
- 提交成功会有一次操作留痕（`audit_events` 表，`image.generate`），这是后端内部的事，
  前端不需要任何调用。
