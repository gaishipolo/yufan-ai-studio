import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';

/* ------------------------------------------------------------------
 * 模块注册表的公共类型 —— 骨架对外的唯一扩展契约
 *
 * 约定：业务只「声明」，骨架负责「装配」。
 * 新增功能 = 新增一个 src/modules/<id>/module.ts，不碰 app / widgets。
 * ------------------------------------------------------------------ */

/**
 * 权限标识，统一写成 `资源:动作`：
 * - `task:read` / `task:delete` / `user:manage`
 * - `task:*` 表示该资源全部动作
 * - `*` 表示全量（通常由后端下发给超管）
 */
export type Permission = string;

export interface ModuleRouteDef {
  /**
   * 相对模块根的路由路径：`'tasks'` → `/tasks`。
   * 不允许 `''`：首页由 `src/app/config.ts` 的 HOME_PATH 决定，避免多个模块抢根路由。
   */
  path: string;
  /**
   * 页面加载器。必须是 `() => import('...')` 形式，
   * 骨架内部用 React.lazy 包装，保证「一个页面一个 chunk」不被破坏。
   */
  load: () => Promise<{ default: ComponentType }>;
  /**
   * 进入该页面所需的权限（全部满足）；不声明 = 公开页面
   * （注意：公开 ≠ 免登录，登录与否由 layout 与骨架守卫决定）
   */
  permissions?: Permission[];
  /**
   * 页面外壳：
   * - `'shell'`（默认）套 AppShell（顶栏 / 导航 / 侧栏），并受登录守卫保护
   * - `'bare'`  独立整页，不套外壳、不做登录守卫（登录页、注册页、外部回调页）
   *
   * 只有确实需要「脱离应用框架」的页面才用 bare；业务页面一律保持默认。
   */
  layout?: 'shell' | 'bare';
}

export interface NavItemDef {
  label: string;
  /** 必须与某个 ModuleRouteDef 解析后的绝对路径一致，注册表会校验 */
  path: string;
  icon: LucideIcon;
  /** 分组标题，如「创作」「资产与数据」 */
  group: string;
  /** 分组排序（小的在前），同值按注册顺序 */
  groupOrder?: number;
  /** 组内排序（小的在前） */
  order?: number;
  /** 无权限时导航项自动隐藏 */
  permissions?: Permission[];
  /**
   * 角标组件（如需「进行中任务数」）。
   * 组件自己取数，骨架不关心数据来源，因此新增角标不需要改 Sidebar。
   */
  badge?: ComponentType;
}

export interface ModuleCardDef {
  title: string;
  subtitle: string;
  path: string;
  icon: LucideIcon;
  /** 渐变底色，如 'linear-gradient(140deg,#5b8cff,#7c5cff)' */
  gradient: string;
  order?: number;
  /** 高亮匹配的前缀，缺省为 [path] */
  matchPrefixes?: string[];
  permissions?: Permission[];
}

/** 骨架暴露的 UI 插槽。新增插槽属于骨架变更，需评审；新增「插槽内容」不用 */
export type SlotName =
  | 'topbar.actions'
  | 'module-nav.trailing'
  | 'sidebar.footer'
  | 'shell.overlay'
  /** 顶栏头像下拉里的菜单项（「个人资料」「修改密码」等由各自模块贡献） */
  | 'user-menu.items';

export interface SlotContribution {
  name: SlotName;
  component: ComponentType;
  order?: number;
  /** 无权限时不渲染 */
  permissions?: Permission[];
}

/**
 * 一个功能模块的完整声明。
 * 用 `defineModule()` 包裹以获得类型推断与拼写检查。
 */
export interface AppModule {
  /** 全局唯一，kebab-case，通常与目录名一致 */
  id: string;
  /** 仅用于调试与错误信息 */
  title?: string;
  routes: ModuleRouteDef[];
  nav?: NavItemDef[];
  cards?: ModuleCardDef[];
  slots?: SlotContribution[];
}

/* ---------------- 注册表产物（骨架内部消费） ---------------- */

export interface ResolvedRoute {
  /** 绝对路径，如 `/tasks` */
  path: string;
  Component: ComponentType;
  permissions: Permission[];
  layout: 'shell' | 'bare';
  moduleId: string;
}

export interface ResolvedNavItem extends NavItemDef {
  moduleId: string;
}

export interface NavGroup {
  title: string;
  order: number;
  items: ResolvedNavItem[];
}

export interface ResolvedCard extends ModuleCardDef {
  moduleId: string;
}

export interface ResolvedSlot {
  name: SlotName;
  component: ComponentType;
  order: number;
  permissions: Permission[];
  moduleId: string;
}

export interface AppRegistry {
  modules: AppModule[];
  routes: ResolvedRoute[];
  navGroups: NavGroup[];
  cards: ResolvedCard[];
  slots: Partial<Record<SlotName, ResolvedSlot[]>>;
  /** 所有被声明的权限，便于调试与文档生成 */
  declaredPermissions: Permission[];
}
