import type {
  LibraryAssetKind,
  LibraryAssetOut,
  LibraryAssetState,
  LibraryTab,
} from '@/shared/api/schema';

/**
 * 资产库 v2 的视图模型。
 *
 * 六个一级入口分两类：`all` / `personal` / `group` / `team` 是 `state=active`
 * 下的可见范围（`library`），`drafts` / `trash` 是两个状态。后端把两者拆成正交
 * 参数，界面上不做解释，只把草稿箱与回收站放在末尾。转换集中在 `viewQuery`，
 * 页面里不许自己拼 `state`，否则「草稿箱里出现小组资产」这类错会散落各处。
 *
 * `all` 不是「三份列表拼起来」，而是后端的全量可见集：本人未共享的 +
 * 共享到我所在小组的 + 团队的，同一个素材只出现一次；装进可见封装的成员
 * 也不单列。所以角标 `all` 可能小于三个入口角标之和（跨入口的封装成员会
 * 在各自的入口里出现一次），这不是漏数据。
 */
export type LibraryView = 'all' | 'personal' | 'group' | 'team' | 'drafts' | 'trash';

export type KindFilter = 'all' | LibraryAssetKind;

export type LibrarySortKey = '-createdAt' | 'createdAt' | 'name' | '-sizeBytes';

export const VIEWS: ReadonlyArray<{
  value: LibraryView;
  label: string;
  hint: string;
  /** 分面字段：徽章数字的来源 */
  facet: 'all' | 'personal' | 'group' | 'team' | 'drafts' | 'trash';
}> = [
  {
    value: 'all',
    label: '全部素材',
    hint: '当前账号能见的个人、小组、团队素材，同一素材不重复',
    facet: 'all',
  },
  {
    value: 'personal',
    label: '个人',
    hint: '自己上传与已采纳的素材，只有本人可见',
    facet: 'personal',
  },
  {
    value: 'group',
    label: '小组',
    hint: '共享给小组的素材，组内可见、负责人审核',
    facet: 'group',
  },
  { value: 'team', label: '团队', hint: '全员可见，任何人不含上传者都不可直接删除', facet: 'team' },
  {
    value: 'drafts',
    label: '草稿箱',
    hint: 'AI 生成结果先落这里，确认后才进个人库',
    facet: 'drafts',
  },
  {
    value: 'trash',
    label: '回收站',
    hint: '保留 30 天，被引用的条目不自动清理',
    facet: 'trash',
  },
];

const VIEW_VALUES = new Set<string>(VIEWS.map((item) => item.value));

/** URL 里的 `view` 不可信：刷新、手改、旧链接都要落回第一个入口 */
export function parseView(raw: string | null): LibraryView {
  return raw && VIEW_VALUES.has(raw) ? (raw as LibraryView) : 'all';
}

export function viewMeta(view: LibraryView) {
  return VIEWS.find((item) => item.value === view) ?? VIEWS[0];
}

/**
 * 视图 → 列表查询参数。
 *
 * `drafts` / `trash` 不走可见范围：它们各自被权限点限定（`draft:review` /
 * `delete:any`），传 `library` 只会让后端多算一遍却不用它。其余四个视图的
 * 字面量与后端 `LibraryTab` 一一对应，`all` 显式传给列表和标签栏，保证
 * 「标签写 N，点进去就是 N 条」用的是同一套口径。
 */
export function viewQuery(view: LibraryView): {
  state: LibraryAssetState;
  library?: LibraryTab;
} {
  if (view === 'drafts') return { state: 'draft' };
  if (view === 'trash') return { state: 'trash' };
  return { state: 'active', library: view };
}

/**
 * 这个视图里能不能出现「上传 / 封装」主按钮。
 *
 * 上传写的是**个人库**、封装选择器的可见集一直是全量，两者都与「当前在看哪
 * 个入口」无关；默认 landing 在「全部素材」，按钮藏起来只会让人以为不能上传。
 * 草稿箱来自生成入库、回收站里的东西不该再被建封装，这两个仍不显示。
 */
export function viewAllowsCreate(view: LibraryView): boolean {
  return view === 'all' || view === 'personal';
}

/**
 * 二级类型筛选。
 *
 * **刻意不含「封装」**：封装是容器，不是第四种素材。把它摆在这一排，会让人
 * 以为它是和「成片 / 图片 / 音频」并列的类型；而且封装创建后自动带上「封装」
 * 标签，按标签筛已经能找全，多一个入口只会两处口径。
 */
export const KIND_OPTIONS: ReadonlyArray<{ value: KindFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'video', label: '成片' },
  { value: 'image', label: '图片' },
  { value: 'audio', label: '音频' },
  { value: 'text', label: '文本' },
];

export const SORT_OPTIONS: ReadonlyArray<{ value: LibrarySortKey; label: string }> = [
  { value: '-createdAt', label: '最新创建' },
  { value: 'createdAt', label: '最早创建' },
  { value: 'name', label: '名称' },
  { value: '-sizeBytes', label: '体积从大到小' },
];

export const KIND_LABEL: Record<LibraryAssetKind, string> = {
  video: '成片',
  audio: '音频',
  image: '图片',
  pack: '封装',
  text: '文本',
};

export const ORIGIN_LABEL: Record<string, string> = {
  upload: '上传',
  generated: 'AI 生成',
  template_source: '模板原片',
  imported: '导入',
  pack: '封装',
};

export const STATE_LABEL: Record<LibraryAssetState, string> = {
  draft: '草稿',
  active: '在库',
  trash: '回收站',
};

export const VISIBILITY_LABEL: Record<string, string> = {
  personal: '个人',
  group: '小组',
  team: '团队',
};

/**
 * 展示用的名称：去掉格式后缀。
 *
 * 后缀是**文件自己的属性**，用户改不了（后端 `keep_extension` 会把后缀拼回
 * 记录原有的那个，见 `api/tests/test_library_naming.py`）。界面上再把 `.png`
 * 摆在名字里，只会让人以为那一段可以编辑——所以卡片、详情标题、选择器统一
 * 用这个函数，只改主体。
 *
 * 只剥「像扩展名」的尾段：带字母、1-5 位。`v1.2`、`主图-2.0` 里的点不是后缀，
 * 剥掉会把名字改残；`.png` 这种「整个名字就是后缀」的，剥完为空，退回原样。
 */
const SUFFIX_SHAPE = /\.[A-Za-z][A-Za-z0-9]{0,4}$/;

export function displayName(name: string): string {
  return name.replace(SUFFIX_SHAPE, '') || name;
}

/**
 * 删除按钮该给什么。
 *
 * 前端只负责「少看到不该点的」，真正的判定在后端（§3.4）：这里给出的
 * `revoke` 也只是「去提交撤销申请」，不是「删掉」。管理员多出来的 `force`
 * 仍需填理由，且后端会补一张自审撤销单。
 */
export type DeleteOffer = 'trash' | 'revoke' | 'force' | 'none';

export function deleteOffer(
  asset: Pick<LibraryAssetOut, 'ownerId' | 'visibility' | 'state'>,
  viewer: { id: string; canDeleteAny: boolean },
): DeleteOffer {
  if (asset.state !== 'active') return 'none';
  if (asset.ownerId === viewer.id) {
    return asset.visibility === 'personal' ? 'trash' : 'revoke';
  }
  return viewer.canDeleteAny ? 'force' : 'none';
}

/** 回收站剩余天数：已过期返回 0，没有截止时间返回 null（不显示徽章） */
export function trashDaysLeft(expiresAt?: string | null, now = Date.now()): number | null {
  if (!expiresAt) return null;
  const diff = new Date(expiresAt).getTime() - now;
  if (Number.isNaN(diff)) return null;
  return Math.max(0, Math.ceil(diff / 86_400_000));
}

/** 过期条目可以看、可以恢复吗：后端恢复接口不看过期，界面只把它标灰 */
export function isTrashExpired(expiresAt?: string | null, now = Date.now()): boolean {
  return trashDaysLeft(expiresAt, now) === 0;
}

/**
 * 「原片」在资产库里的两种来源，**必须一起取**：
 *
 * - `template_source`：模板库切换后新传的原片（`POST /library/source-videos`）；
 * - `imported`：并存期从旧素材库导入的存量原片。
 *
 * 漏掉后者等于切完只剩新传的片子，用户会以为原来的原片被删了；只按
 * `kind=video` 取又会把已生成的成片（同为 `kind=video`）列成原片。
 */
export const SOURCE_VIDEO_ORIGINS: string[] = ['template_source', 'imported'];
