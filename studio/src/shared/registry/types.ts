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
   * 不允许 `''` 或等价的 `'/'`：首页由 `src/shared/config/app.ts` 的 HOME_PATH 决定，
   * 避免多个模块抢根路由。尾部斜杠会被归一化剥掉，所以 `'tasks'` 与 `'tasks/'` 视为重复。
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
  /**
   * 侧栏入口的**展示信息**。声明后由注册表派生 `NavItemDef`，
   * `path` 与 `permissions` 一律继承本路由，不再重复写第三遍。
   */
  nav?: RouteNavDef;
  /**
   * 首页宫格卡片的**展示信息**。同样由注册表派生，
   * `path` / `permissions` 继承本路由，`title` / `icon` 缺省取 `nav` 的值。
   */
  card?: RouteCardDef;
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

/* ---------------- 模块声明用的「展示部分」----------------
 * 只写差异：`path` 与 `permissions` 由所属 route 继承，声明里不再出现。
 * 用 Omit 而不是再抄一遍字段，避免 NavItemDef / ModuleCardDef 加字段时这里悄悄漏掉。
 */

export type RouteNavDef = Omit<NavItemDef, 'path' | 'permissions'>;

export type RouteCardDef = Omit<ModuleCardDef, 'path' | 'permissions' | 'title' | 'icon'> & {
  /** 缺省取 `nav.label`；没有 nav 时取模块 title */
  title?: string;
  /** 缺省取 `nav.icon` */
  icon?: LucideIcon;
};

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
  /** 仅用于调试与错误信息；没写 nav / card 时也是卡片标题的兜底 */
  title?: string;
  /**
   * 路由是唯一的入口声明：侧栏与宫格卡片作为 `route.nav` / `route.card` 挂在路由上。
   * 曾经这里有平级的 `nav[]` / `cards[]`，同一个 path 和同一组 permissions 要写三遍，
   * 改权限漏一处就是「入口可见但 403」——现在由注册表派生，见 create-registry.ts。
   */
  routes: ModuleRouteDef[];
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
  /**
   * 注册序号（模块声明的先后），只用于渲染时的稳定 React key。
   * 不能用排序后的数组下标：`order` 一变下标就整体错位，
   * 带内部状态（如弹窗开关）的插槽会表现为「弹窗还开着，内容却换了」。
   */
  seq: number;
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
