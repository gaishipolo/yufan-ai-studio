import type { ArchivedAsset, TagCatalog, TagGroup, TagOut, TeamList } from '@/shared/api/schema';
import { db } from './db';

/* ------------------------------------------------------------------
 * 素材库的分类树 / 标签词表 / 团队（mock 共享的推导层）
 *
 * 单独一个文件而不是塞进某个 `.mock.ts`：`/assets` 的筛选与
 * `/asset-categories` 的计数**必须用同一个推导函数**，否则 mock 里会出现
 * 「角标 8、点进去 3 条」——那正是真后端要防的错，mock 不该先犯一遍。
 *
 * 与后端 `library_service.PURPOSES` 同源的五个用途名。
 * ------------------------------------------------------------------ */

export const PURPOSES = ['参考素材', '产品图', '人物形象', '场景背景', '合成成片'] as const;
export const INBOX_NAME = '未分类收件箱';

/** 旧枚举 → 用途名。mock 的素材只有旧 `category`，这里做一次确定性映射。 */
const PURPOSE_BY_LEGACY: Record<string, string> = {
  source: '参考素材',
  product: '产品图',
  character: '人物形象',
  scene: '场景背景',
  audio: '合成成片',
};

/**
 * 一条素材在分类树上的位置。**确定性函数**，同一输入永远同一 id。
 *
 * 写成 `cat-<用途下标>` 而不是真 uuid：mock 的 id 只要在同一次会话里稳定即可，
 * 而可读的 id 让人在 debug 时一眼看出它属于哪个分类。
 */
export function categoryIdOf(asset: ArchivedAsset): string {
  const purpose = PURPOSE_BY_LEGACY[asset.category] ?? '参考素材';
  return `cat-${PURPOSES.indexOf(purpose as (typeof PURPOSES)[number])}`;
}

/** 收件箱 id：mock 里所有素材都已归类，所以它永远是 0 条——这本身也是一种状态。 */
export const INBOX_ID = 'cat-inbox';

export function categoryTree(library: string) {
  return [
    {id: INBOX_ID, name: INBOX_NAME, kind: 'system', isInbox: true, sortOrder: 0, library},
    ...PURPOSES.map((name, index) => ({
      id: `cat-${index}`,
      name,
      kind: 'system',
      isInbox: false,
      sortOrder: (index + 1) * 10,
      library,
    })),
  ];
}

/** 媒体类型分面值与后端 `archive_service.MEDIA_TYPES` 一致。 */
export function mediaTypeOf(asset: ArchivedAsset): string {
  return asset.type === 'video' || asset.type === 'audio' ? asset.type : 'image';
}

/* ---------------- 团队 ---------------- */

export const TEAMS: TeamList['items'] = [
  {id: 't_content', name: '内容团队', kind: 'content', role: 'admin', memberCount: 2},
  {id: 't_ops', name: '运营团队', kind: 'ops', role: 'member', memberCount: 2},
  {id: 't_tech', name: '技术团队', kind: 'tech', role: '', memberCount: 2},
];

/* ---------------- 标签词表：六维度 46 词 ---------------- */

const VOCABULARY: Array<[string, string, string[]]> = [
  ['style', '风格', ['写实摄影', '电影感', '广告质感', '3D 渲染', '插画手绘', '扁平矢量', '胶片颗粒', '极简留白', '国潮国风', '赛博霓虹']],
  ['scene', '场景', ['棚拍纯色', '室内家居', '办公空间', '城市街景', '自然户外', '餐厨空间', '工业车间', '抽象背景', '合成虚拟']],
  ['composition', '主体构成', ['无人', '单人', '多人', '手部特写', '宠物', '群体']],
  ['shot', '镜头', ['特写', '中景', '全景', '俯拍', '平视', '推拉', '环绕', '手持']],
  ['placement', '用途/版位', ['主图', '详情页', '封面', '开屏', '信息流', '贴片', '直播']],
  ['content', '内容属性', ['含真人', '含未成年人', '含品牌标识', '需换脸', '需打码', '无水印']],
];

/**
 * `useCount` 按确定性的方式给：前三个词给到 5 以上（进常显区），
 * 其余给 0–4。这样"＋更多"的折叠在 mock 环境里也能看出效果，
 * 而不是所有词都挤在常显区或全被折叠。
 */
export function tagCatalog(): TagCatalog {
  const groups: TagGroup[] = VOCABULARY.map(([dimension, label, words]) => ({
    dimension,
    label,
    items: words.map((name, index): TagOut => {
      const useCount = index < 3 ? 6 + index : index % 5;
      return {
        id: `${dimension}-${index}`,
        dimension,
        name,
        status: 'active',
        source: 'vocabulary',
        useCount,
        pinned: useCount >= 5,
      };
    }),
  }));
  return {groups, pinnedThreshold: 5};
}

/** 未使用的词仍然要在词表里：筛选条靠它显示"能选但还没人用"。 */
export const TAG_TOTAL = VOCABULARY.reduce((sum, [, , words]) => sum + words.length, 0);

/* ---------------- 可见域 ---------------- */

/**
 * 某个库里能看到的素材。**分类计数与列表筛选共用这一个函数**。
 *
 * `personal` 只认 `visibility=personal` 且属于演示账号；`team:<id>` 按团队取
 * （mock 里不区分成员，因为所有演示账号都在同一个 mock 会话里）；
 * `brand` 是公共池，只认已分享且未删除。
 *
 * mock 里没有 `visibility` 列——`ArchivedAsset` 的出参形状来自真后端，
 * 而 mock 的素材是手工构造的。用「不属于我 = 在团队库里」这条近似：
 * 演示账号自己的素材归个人库，其余归团队库。差异只影响演示观感，
 * 不影响任何前端逻辑分支。
 */
export function visibleInLibrary(library: string): ArchivedAsset[] {
  const live = (asset: ArchivedAsset) => asset.deletedAt == null;
  if (library === 'brand') return db.assets.filter(asset => asset.shared && live(asset));
  if (library.startsWith('team:')) {
    return db.assets.filter(asset => asset.ownerId !== 'u_001' && live(asset));
  }
  return db.assets.filter(asset => asset.ownerId === 'u_001' && live(asset));
}
