<!-- 由 `npm run gen:routes` 生成，请勿手工编辑。来源：studio/src/modules/*/module.ts -->

# 前端路由清单

共 10 条路由，来自 10 个模块。

> 路由由 `src/app/registry/index.ts` 的 `import.meta.glob` 自动发现；
> 新增模块只需写 `module.ts`，本文件由 `npm run gen:routes` 重新生成。

| 路径 | 模块 | 外壳 | 权限 | 侧栏 | 宫格 |
| --- | --- | --- | --- | --- | --- |
| `/image` | image | shell | `image:read` | 创作 / 生图 | — |
| `/library` | library | shell | `library:read` | 资产与数据 / 资产库 | ✓ |
| `/login` | auth | bare | — | — | — |
| `/shots` | shots | shell | `shot:read` | 模板与镜头 / 镜头库 | — |
| `/storyboards` | storyboards | shell | `storyboard:read` | 模板与镜头 / 分镜模板库 | — |
| `/studio` | studio | shell | `catalog:read` | 创作 / 生成工作台 | ✓ |
| `/system/user-access` | user-access | shell | `identity:read` | 系统管理 / 用户与权限 | — |
| `/tasks` | task-center | shell | `task:read` | 资产与数据 / 任务中心 | ✓ |
| `/templates` | templates | shell | `template:read` | 模板与镜头 / 模板库 | ✓ |
| `/usage` | usage | shell | — | 资产与数据 / 用量看板 | — |

## 导航分组

- **创作**（order 1）
  - 生成工作台 → `/studio`
  - 生图 → `/image`
- **模板与镜头**（order 2）
  - 模板库 → `/templates`
  - 分镜模板库 → `/storyboards`
  - 镜头库 → `/shots`
- **资产与数据**（order 3）
  - 资产库 → `/library`
  - 任务中心 → `/tasks`
  - 用量看板 → `/usage`
- **系统管理**（order 9）
  - 用户与权限 → `/system/user-access`

## 模块清单

| 模块 | 路由 | 导航 | 卡片 | 插槽 |
| --- | --- | --- | --- | --- |
| auth | 1 | 0 | 0 | 1 |
| image | 1 | 1 | 0 | 0 |
| library | 1 | 1 | 1 | 0 |
| shots | 1 | 1 | 0 | 0 |
| storyboards | 1 | 1 | 0 | 0 |
| studio | 1 | 1 | 1 | 0 |
| task-center | 1 | 1 | 1 | 0 |
| templates | 1 | 1 | 1 | 0 |
| usage | 1 | 1 | 0 | 0 |
| user-access | 1 | 1 | 0 | 0 |
