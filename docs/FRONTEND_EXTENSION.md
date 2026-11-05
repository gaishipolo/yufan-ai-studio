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

---

## 2. 目录与依赖方向

```
app        装配层：Provider、路由生成、错误兜底
 │
modules    功能切片：一个功能一个目录，声明路由 / 导航 / 插槽 / 权限
 │
pages      页面组件（被 module 的 load() 懒加载）
 │
widgets    外壳与跨页组合块
 │
features   可复用的业务交互块（如「角色编辑器」）
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

---

## 3. 扩展点总表

| 想做的事 | 声明位置 | 骨架改动 |
|---|---|---|
| 新增页面 | `module.ts` → `routes[]` | 无 |
| 侧边栏入口 | `module.ts` → `nav[]` | 无 |
| 侧栏一级类目（可折叠） | `nav[].group` + `groupOrder` | 无 |
| 顶部宫格卡片 | `module.ts` → `cards[]` | 无 |
| 导航角标（如进行中任务数） | `nav[].badge`（组件） | 无 |
| 顶栏动作 / 侧栏底部 / 全局浮层 | `module.ts` → `slots[]` | 无 |
| 页面级权限 | `routes[].permissions` | 无 |
| 导航按权限隐藏 | `nav[].permissions` | 无 |
| 按钮级权限 | 页面里 `<Can permission="...">` | 无 |
| 新接口 | `entities/<id>/api/*.api.ts` | 无 |
| 接口 mock | `modules/<id>/mock.ts` | 无 |
| 新权限点 | 后端下发 + 清单里声明 | 无 |

可用插槽（`SlotName`）：`topbar.actions`、`module-nav.trailing`、`sidebar.footer`、`shell.overlay`。
新增**插槽内容**不用改骨架；新增**插槽种类**属于骨架变更。

### 3.1 侧栏分组 = 一级类目（自动可折叠）

`nav[].group` 就是侧栏的一级类目，**不需要额外声明是否可折叠**：

| 行为 | 由谁决定 |
|---|---|
| 是否可折叠、默认展开 | 骨架（`widgets/sidebar`）统一处理 |
| 折叠状态记忆 | `localStorage` 的 `sf_sidebar.collapsedGroups`，按分组标题存 |
| 分组顺序 | `groupOrder`（小的在前），同值按注册顺序 |
| 无可见项的分组 | 自动隐藏标题（避免留下点不动的折叠头） |
| 进入该分组的页面 | 自动展开（首屏加载不展开，尊重上次的折叠） |

加一个分组只需要写 `group: '系统管理'` + `groupOrder`。想插到两个已有分组之间就取中间值
（`1` 与 `2` 之间用 `1.5`）—— **不要为了排序去改别人模块的数字**，那正是并行冲突的来源。

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

### Step 6 · 声明权限

在 `module.ts` 打开 `permissions`，见 §5.4。**没声明 = 公开页面**。

### Step 7 · 验收

```bash
npm run verify     # tsc + eslint + vitest + build，四关全绿才算完
```

---

## 5. 实战：用户权限管理

以下按「前端只负责展示与提交，鉴权以服务端为准」的思路落地。

### 5.1 先和后端对齐契约（标准 RESTful）

> 好消息：后端 `api/src/modules/identity/` **已经实现**，共 17 个接口；
> 下面表格就是它的真实契约（前缀 `/api/v1/identity`），前端直接消费即可，不要另起一套。

| 用途 | 方法与路径 | 关键字段 |
|---|---|---|
| 登录 | `POST /identity/login` | `{ username, password }` → `{ accessToken, tokenType, expiresIn, user }` |
| 当前身份 | `GET /identity/me` | `UserOut` + `permissions: string[]` |
| 改自己资料 | `PATCH /identity/me` | `{ displayName?, email?, avatarUrl? }` |
| 改自己密码 | `POST /identity/me/password` | `{ oldPassword, newPassword }` |
| 用户列表 | `GET /identity/users?page&pageSize&keyword&isActive&roleId` | `Page<UserOut>` |
| 新建用户 | `POST /identity/users` | `{ username, password, displayName, email, roleIds }` → 201 |
| 用户详情 | `GET /identity/users/{id}` | `UserOut` |
| 编辑 / 停用 | `PATCH /identity/users/{id}` | `{ displayName?, email?, isActive? }` |
| 分配角色 | `PUT /identity/users/{id}/roles` | `{ roleIds: string[] }`（整组替换） |
| 重置密码 | `POST /identity/users/{id}/reset-password` | `{ password }` |
| 停用用户 | `DELETE /identity/users/{id}` | 保留历史归属，非物理删除 |
| 角色列表 | `GET /identity/roles` | 含 `permissions` / `userCount` / `orphanedPermissions` |
| 新建角色 | `POST /identity/roles` | `{ code, name, description, permissions }` → 201 |
| 编辑角色 | `PATCH /identity/roles/{id}` | `{ name?, description? }` |
| 改角色权限 | `PUT /identity/roles/{id}/permissions` | `{ permissions: string[] }`（整组替换） |
| 删角色 | `DELETE /identity/roles/{id}` | 内置角色 / 仍有成员时拒绝 |
| 权限目录 | `GET /identity/permissions` | `{ groups: [{ group, items: [{ code, name, description }] }], total }` |

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
src/modules/user-access/module.ts        清单：路由 + 导航 + 权限
src/modules/user-access/mock.ts          本功能 mock
src/entities/user/model/types.ts         User / UserListParams ...
src/entities/user/api/user.api.ts        userApi
src/entities/user/api/user.queries.ts    userKeys / useUserList / useSetUserRoles
src/entities/role/...                    同上
src/features/user-role-editor/           角色勾选弹窗（可被多处复用）
src/pages/user-access/UserAccessPage.tsx 页面组装
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
  resetPassword: (id: string, password: string) =>
    api.post<void>(`${base}/${id}/reset-password`, { password }),
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
      permissions: ['identity:read'],        // ① 路由守卫：手输 URL 也会 403
    },
  ],
  nav: [
    {
      label: '用户与权限',
      path: '/system/user-access',
      icon: ShieldCheck,
      group: '系统管理',
      groupOrder: 9,                         // 排在业务分组之后
      permissions: ['identity:read'],        // ② 导航：无权限自动隐藏
    },
  ],
});
```

生效链路（全部自动，无需改骨架）：

1. 侧边栏 / 宫格按 `permissions` 过滤；
2. 直接访问 URL → `<RequirePermission>` 渲染 403（`ForbiddenState`）；
3. 权限点来源 `Subject.permissions`，由登录模块写入 session。

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

### 5.6 登录模块怎么把身份接进来（不改骨架）

骨架只暴露一个写入点：`useSessionStore`。

```ts
// 登录成功后
import { api } from '@/shared/api/http';
import { useSessionStore } from '@/shared/access/session';

// 后端返回 { accessToken, tokenType, expiresIn, user }
const result = await api.post<{
  accessToken: string;
  expiresIn: number;
  user: Me;
}>('/identity/login', { username, password });

// http.ts 的请求拦截器读的就是这个 key，不要改名
localStorage.setItem('sf_token', result.accessToken);

useSessionStore.getState().setSubject({
  id: result.user.id,
  name: result.user.displayName || result.user.username,
  roles: result.user.roles.map((role) => role.code),
  permissions: result.user.permissions,   // 后端给超管的是 ['*']
});
```

退出 / 401：

```ts
useSessionStore.getState().signOut();
```

> **现状（重要）**：后端 `/identity/login`、`/identity/me` 已就绪，前端**还没有登录页**，
> 所以 `useSessionStore` 目前只可能是 `GUEST_SUBJECT`（无权限），或 `VITE_DEV_PERMISSIONS` 造出的 dev 身份。
>
> 两个坑：
> 1. 开发环境后端默认 `allowAnonymous`，匿名请求会被当成 `u_001`（`permissions: ['*']`）——
>    **这只影响后端鉴权，不会让前端门禁自动放行**。给页面加了 `permissions` 后前端仍会 403，
>    必须配 `VITE_DEV_PERMISSIONS`，或者先把登录做出来。
> 2. `shared/api/http.ts` 的响应拦截器还没接 401 → `signOut()` + 跳登录。
>    这属于登录模块的归属改动（允许改这一处），但不要顺手往里加业务逻辑。
>
> 因此**下一刀建议先做「登录 + 会话引导」**：`POST /identity/login` → 存 token → `setSubject`。
> 做完之后，导航过滤 / 路由守卫 / `<Can>` 全线生效，仍然不需要动骨架。

权限点写法支持三种（`shared/access/evaluate.ts`）：

```
task:read        精确
task:*           该资源全部动作
*                全量（后端给超管）
```

### 5.7 本地开发没有权限后台怎么办

`.env.development`：

```bash
# 逗号分隔，仅 DEV 构建生效（生产构建会被摇掉）
VITE_DEV_PERMISSIONS=identity:read,identity:update,identity:role:manage
```

刷新即拥有这些权限点，可正常调试受保护页面。**不要**为了调试把 `permissions` 从清单里删掉。

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
- [ ] 清空 `VITE_DEV_PERMISSIONS` 后：导航消失、URL 直访 403
- [ ] 分页 / 筛选 / 空态 / 错误态都有对应 UI（不只是成功态）
- [ ] 写操作成功后列表与角标同步刷新（`invalidateQueries` 命中命名空间）
- [ ] mock 与真实后端返回结构一致（关掉 mock 不报类型错）
- [ ] 未在骨架文件里留改动（`git diff` 只包含新文件 + 本模块文件）
- [ ] 权限点与后端一致：身份模块见 `api/src/modules/identity/permissions.py`；
      新权限点应加在后端所属模块的 `ModuleSpec`，前端只消费、不硬编码
- [ ] 新增的权限点在 `VITE_DEV_PERMISSIONS` 里试过，且清空后确实是 403（不是白屏 / 报错）

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
| 在 `Sidebar` 里判断业务 | 骨架被业务污染 | `nav[].permissions` / `nav[].badge` |
| 为了跑通把权限从清单里删掉 | 上线漏门禁 | `VITE_DEV_PERMISSIONS` |
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
| 侧栏角标读取 `nav[].badge` | 骨架不再依赖 task 实体 | 已由 task-center 模块提供 |

历史页面（`pages/*`）暂留在原地，通过清单注册；新页面建议同样放 `pages/<id>/`，与模块目录同名，便于对照。

---

## 9. 附：注册表 API 速查

```ts
// 清单
defineModule({ id, title?, routes, nav?, cards?, slots? })

// routes[]
{ path: string, load: () => Promise<{ default: ComponentType }>, permissions?: string[] }

// nav[]
{ label, path, icon, group, groupOrder?, order?, permissions?, badge?: ComponentType }

// cards[]
{ title, subtitle, path, icon, gradient, order?, matchPrefixes?, permissions? }

// slots[]
{ name: SlotName, component: ComponentType, order?, permissions? }
```

启动期校验（不满足直接抛错，错误信息含双方模块 id）：

- 模块 id 唯一
- 路由绝对路径唯一
- 导航必须指向已注册路由
- 路由路径不能为空（首页由 `shared/config/app.ts` 的 `HOME_PATH` 决定）
- 模块至少声明一条路由
