# src/modules —— 功能切片

**一个功能一个目录**，目录里放该功能的「模块清单」，页面/实体/接口各自归位。

```
src/modules/
  <id>/
    module.ts      ← 必需：路由 / 导航 / 宫格卡片 / 插槽 / 权限
    mock.ts        ← 可选：本功能专属 mock（自动发现）
    *.tsx          ← 可选：只属于这个功能的小组件（如导航角标）
```

## 规则

1. `module.ts` 必须 `export default defineModule({ ... })`，否则启动报错。
2. 页面只能通过 `load: () => import('@/pages/<id>/XxxPage')` 引入 —— 直接 import 会把页面打进首屏。
3. 模块之间**不要互相 import**（ESLint 会拦）；需要复用就下沉到 `features` / `entities` / `shared`。
4. 不要 import `@/app/*`：注册是 app 单向发现模块。
5. `nav[].group` 就是侧栏的**一级类目**：填同一个 `group` 的模块自动归到一组，
   分组天然可折叠（默认展开、状态记忆、进组自动展开都由骨架负责），**不需要额外声明**。
   排序用 `groupOrder`（支持小数插队）与 `order`，不要改别人模块的数字。

## 快速开始

```bash
npm run gen:module -- user-access "用户权限" "系统管理"
```

完整方法、权限接入、多人并行规则见 `docs/FRONTEND_EXTENSION.md`。
