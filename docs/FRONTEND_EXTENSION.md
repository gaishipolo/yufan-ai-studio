# 前端扩展指南（骨架 v2）

> 面向：所有在 `studio/` 里加功能的同学。
> 目标：**新增功能只新增文件，不修改骨架；多人并行不产生合并冲突。**
> 配套：`docs/CODE_STYLE.md`（分层与命名）、`studio/eslint.config.js`（自动拦截违规）。

---

## 0. 一句话结论

以前加一个页面要改 3 个文件（`router.tsx` 路由表、`navigation.ts` 导航、可能还有 `Sidebar`），
现在只要 **新增 1 个清单文件**：`src/modules/<id>/module.ts`，骨架自动发现并装配。
一条命令生成：

```bash
npm run gen:module -- user-access "用户权限" "系统管理"
```

---

## 1. 骨架的承诺与边界

### 1.1 三条不变量

| # | 不变量 | 说明 |
|---|---|---|
| 1 | **业务代码不修改骨架文件** | 下表列出的文件属于骨架，业务 PR 不应改动 |
| 2 | **新功能只新增文件** | 新增页面 / 接口 / mock / 权限，都通过声明完成 |
| 3 | **跨模块复用必须下沉** | 两个模块要共用一段逻辑 → 移到 `features` / `entities` / `shared`，不允许模块互相 import |

### 1.2 骨架文件清单（勿改）

```
src/app/App.tsx                应用入口
src/app/providers.tsx          全局 Provider 组装
src/app/router.tsx             由注册表生成路由，已无需手改
src/app/registry/index.ts      模块自动发现
src/app/RouteError.tsx         路由级错误兜底
src/widgets/**                 外壳：顶栏 / 侧栏 / 宫格导航
src/shared/registry/**         注册表内核（类型、校验、插槽）
src/shared/access/**           权限内核（身份、判定、守卫）
src/shared/api/http.ts         请求内核（拦截器、错误归一）
src/shared/ui/**               基础组件（可新增，不改已有 props）
eslint.config.js / vitest.config.ts / tsconfig*.json
```

> 如果业务上确实需要改这些文件，说明**扩展点不够**：先提 issue 说明场景，评审后补扩展点，再写业务。不要临时在骨架里塞 if。

### 1.3 新增功能会碰到的文件 vs 同事的文件

| 你新增的功能 | 你会创建 / 修改的文件 | 会和同事冲突吗 |
|---|---|---|
| 页面 + 导航 | `src/modules/<id>/module.ts`、`src/pages/<id>/*` | 不会（新文件） |
| 数据接口 | `src/entities/<id>/{model,api}/*` | 不会 |
| 本功能 mock | `src/modules/<id>/mock.ts` | 不会 |
| 复用型组件 | `src/features/<id>/*` 或 `src/shared/ui/*`（新增文件） | 不会 |
| 页面内门禁 | 自己的页面（`<Can>`） | 不会 |
| 全局能力（主题/i18n） | `src/app/providers.tsx` | **会** —— 属骨架变更，走评审 |

**没有任何功能需要改 `router.tsx` / `Sidebar.tsx` / `TopBar.tsx` / `navigation.ts`（已删除）。**

> 例外（2026-11-06）：登录 / 会话这类**横切能力**不适用这句。并入用户体系时确实改了
> `app/router.tsx`（+27 / −4）与 `app/providers.tsx`，能力层扩展点尚未定型。
> 详见 `docs/FRONTEND_FRAMEWORK_RETRO_2026-11-06.md` 的 A1。协议定下来之前，
> 新增横切能力仍要走骨架评审，别拿上面这句当「不用改」的依据。

---

## 2. 目录与依赖方向

```
app        装配层：Provider、路由生成、错误兜底
 │
modules    功能切片：一个功能一个目录，声明路由 / 导航 / 插槽 / 权限
 │
pages      路由落点：module 的 load() 一律指向这里（可以是薄壳）
 │
widgets    外壳与跨页组合块
 │
features   业务交互块：可复用组件，或由 pages 薄壳引用的整页实现
 │
entities   领域模型：types / api / queries / 展示组件
 │
shared     无业务：UI 基件、工具、请求内核、注册表、权限内核
```

规则由 ESLint `no-restricted-imports` 强制执行（`studio/eslint.config.js`）：

- 只能向下依赖，反向 import 直接报错；
- `modules` 不能 import `@/app/*`（避免循环：app 发现 modules）；
- `modules` 之间不能互相 import；
- `pages/widgets/features` 不能直接 import `@/shared/api/http`（请求必须写在 entities 的 `*.api.ts`）。

> 实测：在 `shared` 里写 `import { x } from '@/entities/...'` 会被 lint 拦下并提示「分层违规」。

### 2.1 页面分层的现状（`pages` 薄壳 + `features` 整页）

素材 / 模板两页目前是两层：`module.ts` 的 `load()` 指向 `pages/assets/AssetsPage.tsx`
与 `pages/templates/TemplatesPage.tsx`（各约 15 行，只做转发），真正的整页实现在
`features/asset-library/AssetLibrary.tsx`、`features/replacement-templates/ReplacementTemplates.tsx`。
这是 PR #1 合并后的既有形态，**本轮（2026-11-06）决定保留，不把整页搬回 `pages/`**。规则：

- `module.ts` 的 `load()` **必须**指向 `pages/`，不得直接指向 `features/`；
- 一个路由只能有**一处**整页实现，薄壳里不要再叠加第二套 UI；
- 薄壳只做转发（建议不超过 15 行），不放业务逻辑，也不发数据请求；
- 合并时若两层都写了整页，以薄壳引用的那一版为准，删掉重复的一套。

> 背景：合并时曾出现两套 UI 同屏、同接口请求两遍，见
> `docs/FRONTEND_FRAMEWORK_RETRO_2026-11-06.md` 的 A2 / B1。

---

## 3. 扩展点总表

| 想做的事 | 声明位置 | 骨架改动 |
|---|---|---|
| 新增页面 | `module.ts` → `routes[]` | 无 |
| 侧边栏入口 | `module.ts` → `routes[].nav` | 无 |
| 侧栏一级类目（可折叠） | `routes[].nav.group` + `groupOrder` | 无 |
| 顶部宫格卡片 | `module.ts` → `routes[].card` | 无 |
| 导航角标（如进行中任务数） | `routes[].nav.badge`（组件） | 无 |
| 顶栏动作 / 侧栏底部 / 全局浮层 | `module.ts` → `slots[]` | 无 |
| 独立布局页面（登录页这类，不套外壳） | `routes[].layout = 'bare'` | 无 |
| 顶栏账号菜单项 | `slots[]` → `name: 'user-menu.items'` | 无 |
| 页面 / 导航 / 宫格的权限 | `routes[].permissions`（一处声明，另两处自动继承） | 无 |
| 按钮级权限 | 页面里 `<Can permission="...">` | 无 |
| 新接口 | `entities/<id>/api/*.api.ts` | 无 |
| 接口 mock | `modules/<id>/mock.ts` | 无 |
| 新权限点 | 后端下发 + 清单里声明 | 无 |

可用插槽（`SlotName`）：`topbar.actions`、`module-nav.trailing`、`sidebar.footer`、`shell.overlay`、`user-menu.items`。
新增**插槽内容**不用改骨架；新增**插槽种类**属于骨架变更。

### 3.1 侧栏分组 = 一级类目（自动可折叠）

`routes[].nav.group` 就是侧栏的一级类目，**不需要额外声明是否可折叠**：

| 行为 | 由谁决定 |
|---|---|
| 是否可折叠、默认展开 | 骨架（`widgets/sidebar`）统一处理 |
| 折叠状态记忆 | `localStorage` 的 `sf_sidebar.collapsedGroups`，按分组标题存 |
| 分组顺序 | `groupOrder`（小的在前），同值按注册顺序 |
| 无可见项的分组 | 自动隐藏标题（避免留下点不动的折叠头） |
| 进入该分组的页面 | 自动展开（首屏加载不展开，尊重上次的折叠） |

加一个分组只需要写 `group: '系统管理'` + `groupOrder`。想插到两个已有分组之间就取中间值
（`1` 与 `2` 之间用 `1.5`）—— **不要为了排序去改别人模块的数字**，那正是并行冲突的来源。

### 3.2 页面外壳：`layout: 'shell' | 'bare'`

默认 `'shell'`：套顶栏 / 侧栏 / 宫格，**并要求已登录**（骨架统一包 `<RequireAuth>`）。

`'bare'` 是给「脱离应用框架的整页」用的，目前只有登录页：

| 特性 | `shell` | `bare` |
|---|---|---|
| 顶栏 / 侧栏 / 宫格 | 有 | 无 |
| 登录守卫 | 有 | 无（否则登录页会把自己重定向到自己，死循环） |
| 权限守卫 | 有 | 有（声明了 `permissions` 才校验） |
| 能声明 `nav` / `card` | 能 | **不能**（注册期直接报错，不是运行到一半才发现） |
| 典型场景 | 业务页面 | 登录 / 注册 / 第三方回调 / 大屏 |

新功能**默认不要用 `bare`**。真需要时，`app/router.tsx` 会自动把它挂到顶层，不用改路由文件。

---

## 4. 标准流程：五分钟加一个功能

### Step 0 · 起手

```bash
npm run gen:module -- user-access "用户权限" "系统管理"
```

生成：

```
src/modules/user-access/module.ts       清单（路由 / 导航 / 权限占位）
src/modules/user-access/mock.ts         本功能 mock（默认空）
src/pages/user-access/UserAccessPage.tsx 页面占位
```

跑 `npm run dev`，导航里已经出现「用户权限」，点进去是占位页 —— 注册表生效了。

### Step 1 · 先定契约

打开后端接口文档 / 和后端确认 JSON，先把 `entities/<id>/model/types.ts` 写出来。
**先类型后页面**：类型是前后端唯一契约，页面只是消费者。

### Step 2 · 写 API（标准 RESTful 直接套工厂）

```ts
// src/entities/user/api/user.api.ts
import { api } from '@/shared/api/http';
import { createRestResource } from '@/shared/api/rest';
import type { User, UserListParams, UserListResult } from '../model/types';

export const userApi = {
  // list / detail / create / replace / update / remove / action 一次拿全
  ...createRestResource<User, UserListResult, UserListParams>('/users'),
  // 领域动作单独补，避免污染通用工厂
  setRoles: (id: string, roleIds: string[]) => api.put<User>(`/users/${id}/roles`, { roleIds }),
};
```

### Step 3 · 写查询

```ts
// src/entities/user/api/user.queries.ts
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createQueryKeys } from '@/shared/api/rest';
import { userApi } from './user.api';
import type { UserListParams } from '../model/types';

export const userKeys = createQueryKeys<UserListParams>('users');

export function useUserList(params: UserListParams) {
  return useQuery({
    queryKey: userKeys.list(params),
    queryFn: () => userApi.list(params),
    placeholderData: keepPreviousData,
  });
}

export function useSetUserRoles() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; roleIds: string[] }) => userApi.setRoles(vars.id, vars.roleIds),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}
```

### Step 4 · 写页面

页面只做三件事：读 query、调 mutation、渲染。请求逻辑不要写在组件里（lint 会拦 `http` 直调）。

### Step 5 · 写 mock（可选但强烈建议）

见 §5.8。后端没就绪时，页面能独立开发和演示。
覆盖率现状由 `npm run gen:mock-coverage` 记到 `docs/MOCK_COVERAGE.md`——**只记录，不做 CI 门禁**：
一条 handler 可能匹配多个端点，「每个端点都有 mock」无法可靠机械判定，硬做成断言只会变成可绕过的假守卫。
改了接口或 mock 后重跑一次即可，`--check` 只比对不写盘。

### Step 6 · 声明权限

在 `module.ts` 打开 `permissions`，见 §5.4。**没声明 = 公开页面**。

### Step 7 · 验收

```bash
npm run verify     # tsc + eslint + vitest + build，四关全绿才算完
```

---

## 5. 实战：用户权限管理

> **状态：已实现**（`modules/user-access` + `pages/user-access` + `entities/user|role` + `features/*`）。
> 下面的契约表已按**后端实际行为**校准过两处易错点：重置密码字段是 `newPassword`，
> 角色列表返回裸数组。§5.3 的示例代码是「最小骨架」，实际文件划分见 §5.2。

以下按「前端只负责展示与提交，鉴权以服务端为准」的思路落地。

### 5.1 先和后端对齐契约（标准 RESTful）

> 好消息：后端 `api/src/modules/identity/` **已经实现**，共 19 条路径（`api/docs/openapi.json` 为准）。
> 下面的表是导览，**令牌相关字段以 `studio/src/shared/api/schema.ts` 的 `TokenOut` 为准**：
> 类型从生成物派生（`entities/session/model/types.ts`），后端加字段前端自动跟上，
> 别再照表格手抄一遍 —— 上一版就是这样漏掉 `refreshToken` 的。
>
> 前缀 `/api/v1/identity`，前端直接消费即可，不要另起一套。

| 用途 | 方法与路径 | 关键字段 |
|---|---|---|
| 登录 | `POST /identity/login` | `{ username, password }` → `TokenOut`：`{ accessToken, tokenType, expiresIn, refreshToken, refreshExpiresIn, user }` |
| 续期 | `POST /identity/refresh` | `{ refreshToken }` → `TokenOut`（**轮换**：旧 refresh 立即失效） |
| 登出 | `POST /identity/logout` | 空体，Bearer 头带 access 令牌；幂等，重复调用仍 200 |
| 当前身份 | `GET /identity/me` | `MeOut`：`UserOut` + `permissions: string[]` |
| 改自己资料 | `PATCH /identity/me` | `{ displayName?, email?, avatarUrl? }` |
| 改自己密码 | `POST /identity/me/password` | `{ oldPassword, newPassword }`；**成功后吊销本账号全部会话**（含当前设备） |
| 我的登录设备 | `GET /identity/me/sessions` | `list[SessionOut]`，含 `current` 标记 |
| 退出其他设备 | `DELETE /identity/me/sessions` | 保留当前会话 → `{ revoked: number }` |
| 踢掉一条设备 | `DELETE /identity/me/sessions/{session_id}` | `data: null`；只能踢自己的（别人的 sid 查不到 → 404） |
| 我的登录记录 | `GET /identity/me/logins?limit` | `LoginEventListOut`（按条数取，不分页） |
| 用户列表 | `GET /identity/users?page&pageSize&keyword&isActive&roleId` | `Page<UserOut>` |
| 新建用户 | `POST /identity/users` | `{ username, password, displayName, email, roleIds }` → 201 |
| 用户详情 | `GET /identity/users/{id}` | `UserOut` |
| 编辑 / 停用 | `PATCH /identity/users/{id}` | `{ displayName?, email?, isActive? }` |
| 分配角色 | `PUT /identity/users/{id}/roles` | `{ roleIds: string[] }`（整组替换） |
| 重置密码 | `POST /identity/users/{id}/reset-password` | `{ newPassword }`（**不是** `password`）；**吊销目标账号全部会话** |
| 停用用户 | `DELETE /identity/users/{id}` | 保留历史归属，非物理删除；返回停用后的 `UserOut` |
| 踢下线 | `POST /identity/users/{id}/revoke-sessions` | → `{ revoked: number }` |
| 某人的登录记录 | `GET /identity/users/{id}/logins?limit` | `LoginEventListOut`；需 `identity:read`，且目标不能高于操作者 |
| 角色列表 | `GET /identity/roles` | **裸数组**（不分页），含 `permissions` / `userCount` / `orphanedPermissions` |
| 新建角色 | `POST /identity/roles` | `{ code, name, description, permissions }` → 201 |
| 编辑角色 | `PATCH /identity/roles/{id}` | `{ name?, description? }` |
| 改角色权限 | `PUT /identity/roles/{id}/permissions` | `{ permissions: string[] }`（整组替换） |
| 删角色 | `DELETE /identity/roles/{id}` | 内置角色 / 仍有成员时拒绝；`data: null` |
| 权限目录 | `GET /identity/permissions` | `{ groups: [{ group, items: [{ code, name, description }] }], total }` |
| 口令策略 | `GET /identity/password-policy` | 下发 `minLength` / `minUniqueChars` 等；前端目前用本地常量，见 `entities/user/model/validation.ts` |

统一约定（与现有后端一致，前端拦截器已适配）：

- 成功：`{ code: 0, message: 'OK', data: ... }`
- 失败：HTTP 4xx/5xx + `{ code, message, data: null }`，前端抛 `ApiError`
- 分页：`{ items, total, page, pageSize }`
- 字段：后端出参 camelCase（`displayName` / `isActive` / `lastLoginAt`），前端 types 直接照抄
- 权限点：`资源:动作`。身份模块的权限码是
  `identity:read` / `identity:create` / `identity:update` / `identity:delete` / `identity:role:manage`
  （`role:manage` 故意独立：能改昵称 ≠ 能给自己提权）

### 5.2 文件落点

```
src/modules/user-access/module.ts          清单：路由 + 导航 + 权限
src/modules/user-access/mock.ts            本功能 mock（14 条断言钉住契约）
src/entities/user/model/types.ts           User / UserListParams / 展示名兜底
src/entities/user/api/user.api.ts          userApi（list/detail 走工厂，写操作各自入参）
src/entities/user/api/user.queries.ts      userKeys / useUserList / 三个 mutation
src/entities/role/model/types.ts           Role / PermissionCatalog / cannotDeleteReason
src/entities/role/api/role.api.ts          roleApi（注意 roles 返回裸数组）
src/entities/role/api/role.queries.ts      roleKeys / useRoleList / usePermissionCatalog
src/entities/role/ui/RoleCheckboxList.tsx  角色多选（新建用户与分配角色共用）
src/features/user-form/UserFormModal.tsx   新建 / 编辑用户
src/features/user-roles/AssignRolesModal.tsx
src/features/user-security/ResetPasswordModal.tsx
src/features/user-security/DeactivateUserModal.tsx
src/features/role-editor/RoleFormModal.tsx
src/features/role-editor/RolePermissionsModal.tsx
src/pages/user-access/UserAccessPage.tsx   页面组装（用户 / 角色两个页签）
src/pages/user-access/UsersSection.tsx     用户列表：搜索 / 筛选 / 分页 / 行内动作
src/pages/user-access/RolesSection.tsx     角色卡片：成员数 / 失效权限 / 删除保护
```

> 判断放哪一层：**只在页面用 → 页面旁边；多个页面用 → features；跨产品复用 → entities/ui 或 shared/ui。**

### 5.3 类型与 API

字段与后端 `modules/identity/schema.py` 一一对应（照抄即可，不要自己发明 `status` 字段）：

```ts
// src/entities/user/model/types.ts
export interface RoleBrief {
  id: string;
  code: string;
  name: string;
  isSuperuser: boolean;
}

export interface User {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  avatarUrl: string | null;
  isActive: boolean;
  lastLoginAt: string | null;
  createdAt: string; // ISO 8601
  roles: RoleBrief[];
}

/** GET /identity/me 比 UserOut 多权限点数组 */
export interface Me extends User {
  permissions: string[];
}

export interface UserListParams {
  page?: number;
  pageSize?: number;
  keyword?: string;
  isActive?: boolean;
  roleId?: string;
}

export interface UserListResult {
  items: User[];
  total: number;
  page: number;
  pageSize: number;
}
```

```ts
// src/entities/user/api/user.api.ts
import { api } from '@/shared/api/http';
import { createRestResource } from '@/shared/api/rest';
import type { Me, User, UserListParams, UserListResult } from '../model/types';

const base = '/identity/users';

export const userApi = {
  ...createRestResource<User, UserListResult, UserListParams>(base),
  // 领域动作：通用工厂只管 CRUD，提权/重置这类单独写，避免污染
  setRoles: (id: string, roleIds: string[]) => api.put<User>(`${base}/${id}/roles`, { roleIds }),
  // 后端字段是 newPassword：写成 password 会 422，且只在联调时才暴露
  resetPassword: (id: string, newPassword: string) =>
    api.post<null>(`${base}/${id}/reset-password`, { newPassword }),
};

export const identityApi = {
  login: (payload: { username: string; password: string }) =>
    api.post<{ accessToken: string; tokenType: string; expiresIn: number; user: Me }>(
      '/identity/login',
      payload,
    ),
  me: () => api.get<Me>('/identity/me'),
  permissions: () =>
    api.get<{ groups: Array<{ group: string; items: Array<{ code: string; name: string; description: string }> }>; total: number }>(
      '/identity/permissions',
    ),
};
```

角色实体同理（`entities/role/`），字段：`id / code / name / description / isSuperuser / isBuiltin / sortOrder / createdAt / permissions / userCount / orphanedPermissions`。

> `orphanedPermissions` 是后端给的「库里还留着、但权限点已下线」的提示，页面要展示出来，否则管理员会看到“明明取消了却还能访问”的错觉。

### 5.4 模块清单：一行权限，三处生效

```ts
// src/modules/user-access/module.ts
import { ShieldCheck } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'user-access',
  title: '用户与权限',
  routes: [
    {
      path: 'system/user-access',           // 多级路径直接写，无需改路由表
      load: () => import('@/pages/user-access/UserAccessPage'),
      permissions: ['identity:read'],        // ① 只写一次：路由 / 侧栏 / 宫格三处共用
      nav: {                                // ② 侧栏入口：path 与 permissions 从本路由继承
        label: '用户与权限',
        icon: ShieldCheck,
        group: '系统管理',
        groupOrder: 9,                      // 排在业务分组之后
      },
      // card: { subtitle: '…', gradient: '…', order: 4 },  // ③ 需要宫格时打开
    },
  ],
});
```

`nav` / `card` 挂在路由上，**不写 `path` 与 `permissions`**：注册表从所属 route 派生。
以前平级的 `nav[]` / `cards[]` 要把同一个路径和同一组权限各抄三遍，改权限漏一处就是
「入口可见但点进去 403」；现在从结构上不可能发生。`card.title` / `card.icon` 缺省取
`nav.label` / `nav.icon`，需要不同值时才显式覆盖。

生效链路（全部自动，无需改骨架）：

1. 侧边栏 / 宫格按派生自 route 的 `permissions` 过滤；
2. 直接访问 URL → `<RequirePermission>` 渲染 403（`ForbiddenState`）；
3. 权限点来源 `Subject.permissions`，由登录模块写入 session。

> **`permissions` 是必填的。** `router.test.ts` 断言「除豁免名单外，每个 shell 路由的
> `permissions.length > 0`」——因为 `can(subject, [])` 恒为 `true`，一条都不写等于对任何
> 已登录用户放行，而"有没有被守卫包住"看起来完全正常。新模块确实没有后端权限点时，
> 显式加进 `NO_PERMISSION_PATHS`（并在注释里写清理由），不要靠"忘了写"来豁免。
>
> 权限码拼错同样不会报错，只会让入口对**所有人**永久隐藏。`router.test.ts` 还会检查
> 每个声明的权限点都在 `entities/role/model/permission-catalog.ts` 里；那份目录现在只是
> `shared/api/permissions.ts`（后端 `app export-openapi` 生成物）的薄壳，由后端
> `test_permission_catalog_is_up_to_date` 做字节比对，漂移会直接让 CI 红。

### 5.5 页面里的按钮级门禁

```tsx
import { Can } from '@/shared/access/Can';

{/* 改角色权限是提权动作，必须单独一个权限点，不能和「编辑用户」合并 */}
<Can permission="identity:role:manage">
  <Button onClick={openRoleEditor}>编辑角色权限</Button>
</Can>

{/* 「停用」属于 identity:delete，不是 identity:update */}
<Can permission="identity:delete">
  <Button variant="danger" onClick={deactivate}>停用</Button>
</Can>

{/* 数组表示「全部满足」；任一满足用 useCanAny */}
<Can permission={['identity:update', 'identity:role:manage']}>...</Can>
```

**三层防线，缺一不可：**

| 层 | 作用 | 性质 |
|---|---|---|
| 导航隐藏 | 少看到不该看的入口 | 体验 |
| 路由守卫 | 防手输 URL / 深链接 | 前端约束 |
| 后端鉴权 | 真正的安全边界 | 必须实现 |

> 前端门禁**不是**安全措施：任何人都能改浏览器内存。所有写接口后端必须独立校验。

#### 5.5.1 两条容易漏的边界（都踩过）

**① 操作自己：不要等后端 409 才告诉用户**

停用、删除自己的账号后端会拒绝（`CannotTargetSelf`），但「点一下才知道」的体验很差，
而且用户会以为是自己操作错了。做法是**在自己那一行就把按钮禁掉并说明原因**：

```tsx
const subject = useSubject();                                  // shared/access/use-access
const isSelf = user.id === subject.id;

<span title={isSelf ? '不能停用自己的账号' : undefined}>      {/* disabled 按钮不触发鼠标事件，title 要挂在外层 */}
  <Button disabled={isSelf} onClick={() => open('deactivate', user)}>停用</Button>
</span>
```

改**自己**的角色则不能禁——转交超管是正常流程，先撤自己才安全。改法是给提示而不是关入口
（`features/user-roles/AssignRolesModal.tsx`：`isSelf` 时渲染一条 `role="status"` 的降权提醒）。

**② 客户端校验只写一份**

口令与邮箱规则原来散在 4 个弹窗 + 2 个 mock 里（`10` 这个数字出现过 5 次），
后端改策略必然漏改。现在统一放在 `entities/user/model/validation.ts`：

```ts
export const PASSWORD_MIN_LENGTH = 10;
export function checkPassword(value: string, label = '密码'): string | null;
export function checkEmail(value: string | null | undefined): string | null;
export function checkAvatarUrl(value: string | null | undefined): string | null;
```

约定：**返回错误文案、合法返回 `null`**（而不是抛异常或返回布尔），
调用方一行 `if (err) { setFormError(err); return; }` 就能用，
`label` 用来区分「初始密码 / 新密码」的文案。

### 5.6 登录与会话（已实现，新模块照这个接）

骨架只暴露一个写入点：`useSessionStore`。写进去之后，导航过滤、路由守卫、`<Can>`、
顶栏显示全部自动跟随——不需要改任何骨架文件。

实现分三层，新功能照抄：

| 层 | 文件 | 职责 |
|---|---|---|
| 实体 | `entities/session/api/session.queries.ts` | `useLogin` 写会话、`useMe` 刷新身份、`useUpdateProfile` 回写 |
| 交互 | `features/auth/*` | 登录表单、个人资料、修改密码（只管收集与报错） |
| 页面 / 清单 | `pages/login/LoginPage.tsx` + `modules/auth/module.ts` | `layout: 'bare'` 路由 + 顶栏菜单插槽 |

核心只有两行（`useLogin` 内部）：

```ts
queryClient.setQueryData(sessionKeys.me, result.user);
signIn({
  token: result.accessToken,
  expiresIn: result.expiresIn,
  refreshToken: result.refreshToken,      // 少了这一对，2 小时后就只能回登录页
  refreshExpiresIn: result.refreshExpiresIn,
  subject: toSubject(result.user),
});
```

`toSubject(me)` 是后端 `Me` → 骨架 `Subject` 的**唯一**映射点，后端字段变了只改这一处。

> 不要自己写 `localStorage.setItem('sf_token')`，也不要再写第二份 setSubject。
> 令牌的唯一真相是 session store，`http.ts` 的请求拦截器也从那里读；
> 存两份迟早出现「已经登出，请求还在带旧令牌」。

401 的处理在 `shared/api/http.ts`，分两级：**先换票，再重放原请求；换不到才清场**。

> 跳转刻意不写在拦截器里。会话变 `anonymous` → 守卫重定向，这样「谁能进来」只有一处真相，
> 不会出现拦截器跳一个地址、守卫又跳另一个的打架。

会话三态 `loading / authenticated / anonymous` 不要合并成布尔量：
少了 `loading`，刷新页面时会先闪一下登录页再跳回来。

#### 5.6.1 会话怎么结束（五条路径，一个出口）

会话有**五条**结束路径，其中四条是被动发生的（到点、401、别的标签页退出、改密）。
它们以前各自 `signOut`，只有手动退出顺手清了查询缓存——于是「401 → 回登录页 →
同标签页换账号登录」会在 `staleTime` 内把上一个账号的生图历史、角色列表当成新账号的数据渲染出来。

现在五条路径统一走 `shared/access/end-session.ts` 的 `endSession(reason)`：
**清会话 + 清查询缓存**写在一处，谁也别再自己拼。

| 触发 | 谁来做 | 调用 |
|---|---|---|
| 到点过期 | `useSessionExpiryWatch` 定时器（`expiresAt` 由登录返回的 `expiresIn` 算出） | 先续期；续不上 → `endSession('expired')` |
| 冷启动换票被拒 | `SessionBootstrap`（只有刷新令牌时先换票） | `endSession('expired')` |
| 服务端 401 | `http.ts` 响应拦截器 | 先续期并重放；续不上 → `endSession('unauthorized')` |
| 别的标签页退出 | `useSessionCrossTabSync` 听 `storage` 事件 | `endSession('external')` |
| 用户点退出 | `widgets/user-menu/use-sign-out.ts` | `endSession('manual')` + 跳登录页 |
| 本人改完密码 | `features/auth/ChangePasswordModal` | `endSession('password')` |

两个 hook 都挂在 `app/SessionBootstrap.tsx`——它们必须常驻且与页面无关。
登出原因只影响登录页的一句提示（`RequireAuth` 把 10 秒内的原因放进 `location.state.reason`），
**不参与任何权限判断**，别拿它做业务分支。

`endSession` 里还有一件只有 `manual` 才做的事：`POST /identity/logout`。
不调它意味着「退出登录」在服务端什么都没发生——令牌有效到 2 小时后，
`/identity/me/sessions` 里那条还是「本机在线」。共用电脑上这是安全问题，不是体验问题。
顺序是**先清本地再发请求**、fire-and-forget：反过来会出现「网络断了就退不出去」。
其余不调：会话已经不可用（续期失败）、另一个标签页已经调过，或者后端改密时已经
`revoke_all`（`password` 那条）。

还有两条容易漏的路径：

1. **引导失败**。刷新时只有令牌、没有身份快照（`Subject` 结构改过、清过缓存、
   首次在本机打开）时，store 停在 `loading` 等 `/identity/me` 定性；
   如果那次请求回的是 500 / 断网而不是 401，拦截器不会清场，`status` 会永远停在 loading。
   所以 `SessionBootstrap` 在「仍是 loading 且失败不是 401」时渲染 `SessionRecovery`
   （重试 / 重新登录两个出口）——不假装还在加载。
2. **冷启动时 access 已过期、刷新令牌还活着**（关掉页面几小时再回来、手机锁屏、过夜）。
   `resolveInitialSession` 只清 access 与快照、**留住刷新令牌**并进 `loading`，
   由 `SessionBootstrap` 先换票再取身份。漏了这一步会有两种事故：
   把刷新令牌一起删掉（7 天会话退化成 2 小时），或者 `useMe(Boolean(token))` 的
   enabled 恒为 false、查询永远不发，页面无声地卡死在整页 loading。
   换票遇到 401 → `endSession('expired')`；遇到网络故障 → `SessionRecovery`（本地凭证还在，重试有意义）。

#### 5.6.1.1 续期（`shared/access/token-refresh.ts`）

后端 `access` 令牌 2 小时、刷新令牌 7 天且滑动续期。不做续期的后果很具体：
每个用户上午、下午各掉一次线，而服务端本来愿意让他待 7 天。

三条规矩，缺一条就会变成 bug：

1. **单飞**。一屏同时有 6 个请求，令牌过期时它们会一起 401。六个并发续期里
   第一个成功并轮换了 refresh，其余五个拿旧票必然失败（后端把旧 jti 判无效），
   用户看到的是「明明还在用却突然退出」。并发调用共享同一个 Promise。
2. **失败先怀疑「别的标签页换过了」**。多标签页同时 401 时，后到的那个手里一定是旧票。
   这时去看 `localStorage`：更新的票就 `adoptStoredTokens()` 跟过去（会校验是同一个人），
   而不是把用户踢出去。
3. **不碰身份，也不碰路由**。`/identity/refresh` 不返回用户信息，权限等下一次 `/identity/me`；
   跳转仍由守卫读 `status` 决定。

续期与登出走 `shared/api/auth.ts` 的**旁路通道**（一条不带拦截器的 axios 实例），
原因是它们自己就可能 401：走主干会再进「401 → 续期」的分支，形成递归；
而登出发生在本地清场之后，主干注不进鉴权头，服务端就不知道该吊销哪条会话。
这也是为什么 `sessionApi` 里没有 `refresh` / `logout`——不是漏了。

三个写入点因此各有一条守卫，别去掉：

- `setSubject`：没有令牌就直接丢弃（`/me` 的 `staleTime` 是 5 分钟，重连后可能迟到）；
  并且**写进来的 subject 必须属于当前会话**（换号后 A 的 `/me` 迟到会让界面带着
  B 的令牌显示 A 的权限）——从占位身份补全是唯一例外。
- `applyRefreshedTokens`：没有 access 也没有刷新令牌才丢弃；冷启动换票时收下新票
  但**保持 loading**，等 `/me` 回来再点亮，否则导航会带着空 subject 渲染一帧。
- `adoptStoredTokens`：只跟同一账号、且比本地更新的票。

#### 5.6.2 登录失败要说人话

登录是唯一「匿名可达」的接口，失败原因不止一种，全部甩后端原文会让用户
把「被限流」「后端挂了」都当成「密码错了」然后继续试——而继续试正是限流要拦的。

分类在 `features/auth/login-feedback.ts`，分档依据是**状态段**而不是完整业务码：

```ts
errorHttpStatus(err.code) // 40121 → 401、42900 → 429、50000 → 500
```

> 后端约定 `code = http_status * 100 + 模块号 * 10 + 序号`（`api/src/core/exceptions.py`），
> 所以 `code // 100` 恒等于 HTTP 状态码。**不要按完整业务码写分支**：
> identity 的登录失败是 `40121`，不是框架的 `40100`，写死具体码换个模块就静默失效。
> 顺带一提，mock 适配器直接透传裸状态码（401 / 429），这套写法两边都兼容。

限流（429）时按钮进入按秒倒计时锁定，时长从后端文案里解析（解析不到退回 60 秒兜底）。
后端策略是「前 5 次 401，第 6 次起 429，窗口 15 分钟」，前端**不硬编码这个阈值**，
只在连续失败到第 3 次时软提示一句——阈值写在两处，迟早对不上。

### 5.7 本地怎么登录

登录页 `/login`，账号与真实后端一致（`api/src/seed.py`）：

| 账号 | 密码 | 昵称 | 权限 |
|---|---|---|---|
| `admin` | `admin@12345` | 老大 | `*`（超管，全部权限） |
| `editor` | `editor@12345` | 小林 | 内容生产 |
| `viewer` | `viewer@12345` | 阿 May | 只读（用来看 403 与菜单隐藏） |

- 直连后端（`VITE_USE_MOCK=false`）：上面三个账号就在种子数据里。
- mock 模式（`VITE_USE_MOCK=true`）：`modules/auth/mock.ts` 里同名同密码的账号，
  登录页会直接提示；不启后端也能走完「登录 → 401 → 回登录页」整条链路。
  mock 同样复现了登录限流（第 6 次失败起 429）——想验证锁定态，用一个不存在的账号连点 6 次即可，
  别拿 `admin` 试，那会把演示账号自己锁在门外。

`VITE_DEV_PERMISSIONS` 已移除。那种「用权限点伪造身份」的开关没有令牌，
任何真实接口都会 401，只会制造「明明配了权限却一直跳登录」的困惑。

**验证权限的最快方式**：用 `viewer` 登录，打开 `/system/user-access` 应看到 403 而不是白屏。

### 5.8 本功能的 mock

```ts
// src/modules/user-access/mock.ts
import type { MockRoute } from '@/mocks/kernel';
import { HttpError } from '@/mocks/kernel';

const users = [
  {
    id: 'u_001',
    username: 'admin',
    displayName: '老大',
    email: 'boss@example.com',
    avatarUrl: null,
    isActive: true,
    lastLoginAt: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    roles: [{ id: 'r_admin', code: 'admin', name: '超级管理员', isSuperuser: true }],
  },
];

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/identity\/users$/,
    handler: (ctx) => {
      const page = Math.max(1, Number(ctx.params.page ?? 1));
      const pageSize = Math.max(1, Number(ctx.params.pageSize ?? 20));
      return { items: users.slice((page - 1) * pageSize, page * pageSize), total: users.length, page, pageSize };
    },
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/users\/([\w-]+)\/roles$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const user = users.find((item) => item.id === id);
      if (!user) throw new HttpError(404, '用户不存在');
      return user;
    },
  },
];
```

约束：

- 只监听自己资源的路径（`/identity/users`、`/identity/roles`），不要写通配，也不要抢别人的前缀；
- 保持 REST 语义：分页、`201`、`404`、错误信息 —— mock 的职责是**提前暴露契约问题**，不是让页面好看；
- mock 文件随模块走，删功能时一起删，不留孤儿。

### 5.9 验收清单

- [ ] `npm run verify` 四关全绿
- [ ] 新增页面出现在导航，刷新 / 深链接直达正常
- [ ] 用 `viewer` 登录后：导航项消失、URL 直访 403（不是白屏 / 报错）
- [ ] 分页 / 筛选 / 空态 / 错误态都有对应 UI（不只是成功态）
- [ ] 写操作成功后列表与角标同步刷新（`invalidateQueries` 命中命名空间）
- [ ] mock 与真实后端返回结构一致（关掉 mock 不报类型错）
- [ ] 未在骨架文件里留改动（`git diff` 只包含新文件 + 本模块文件）
- [ ] 权限点与后端一致：身份模块见 `api/src/modules/identity/permissions.py`；
      新权限点应加在后端所属模块的 `ModuleSpec`，前端只消费、不硬编码
- [ ] 新增的权限点用低权账号试过，确实是 403（不是白屏 / 报错）

---

## 6. 多人并行协作规则

### 6.1 谁拥有什么

| 角色 | 拥有目录 | 约定 |
|---|---|---|
| 功能 A 开发 | `src/modules/a/**`、`src/pages/a/**`、`src/entities/a/**`、`src/features/a-*/**` | 只在自己的目录写 |
| 功能 B 开发 | 同上，换成 `b` | 同上 |
| 骨架维护者 | `src/app/**`、`src/widgets/**`、`src/shared/{registry,access,api,ui}` | 只做扩展点，不写业务 |

### 6.2 为什么不会冲突

- 路由表 / 导航 / 外壳**不再有“注册行”**，没有共享可变列表；
- 每个功能一个目录，文件名由模块 id 前缀限定；
- 注册表在**启动期**校验（重复 id、重复路由、导航指向空路由会直接抛错），冲突在本地就暴露，不会带到线上。

### 6.3 契约先行

多人并行的真实瓶颈不是代码冲突，是**接口没定**：

1. 后端先给 OpenAPI / 一张字段表；
2. 前端按契约写 `types.ts` + `mock.ts`，页面即可开工；
3. 后端联调时把 `VITE_USE_MOCK=false`，类型不变、页面不改；
4. 字段变更：先改 types → tsc 会把所有受影响处报出来（这是 TS 的最大价值，别用 `any` 绕过）。

### 6.4 命名空间约定（避免“缓存串味 / mock 抢路由”）

| 资源 | 约定 | 反例 |
|---|---|---|
| queryKey | 首段 = 资源名，`createQueryKeys('users')` | `['list']`（谁都能撞） |
| mock 路由 | 只匹配自己资源前缀 | `/^\/.*$/` |
| 权限点 | `资源:动作` | `isAdmin` 布尔 |
| 路由路径 | 功能前缀，如 `system/user-access` | 抢占 `/settings` 这种通用词 |

### 6.5 想改骨架怎么办

提 issue，写清三件事：场景、现有扩展点为什么不够、最小改动方案。
评审通过后**先加扩展点 + 测试**，再合业务。禁止在骨架里 `if (moduleId === 'xxx')`。

---

## 7. 反模式

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| 在 `module.ts` 里直接 `import XxxPage` | 页面被打进首屏 chunk，失去分包 | `load: () => import(...)` |
| 页面里 `import { http }` | 请求散落、无法统一错误处理 | 写进 `entities/*/api` |
| 两个模块共用 `['list']` 这种 key | 相互覆盖缓存 | `createQueryKeys` |
| 用 `isAdmin` 判断权限 | 角色一变到处改 | 权限点 `user:manage` |
| 在 `Sidebar` 里判断业务 | 骨架被业务污染 | `routes[].permissions` / `routes[].nav.badge` |
| 为了跑通把权限从清单里删掉 | 上线漏门禁 | 用 `viewer` 账号登录验证，别删声明 |
| mock 返回“方便页面”的结构 | 接真后端时全面返工 | 严格按契约 |
| `as any` 绕过类型 | 契约漂移无人发现 | 改 types 或让后端改字段 |

---

## 8. 本次骨架变更（迁移说明）

| 变更 | 影响 | 迁移 |
|---|---|---|
| 删除 `src/shared/config/navigation.ts` | 导航不再集中配置 | 已拆到各 `modules/<id>/module.ts` |
| `router.tsx` 由注册表生成 | 新增页面无需改路由 | 无需操作 |
| 新增 `src/modules/**` | 功能入口 | 新功能一律放这里 |
| 新增 `src/shared/registry/**` | 注册表内核 | 只读引用 |
| 新增 `src/shared/access/**` | 身份与权限内核 | 登录模块写 `setSubject` |
| `src/mocks` 拆成 `kernel / registry / routes` | mock 可扩展 | 业务 mock 放 `modules/<id>/mock.ts` |
| 新增 `scripts/gen-module.mjs` | 脚手架 | `npm run gen:module` |
| 侧栏角标读取 `routes[].nav.badge` | 骨架不再依赖 task 实体 | 已由 task-center 模块提供 |
| 路由支持 `layout: 'bare'` | 登录页这类整页能脱离外壳 | 新功能默认不写，保持 `'shell'` |
| 新增 `RequireAuth` + 会话三态 | 未登录自动回登录页、刷新不闪屏 | 无需操作 |
| `http.ts` 接 401 → `signOut()` | 令牌失效全站一致 | 无需操作 |
| 新增插槽 `user-menu.items` | 账号菜单项由模块注入 | 加菜单项写 `slots[]` |
| 新增 `src/modules/auth/**` | 登录页 + 账号菜单 | 只读参考，别改 |
| `nav[]` / `cards[]` 改为 `routes[].nav` / `routes[].card` | 路径与权限只写一次 | 模块清单按 §9 速查改写，产物不变 |

历史页面（`pages/*`）暂留在原地，通过清单注册；新页面建议同样放 `pages/<id>/`，与模块目录同名，便于对照。

---

## 9. 附：注册表 API 速查

```ts
// 清单：侧栏与宫格挂在路由上，不再是平级数组
defineModule({ id, title?, routes, slots? })

// routes[]
{
  path: string,
  load: () => Promise<{ default: ComponentType }>,
  permissions?: string[],              // 一处声明，nav / card 自动继承
  layout?: 'shell' | 'bare',
  nav?: {                             // 声明即生成侧栏入口
    label, icon, group, groupOrder?, order?, badge?: ComponentType
  },
  card?: {                            // 声明即生成首页宫格
    subtitle, gradient, title?, icon?, order?, matchPrefixes?
  },
}

// slots[]
{ name: SlotName, component: ComponentType, order?, permissions? }
```

`nav` / `card` 里**没有** `path` 与 `permissions`：注册表从所属 route 派生。
`card.title` 缺省取 `nav.label`（再兜底到模块 `title`），`card.icon` 缺省取 `nav.icon`；
两者都没写 `icon` 时注册期直接报错。`bare` 路由不允许声明 `nav` / `card`。

启动期校验（不满足直接抛错，错误信息含双方模块 id）：

- 模块 id 唯一
- 路由绝对路径唯一
- 导航必须指向已注册路由
- 路由路径不能为空（首页由 `shared/config/app.ts` 的 `HOME_PATH` 决定）
- 模块至少声明一条路由
- `layout` 只能是 `'shell'` / `'bare'`
- 导航项不能指向 `layout: 'bare'` 的页面（那种页面不套外壳，放侧栏会丢掉导航与登录态）
