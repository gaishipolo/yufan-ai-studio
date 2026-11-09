import type { TeamSummary } from '@/entities/asset/api/team.api';

/**
 * 「库」的模型：个人库 / 团队库 / 公共库。
 *
 * 库是**权限边界**，分类是**内容组织**，两者正交。所以它们在 URL 里也是两个参数
 * （`library` + `category`），而不是拼成一个 `scope=team_1_source`——拼起来之后
 * 每加一个库或一个分类都要改一次解析逻辑，而"哪个组合不合法"永远说不清。
 *
 * ## 旧参数怎么办
 *
 * 改造前是 `?scope=public|mine|references|packs` 加 `?type=source|product|...`。
 * 这些**全部保留为别名**（至少两个版本）：用户书签、外部链接、别人贴的工单里
 * 全是旧地址，切过去直接 404 是最容易被忽略的回归。映射集中在 `resolveLocation`，
 * 不散落到组件里。
 */

/** `personal` · `brand` · `team:<id>`。团队 id 带前缀是为了不和个人库的字面量撞名。 */
export type LibraryId = string;

export const LIBRARY_PERSONAL: LibraryId = 'personal';
export const LIBRARY_BRAND: LibraryId = 'brand';
export const TEAM_PREFIX = 'team:';

export function teamLibrary(teamId: string): LibraryId {
  return `${TEAM_PREFIX}${teamId}`;
}

export function teamIdOf(library: LibraryId): string {
  return library.startsWith(TEAM_PREFIX) ? library.slice(TEAM_PREFIX.length) : '';
}

export interface LibraryOption {
  id: LibraryId;
  label: string;
  kind: 'personal' | 'team' | 'brand';
  teamId: string;
  /** 我在这个团队里的角色（admin / member）；非团队库为空 */
  role: string;
  memberCount: number;
}

/**
 * 顶栏库切换器的选项。顺序是**个人 → 团队 → 公共**：
 * 个人库是每个人每天都用的，公共库是偶尔来取的；把公共放前面会让每次切回个人
 * 都多一次点击。
 */
export function libraryOptions(teams: TeamSummary[]): LibraryOption[] {
  return [
    {id: LIBRARY_PERSONAL, label: '我的素材', kind: 'personal', teamId: '', role: '', memberCount: 0},
    ...teams.map(team => ({
      id: teamLibrary(team.id),
      label: team.name,
      kind: 'team' as const,
      teamId: team.id,
      role: team.role,
      memberCount: team.memberCount,
    })),
    {id: LIBRARY_BRAND, label: '公共库', kind: 'brand', teamId: '', role: '', memberCount: 0},
  ];
}

/**
 * 二级「媒体类型」分面。它不是真实分类节点（C1），只是同一个分类下的一次过滤。
 *
 * 值与后端 `archive_service.MEDIA_TYPES` 同源（`image` / `video` / `audio`），
 * **不是**旧的 `source/character/product/scene/audio`——那套是用途，和类型混在
 * 一起后会让人把「原片」当成一种媒体格式。
 */
export const MEDIA_TYPES: ReadonlyArray<{key: string; label: string}> = [
  {key: '', label: '全部'},
  {key: 'image', label: '图片'},
  {key: 'video', label: '视频'},
  {key: 'audio', label: '音频'},
];

/** 页面级的"我在看什么"。`list` 是素材库主视图，其余两个是保留的旧入口（已迁到独立路由）。 */
export type WorkspaceView = 'list' | 'references' | 'packs';

export interface ResolvedLocation {
  view: WorkspaceView;
  library: LibraryId;
  /** 分类 id（新分类树上的位置）；空串表示「全部素材」 */
  categoryId: string;
  /** 回收站 / 历史素材这类"状态视图"，与库无关 */
  scope: 'mine' | 'public' | 'trash' | 'history';
  /** 公共库按业务类型分面时用（旧的 `?type=product`），保留原样 */
  publicType: string;
}

/**
 * 把 URL 参数解析成"我在看什么"。**新旧参数都在这里判定**，组件不再各判一次。
 *
 * 优先读新参数（`library`），读不到再按旧参数（`scope` + `type`）翻译。
 * 这样旧链接打开后 URL 仍是旧的（不会突然跳变），行为却已经是新的。
 */
export function resolveLocation(params: URLSearchParams): ResolvedLocation {
  const scope = params.get('scope') ?? '';
  const libraryParam = params.get('library') ?? '';
  const viewParam = params.get('view') ?? '';
  const publicType = params.get('type') ?? 'source';
  const categoryId = params.get('category') ?? '';

  // 旧入口先判：它们曾经是"整页换一套内容"的视图，现在各自有独立路由。
  // 这里只做标记，由 `AssetWorkspace` 决定跳转——解析函数不该产生副作用。
  if (scope === 'references') {
    return {view: 'references', library: LIBRARY_PERSONAL, categoryId: '', scope: 'mine', publicType};
  }
  if (scope === 'packs') {
    return {view: 'packs', library: LIBRARY_BRAND, categoryId: '', scope: 'public', publicType};
  }

  const library: LibraryId = libraryParam || (scope === 'public' ? LIBRARY_BRAND : LIBRARY_PERSONAL);

  if (viewParam === 'trash') {
    return {view: 'list', library, categoryId: '', scope: 'trash', publicType};
  }
  if (viewParam === 'history') {
    return {view: 'list', library, categoryId: '', scope: 'history', publicType};
  }

  return {
    view: 'list',
    library,
    categoryId,
    scope: library === LIBRARY_BRAND ? 'public' : 'mine',
    publicType,
  };
}

/**
 * 当前库该用哪个 `GET /assets` scope。
 *
 * 团队库是第四个域，**不是 mine 的子集**：团队库里的素材 owner 可能是别人，
 * 而 `mine` 永远只看自己的。所以这里必须显式返回 `team`，让服务端走
 * `visibility + team_id` 那条分支。
 */
export function listScopeOf(location: ResolvedLocation): 'mine' | 'public' | 'trash' | 'team' {
  if (location.scope === 'trash') return 'trash';
  if (location.library === LIBRARY_BRAND) return 'public';
  if (teamIdOf(location.library)) return 'team';
  return 'mine';
}

/** 顶栏与空状态里显示的库名。团队列表没加载出来时也不显示 id。 */
export function libraryLabel(library: LibraryId, teams: TeamSummary[]): string {
  if (library === LIBRARY_BRAND) return '公共库';
  const teamId = teamIdOf(library);
  if (!teamId) return '我的素材';
  return teams.find(team => team.id === teamId)?.name ?? '团队库';
}
