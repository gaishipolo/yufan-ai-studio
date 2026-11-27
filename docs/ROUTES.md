<!-- 由 `npm run gen:routes` 生成，请勿手工编辑。来源：studio/src/modules/*/module.ts -->

# 前端路由清单

共 13 条路由，来自 12 个模块。

> 路由由 `src/app/registry/index.ts` 的 `import.meta.glob` 自动发现；
> 新增模块只需写 `module.ts`，本文件由 `npm run gen:routes` 重新生成。

| 路径 | 模块 | 外壳 | 权限 | 侧栏 |
| --- | --- | --- | --- | --- |
| `/image` | image | shell | `page:image` | 创作 / 生图 |
| `/library` | library | shell | `page:library` | 资产与数据 / 资产库 |
| `/llm` | llm | shell | `page:llm` | 运维 / LLM 配置 |
| `/login` | auth | bare | — | — |
| `/ops` | ops | shell | `page:ops` | 运维 / 运维 |
| `/shots` | shots | shell | `page:shots` | 模板与镜头 / 镜头库 |
| `/storyboards` | storyboards | shell | `page:storyboards` | 模板与镜头 / 分镜模板库 |
| `/studio` | studio | shell | `page:studio` | 创作 / 生成工作台 |
| `/system/user-access` | user-access | shell | `page:user-access` | 系统管理 / 用户与权限 |
| `/tasks` | task-center | shell | `page:task-center` | 资产与数据 / 任务中心 |
| `/templates/library` | templates | shell | `page:templates` | 模板与镜头 / 模板库 |
| `/templates/specs` | templates | shell | `page:template-specs` | 模板与镜头 / 模板封装 |
| `/usage` | usage | shell | `page:usage` | 资产与数据 / 用量看板 |

## 导航分组

- **创作**（order 1）
  - 生成工作台 → `/studio`
  - 生图 → `/image`
- **模板与镜头**（order 2）
  - 模板库 → `/templates/library`
  - 模板封装 → `/templates/specs`
  - 分镜模板库 → `/storyboards`
  - 镜头库 → `/shots`
- **资产与数据**（order 3）
  - 资产库 → `/library`
  - 任务中心 → `/tasks`
  - 用量看板 → `/usage`
- **系统管理**（order 9）
  - 用户与权限 → `/system/user-access`
- **运维**（order 10）
  - 运维 → `/ops`
  - LLM 配置 → `/llm`

## 模块清单

| 模块 | 路由 | 导航 | 插槽 |
| --- | --- | --- | --- |
| auth | 1 | 0 | 1 |
| image | 1 | 1 | 0 |
| library | 1 | 1 | 0 |
| llm | 1 | 1 | 0 |
| ops | 1 | 1 | 0 |
| shots | 1 | 1 | 0 |
| storyboards | 1 | 1 | 0 |
| studio | 1 | 1 | 0 |
| task-center | 1 | 1 | 1 |
| templates | 2 | 2 | 0 |
| usage | 1 | 1 | 0 |
| user-access | 1 | 1 | 0 |
