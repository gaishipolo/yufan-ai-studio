# 用户模块升级 · 个人-小组-团队三层权限

> 本文只回答三件事：**按什么顺序做、动哪些文件、怎么判完成**。
> 需求与决策见 §0；代码现状调研结论见 §1；截图参考的前端形态见 §6。
> 2026-11-09 用户拍板「按默认」，D1–D8 全部按本文默认执行，开工不再等待确认。

## 0. 决策与纪律

### 0.1 已拍板（默认生效）

| # | 决策 | 结论 | 影响面 |
|---|---|---|---|
| D1 | 「个人权限永远 ≥ 小组/团队」 | **四层取并集、不设 deny** | `resolve_identity()` 是唯一合并点；无需优先级歧义 |
| D2 | 「管理员」的定义 | 按动作细分 8 个权限点：`identity:team:manage`、`identity:group:create/read/update/delete/member:manage/permission:manage`、`identity:user:permission:manage`（超管自然拥有）；授超管角色仍需超管 | 新接口的 `require()` 与提权守卫 |
| D3 | 团队基础权限默认值 | 现行 `viewer` 的 7 条只读，管理员可编辑；**禁止**把 `identity:*` / `*` 放进基础权限 | `TEAM_BASE_PERMISSIONS` 常量与写接口校验 |
| D4 | 组织形状 | 一人可属多组、权限取并集；**小组不嵌套** | `group_members` 无 parent 列 |
| D5 | 小组/个人层能否授管理权限 | 允许，但受 `_guard_permission_grant` 约束（不得授出自己没有的） | 所有写接口复用同一守卫 |
| D6 | 与现有角色体系的关系 | **保留**，三层与角色权限并集；不迁移存量角色绑定 | 上线后存量用户权限只增不减 |
| D7 | 前端形态 | 现有「用户与权限」页加分组侧栏与团队入口，**不新增路由** | `ROUTES.md` / 注册表不动 |
| D8 | 备份分支 `43ebbe5` 的 team/subject 成果 | **不取回**（语义冲突：主体=成组资产、三层=可见域、取交集） | 只借鉴 `migrate_asset_roles.py` 的 preview/apply 写法 |
| D9 | 新建用户时能否一并选分组（2026-11-09 追加） | **能**：`POST /identity/users` 接受 `groupIds`，建号与入组同一个事务；分组与角色在弹窗里都用**多选下拉** | 后端入参 + 事务 + 提权守卫；前端新增通用 `MultiSelect` |

**三条贯穿全程的纪律**

1. **只增不减**：三层新表与团队基础权限播种都不得让任何存量账号掉权限；新权限点不写进 `BUILTIN_ROLES`，否则普通角色自动获得组织管理能力。
2. **合并只在后端**：`CurrentUser` 与 `require()` 不动，前端 `can()` 仍只认权限码；分层来源仅作为展示数据下发。
3. **不写演示数据**：用户已明确「不要 seed 了，全部都不要了」。演示组织、成员、授权一律由测试 fixture 自建，不动 `identity/seed.py` 的 3 个账号与 4 个内置角色。

### 0.2 本期不做

- 不把 `scope=team` 接成真实数据范围（牵动 task / image / asset 三个模块，另立 M4）。
- 不做 deny / 个人降权、不做小组嵌套、不改资源 `owner_id` 归属。
- 不做「批量导入/导出」按钮：后端没有对应接口，页面上不放点不动的假入口。
- 不做手机号/职务的校验规则（只做可选展示字段，见 §6）。
- 不在**编辑用户**弹窗里改分组/角色：角色走「分配角色」（`identity:role:manage`），分组走小组编辑的「成员」页签；建号弹窗里的两个下拉只在创建模式出现。

### 0.3 待确认（记录，不阻塞开工）

| # | 问题 | 当前做法 | 若改变的影响 |
|---|---|---|---|
| Q1 | 截图里的「手机号 / 职务」是否要参与业务校验（唯一性、必填、导出） | 只作可选字符串展示 | 需要补唯一索引、导入接口、表单校验 |
| Q2 | 是否需要「批量导入/导出成员」 | 不做 | 需新增导入导出模块与模板 |
| Q3 | 团队基础权限是否要区分「未分组用户」与「已分组用户」 | 不区分，全员一致 | 需要按成员关系分流 |
| Q4 | 停用账号是否保留在小组里 | 保留（启用后权限原样恢复） | 需要停用即清绑定的级联逻辑 |

## 1. 代码现状（开工前核对）

- `main` 基线 `d182569`（`c5ee90b` + 一个备份修复）；身份模块 6 张表：`users` / `roles` / `role_permissions` / `user_roles` / `user_sessions` / `login_events`，**无组织实体**。
- 权限展开：`IdentityService.resolve_identity()`（`service.py:393`）取多角色并集，超管短路为 `*`；每请求经 `IdentityPrincipalResolver` 查库，改权限免重登。
- 提权守卫：`_guard_target_not_above` / `_guard_role_grant` / `_guard_permission_grant` / `_guard_has_superuser`（`service.py:1289-1430`）。
- 权限点目录：各模块 `ModuleSpec.permissions` → `core.permissions.catalog`；`app export-openapi` 一次生成 `api/docs/openapi.json`、`api/docs/API.md`、`studio/src/shared/api/schema.ts`、`studio/src/shared/api/permissions.ts`，`test_openapi_contract.py` 字节比对。
- Alembic head：`e4b7c2a91f38`；模型必须写在 `modules/*/models.py`（`db/registry.py` 只扫这个路径）。
- 前端：`pages/user-access/` 两个 Tab，`shared/access/evaluate.ts` 纯字符串判定，`session.ts::parseSubject()` 会丢弃未知字段。

## 2. 数据模型

新增三张表（不建外键，删组由 repository 显式清绑定）：

| 表 | 字段 | 约束 |
|---|---|---|
| `user_groups` | `id/name/description/created_at/updated_at` | `name` 唯一索引（`GROUPS` 在 PG 窗口帧语法里是关键字，避开） |
| `group_members` | `id/group_id/user_id/…` | `(group_id,user_id)` 唯一 + `(user_id,group_id)` 索引 |
| `permission_grants` | `id/scope/scope_id/permission/…` | `(scope,scope_id,permission)` 唯一 + `(scope,scope_id)` 索引 |

`scope` = `team` / `group` / `user`；`team` 的 `scope_id` 固定为常量 `__all__`（团队=所有启用账号，不建成员表）。
`users` 另加 `phone` / `position` 两个可选列（对齐截图表格列，M2 迁移）。

**有效权限** = `团队 ∪ 所有小组 ∪ 个人 ∪ 现有角色权限`；任一层出现 `*`，整体短路为 `*`。

## 3. 批次总览

```
M0 决策（本文） ──> M1 数据地基 ──> M2 后端接口 ──> M3 前端页面
                                        │
                                        └──> M4（另立）scope=team 真实数据范围
```

| 批次 | 交付物 | 依赖 | 可独立上线 | 风险 |
|---|---|---|---|---|
| M1 | 三表 + 迁移 + repository + 合并展开 + 团队基础权限播种 + 并集单测 | — | 是（无新接口，行为只对既有鉴权生效） | 基础权限给全员加读权限，需回归存量鉴权用例 |
| M2 | 8 个权限点、约 12 条接口、守卫、doctor 对账、四份生成物、越级测试 | M1 | 是 | 权限点漏登记会「超管能、别人 403」 |
| M3 | 分组侧栏、表格、弹窗、entities/group、mock、Subject 分层字段 | M2 | 是 | `parseSubject` 漏改会让刷新后分层信息丢失 |
| M4 | `scope=team` 成员范围（另立需求） | M1 | 是 | 牵动三个业务模块 |

## 4. 每批次都要过的闸门

| 闸门 | 命令 / 动作 | 不做的后果 |
|---|---|---|
| 后端单测 | `cd api && ./.venv/Scripts/python.exe -m pytest -q --basetemp=var/pytest-tmp` | 并集语义与提权守卫回归无人守 |
| 静态检查 | `ruff check`、`mypy --platform win32` | 类型漂移进主干 |
| 迁移双向 | `alembic upgrade head` → `downgrade -1` → `upgrade`、`alembic check` | 升级到一半卡死、生产无法回退 |
| 四份生成物 | `app export-openapi`（`--check` 只比对） | `test_openapi_contract.py` 红、前端类型与权限码漂移 |
| 启动对账 | `app doctor` + `permission_gaps == 0` | 拼错的权限码只有非超管 403 |
| 写路由策略 | `tests/test_route_policy.py`（全量 pytest 里） | 新增写接口漏登记限流/豁免，接口照常工作但没有任何上限 |
| 前端 | `cd studio && npm run verify`；`npm run gen:mock-coverage`（新增 mock 路由时） | typecheck / 契约 / 覆盖率文档红 |

## 5. 各批次文件清单与验收

### M1 · 数据地基

- 改：`api/src/modules/identity/models.py`（3 个新实体）、`permissions.py`（scope 常量 + `TEAM_BASE_PERMISSIONS`）、`repository.py`（`PermissionLayerRepository`）、`service.py`（`resolve_identity()` 合并、`bootstrap()` 播种）、`__init__.py`（bootstrap 调用链不变）。
- 加：`api/migrations/versions/a3f1c7d9e520_identity_permission_layers.py`（挂在 `e4b7c2a91f38` 之后，双方言、无外键）、`api/tests/test_identity_layers.py`。
- **验收**：存量 `pytest` 全绿；新用例覆盖并集、`*`、停用账号、离组即时生效、多组并集、团队播种幂等、迁移双向。

### M2 · 后端接口

- 改：`identity/permissions.py`（8 个新权限点，**不进 `BUILTIN_ROLES`**）、`dependencies.py`、`schema.py`、`router.py`、`service.py`、`repository.py`、`errors.py`（新错误码）、`models.py`（`phone`/`position`）、`api/docs/*` 与 `studio/src/shared/api/*`（生成物）。
- 加：`api/migrations/versions/b4e2d8f1a630_user_contact_fields.py`、接口测试。
- 接口：`GET/PUT /identity/team/permissions`；`GET/POST /identity/groups`、`GET/PATCH/DELETE /identity/groups/{id}`、`PUT .../members`、`PUT .../permissions`；`GET/PUT /identity/users/{id}/permissions`、`GET /identity/users/{id}/effective-permissions`；`/identity/users` 增加 `name` / `username` / `phone` / `groupId` 四路筛选。
- 分层明细**不进** `MeOut` / `Subject`：由 `GET /users/{id}/effective-permissions` 单独下发，判定仍只认 `effective`（纪律 2）。早期草案写过「`MeOut` 增加分层明细」，实现时按纪律 2 去掉了。
- 补遗（全量 pytest 才抓到）：7 条新增写路由必须登记 `api/ratelimit_policy.py::EXEMPT_WRITE_ROUTES`，口径与既有 identity 管理动作一致（低频 + 权限点 + 服务层守卫）。漏登记不会报错，只会静默没有上限。
- **验收**：`permission_gaps == 0`、四份生成物字节比对绿、越级授予 403、团队基础权限禁 `identity:*`、改权限免重登、`test_route_policy.py` 绿。

### M3 · 前端页面（截图参考）

- 改：`pages/user-access/UserAccessPage.tsx`（说明）、`UsersSection.tsx`（左栏 + 三路筛选 + 8 列表格 + 弹窗接线）、`RolesSection.tsx`（团队卡片）、`entities/user/model/types.ts`、`features/user-form/UserFormModal.tsx`（手机号/职务）、`modules/user-access/mock.ts`、`docs/MOCK_COVERAGE.md`。
- 加：`entities/group/*`（types / api / queries）、`pages/user-access/GroupPanel.tsx`、`UserTableFooter.tsx`、`features/group-editor/GroupEditorModal.tsx`（基本信息 / 成员 / 权限三页签）、`features/permission-layers/*`（通用勾选器、团队与个人授权弹窗、有效权限来源弹窗、团队卡片）。
- **不改 `Subject`**：`parseSubject()` / `toSubject()` 保持原样。分层来源只是弹窗展示数据，前端 `can()` 依旧只认权限码；把四层塞进 `Subject` 会诱导前端重算合并规则，漏一层就是越权。早期草案里的「Subject 分层字段」按纪律 2 撤销。
- **验收**：`npm run verify` 全绿（typecheck / lint / 433 用例 / build）；`docs/MOCK_COVERAGE.md` 重生成（端点疑似覆盖 53）；mock 模式下分组筛选、四层并集、授权整组替换可走通。

### M3.1 · 增量：建号直接选分组（2026-11-09）

需求：新建用户弹窗里能选已有分组，且**分组与角色都改成下拉列表**。

- 后端：`UserCreateIn` 加 `groupIds`；`create_user()` 去重 → `groups_by_ids()` 校验存在（缺 id 抛 `GroupNotFound` 404）→ 对每个目标组跑 `_guard_permission_grant`（入组等于把该组权限授予新账号，与建号挂角色同一条提权红线）→ `add_to_groups()` 与 `set_roles()` 在同一事务提交。**不传该字段的旧调用行为不变**，也不需要新迁移或新权限点。
- 生成物：`app export-openapi` 重生成四份；`entities/user/model/types.ts` 的 `CreateUserPayload` 改成直接派生 `UserCreateIn` —— 手抄版本遇到后端加字段不会报错，只会静默丢掉，症状正是「界面选了没生效」。
- 前端：新增通用 `shared/ui/MultiSelect.tsx`（收起一行、展开复选框列表、点外部 / Esc 关闭）；`UserFormModal` 用它替换 `RoleCheckboxList`，并排新增分组下拉；`UsersSection` 把 `useGroupList()` 的数据传进去。**保留多选**（一人可属多组、可挂多角色），没有为了「下拉」退化成单选；编辑模式仍不显示这两个字段（见 §0.2）。没有 `identity:group:read` 时下拉显示空态，不阻断建号。
- mock：`POST /identity/users` 处理 `groupIds`，同步写 `memberIds`，`effectiveOf()` 随即把组权限并进有效权限。
- **验收**：新增 3 条后端接口用例（入组并继承组权限 / 未知组 404 / 越级组 403）、2 条表单用例、1 条 mock 用例；四份生成物 `--check` 无差异；后端 `986 passed / 12 skipped` + ruff / mypy / `alembic check` / doctor 25 项 0 失败，前端 `npm run verify` 全绿（436 用例 + build）。8080 重启后 live `openapi.json` 的 `UserCreateIn` 已带上 `groupIds`（旧进程不会热加载，必须重启）。

## 6. 前端形态（参考截图）

截图是「左侧分组 + 顶部筛选 + 用户表格 + 分页」的单页后台：

- **左栏卡片「分组」**：标题旁「+ 新增分组」；首行「全部分组 (N)」；其余每行组名 + 编辑/删除图标；可滚动；点击某组即筛选右侧列表。
- **顶部**：三个搜索框（名字 / 账号 / 手机号）、角色下拉、账号状态下拉；右侧「添加成员」（截图的「批量导入/导出」本期不放，见 §0.2）。
- **表格列**：姓名、账号、手机号、角色、职务、创建时间、最后登录、操作（重置密码 / 编辑 / 停用 + 启用开关）。后端 `DELETE /users/{id}` 是停用，文案统一写「停用」，不写「删除」。
- **底部**：共 N 条、每页条数、页码、前往第 N 页。
- **授权入口**：分组编辑弹窗内分「基本信息 / 成员 / 权限」三块；用户行「有效权限」弹窗按 团队 / 小组 / 个人 / 角色 四段展示来源；团队基础权限放在「角色与权限」Tab 顶部卡片。
- **新建用户弹窗**（M3.1）：登录名 / 初始密码 / 昵称 / 邮箱 / 手机号+职务两列之后，「分组」「角色」并排两个多选下拉；收起时各占一行（未选分别是「不加入分组」「不分配角色」），展开是带第二行说明的复选框列表，角色额外带「超管 / 内置」标记。原来的角色复选框长列表被收进下拉，弹窗不再需要滚动才能看到底部按钮。
- 视觉沿用现有组件与浅色主题，不引入新设计系统。

**与截图的三处取舍（记录，免得被当成漏做）**

1. 不放「批量导入/导出」（§0.2：后端没有接口，页面上不摆点不动的假入口）；右侧主按钮是「添加成员」（`POST /identity/users`）。
2. 行内操作保留文字按钮（权限 / 编辑 / 角色 / 重置密码 / 停用|启用），没有换成截图里的图标 + 开关：这些按钮有按钮级 `<Can>` 门禁、可访问性名称与「不能停用自己」的禁用态，换成 `Switch` 会把这些断言与后端 409 的说明一起丢掉。
3. 底部分页写在页面专属 `UserTableFooter`，不改公共 `Pager`：后者的既定约定是「总条数由调用方放在工具栏」，为一张截图改公共组件会让别的列表页一起变样。越界回收逻辑照搬，避免「第 3 / 2 页 + 空列表」看起来像丢数据。

## 7. 回滚矩阵

| 批次 | 回滚动作 | 数据影响 |
|---|---|---|
| M1 | `alembic downgrade -1` 删三表 | 无存量数据改动（新表只增不减） |
| M2 | `alembic downgrade -1` 删 `phone/position` 两列；代码回退 | 已填的手机号/职务丢失（仅展示字段） |
| M3 | 前端回退到上一版 | 无数据影响 |
| M3.1 | 前端回退到复选框版本；后端 `UserCreateIn.groupIds` 可留着不删（默认空，旧调用无感） | 已通过该入口入组的账号仍留在组里，需按小组「成员」页签手工调整 |
| 全量 | 反向依次 downgrade；`app export-openapi` 重生成 | 角色与用户绑定从未被改写 |

## 8. 风险登记

| 风险 | 症状 | 对策 |
|---|---|---|
| 团队基础权限给全员加 7 条读权限 | 存量「无权限用户应 403」用例变红 | M1 跑全量回归；必要时把基础权限收窄为最小集并在本文记录 |
| 权限点漏登记 | 超管一切正常、别人 403 | `permission_gaps` 启动对账 + `test_permission_coverage.py` |
| 分层字段被 `parseSubject` 丢弃 | 刷新后组织信息消失 | M3 单测覆盖 `parseSubject` 往返 |
| 每请求多两次查询 | 首屏变慢 | 三层 grants 合并成一次 `IN` 查询；不引入缓存 |
| 删组留下孤儿 grant | 残留权限码无人可见 | repository 删组同时清 members + grants；doctor 对账 |
