<!-- 由 `npm run gen:mock-coverage` 生成，请勿手工编辑。 -->

# Mock 覆盖率（只记录，不作门禁）

后端端点 153 条（来自 `schema.ts` 的 `EndpointData`），
已注册 mock 路由 80 条（来自 `mockRouteTable()`），
按「同方法 + 正则能匹配」判定疑似覆盖 83 条。

> **这是记录，不是门禁**：CI 不检查覆盖率是否达标，也不要求新增端点必须带 mock。
> 一条 handler 可以覆盖多个端点（例如 `/tasks/([\w-]+)` 同时命中详情与删除），
> 「每个端点都有 mock」无法可靠机械判定；硬做成断言只会变成可绕过的假守卫。
> 判定本身也是启发式的：路径参数代入样例值 `demo`，只求「正则能否命中」，
> 不代表 handler 真的返回了正确数据。改了接口或 mock 后，重跑一次本脚本即可。

## 模块级覆盖率

**4 / 12** 个模块带 `mock.ts`。

| 模块 | mock.ts |
| --- | --- |
| `auth` | ✓ |
| `image` | — |
| `library` | ✓ |
| `llm` | — |
| `ops` | — |
| `shots` | — |
| `storyboards` | — |
| `studio` | — |
| `task-center` | — |
| `templates` | — |
| `usage` | ✓ |
| `user-access` | ✓ |

## 未命中端点的清单

以下端点在当前注册表里找不到同方法的匹配路由。它们**不一定**是缺陷——
有些接口只在真实后端使用（如健康检查、静态资源、归档导入），mock 模式从来不会调到。

| 方法 | 路径 |
| --- | --- |
| GET | `/api/v1/exports` |
| POST | `/api/v1/exports` |
| DELETE | `/api/v1/exports/{}` |
| GET | `/api/v1/exports/{}` |
| GET | `/api/v1/exports/{}/download` |
| GET | `/api/v1/exports/sources` |
| GET | `/api/v1/health` |
| GET | `/api/v1/health/ready` |
| GET | `/api/v1/identity/me/logins` |
| DELETE | `/api/v1/identity/me/sessions` |
| GET | `/api/v1/identity/me/sessions` |
| DELETE | `/api/v1/identity/me/sessions/{}` |
| GET | `/api/v1/identity/password-policy` |
| GET | `/api/v1/identity/permission-tree` |
| GET | `/api/v1/identity/users/{}/logins` |
| GET | `/api/v1/identity/users/{}/permission-exceptions` |
| PUT | `/api/v1/identity/users/{}/permission-exceptions` |
| POST | `/api/v1/identity/users/{}/revoke-sessions` |
| GET | `/api/v1/library/assets/{}/preview` |
| GET | `/api/v1/library/assets/{}/stream` |
| GET | `/api/v1/library/assets/{}/text` |
| PUT | `/api/v1/library/assets/{}/text` |
| GET | `/api/v1/library/assets/{}/text/raw` |
| POST | `/api/v1/library/assets/{}/text/restore` |
| GET | `/api/v1/library/assets/{}/text/versions` |
| GET | `/api/v1/library/assets/{}/text/versions/{}` |
| GET | `/api/v1/library/packs` |
| POST | `/api/v1/library/packs` |
| GET | `/api/v1/library/packs/{}` |
| PATCH | `/api/v1/library/packs/{}` |
| PATCH | `/api/v1/library/tags/{}/parent` |
| POST | `/api/v1/library/text-assets` |
| GET | `/api/v1/llm-config/providers` |
| PUT | `/api/v1/llm-config/providers` |
| DELETE | `/api/v1/llm-config/providers/{}` |
| POST | `/api/v1/llm-config/providers/{}/models:fetch` |
| POST | `/api/v1/llm-config/providers/{}/test` |
| GET | `/api/v1/model-driver/config` |
| PUT | `/api/v1/model-driver/config` |
| GET | `/api/v1/model-driver/drivers` |
| GET | `/api/v1/model-driver/libtv/account` |
| GET | `/api/v1/model-driver/libtv/consumption` |
| POST | `/api/v1/model-driver/libtv/progress` |
| GET | `/api/v1/model-driver/models` |
| POST | `/api/v1/model-driver/refresh` |
| POST | `/api/v1/model-driver/tasks` |
| GET | `/api/v1/model-driver/tasks/{}` |
| POST | `/api/v1/model-driver/tasks/{}/cancel` |
| POST | `/api/v1/model-driver/tasks/{}/retry` |
| POST | `/api/v1/tasks/{}/desubtitle` |
| GET | `/api/v1/template-cases` |
| POST | `/api/v1/template-cases` |
| DELETE | `/api/v1/template-cases/{}` |
| GET | `/api/v1/template-cases/{}` |
| PATCH | `/api/v1/template-cases/{}` |
| POST | `/api/v1/template-cases/{}/offline` |
| POST | `/api/v1/template-cases/{}/online` |
| POST | `/api/v1/template-cases/{}/restore` |
| GET | `/api/v1/template-specs` |
| POST | `/api/v1/template-specs` |
| DELETE | `/api/v1/template-specs/{}` |
| GET | `/api/v1/template-specs/{}` |
| POST | `/api/v1/template-specs/{}/compile` |
| POST | `/api/v1/template-specs/{}/disable` |
| POST | `/api/v1/template-specs/{}/publish` |
| POST | `/api/v1/template-specs/{}/restore` |
| GET | `/api/v1/template-specs/limits` |
| GET | `/api/v1/template-specs/published` |
| GET | `/api/v1/template-specs/trash` |
| POST | `/api/v1/template-specs/validate` |

（未命中 70 条；已命中 83 条不在本表重复列出。）
