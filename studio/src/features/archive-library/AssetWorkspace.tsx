import { Navigate, useSearchParams } from 'react-router-dom';
import { useCan } from '@/shared/access/use-access';
import { Button } from '@/shared/ui';
import { useTeams } from '@/entities/asset/api/team.api';
import { CandidateBanner } from './CandidateBanner';
import { SourceLibrary } from './SourceLibrary';
import { ArchivedResults } from './ArchivedResults';
import { CategoryBar } from './CategoryBar';
import { LibrarySwitcher } from './LibrarySwitcher';
import { TagFilterBar } from './TagFilterBar';
import { LIBRARY_BRAND, libraryLabel, listScopeOf, resolveLocation, teamIdOf } from './model/library';

/**
 * 素材库管理页：**纵向六段式**（设计文档 §7.2 + M3-D）。
 *
 * ```
 * ① 顶栏：库切换 + 库名 + 成员数
 * ② 待确认的生成结果（为 0 时整段消失）
 * ③ 横向分类条（一级分类 + 二级媒体类型分面）
 * ④ 筛选条（关键词 + 标签 + 已保存视图）
 * ⑤ 吸顶控制条（数量 / 排序 / 网格|列表）  ← 在 SourceLibrary 里，见下
 * ⑥ 内容区
 * ```
 *
 * **顺序不是排版偏好**：先定范围（库）→ 再清待办（候选）→ 再定位置（分类）
 * → 再定条件（筛选）→ 再定呈现（控制条）→ 最后是内容。每一步都在缩小集合，
 * 用户的视线自上而下收敛，不会出现「筛完了才发现库选错了」的回头路。
 *
 * 候选插在分类条**之前**：它不属于任何分类，放在分类下面会让人以为
 * "切个分类就能把它筛出来"。
 *
 * ⑤⑥ 留在 `SourceLibrary` 内部：`total` 与 `isFetching` 都来自那一个查询，
 * 提到外层只为把它们当参数再传回去，等于把一段内聚的东西切开。
 *
 * ## 旧入口
 *
 * `?scope=references` / `?scope=packs` 曾经是同一页里的两个 tab，现在各自有
 * 独立路由（`/assets/references`、`/assets/packs`）。这里**重定向**而不是继续
 * 内联渲染：书签、工单里的旧链接都还能用，但页面结构只有一套。
 */
export function AssetWorkspace() {
  const [params, setParams] = useSearchParams();
  const location = resolveLocation(params);
  const {data: teamData} = useTeams();
  const teams = teamData?.items ?? [];
  const referenceManager = useCan('asset:reference:manage');
  const packManager = useCan('template:pack:manage');

  if (location.view === 'references') return <Navigate to="/assets/references" replace />;
  if (location.view === 'packs') return <Navigate to="/assets/packs" replace />;

  const scope = listScopeOf(location);
  const teamId = teamIdOf(location.library);
  const tagIds = (params.get('tag') ?? '').split(',').filter(Boolean);

  /** 换库要清掉分类与标签：它们在旧库里有意义，在新库里是另一套 id。 */
  const setLibrary = (next: string) =>
    setParams(previous => {
      const updated = new URLSearchParams(previous);
      updated.set('library', next);
      updated.delete('category');
      updated.delete('tag');
      updated.delete('page');
      updated.delete('q');
      return updated;
    });
  const setParam = (key: string, value: string) =>
    setParams(previous => {
      const updated = new URLSearchParams(previous);
      if (value) updated.set(key, value);
      else updated.delete(key);
      // 任何筛选条件变化都回到第 1 页：留在第 4 页会看到一个空列表，
      // 而"筛选后没结果"与"这一页没内容"是两件事
      if (key !== 'page') updated.set('page', '1');
      return updated;
    });
  const setTags = (next: string[]) => setParam('tag', next.join(','));

  return (
    <div className="space-y-4">
      {/* ① 顶栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <LibrarySwitcher value={location.library} onChange={setLibrary} />
        <span className="text-sm font-semibold text-ink">
          {libraryLabel(location.library, teams)}
        </span>
        {location.scope === 'mine' && (
          <span className="text-xs text-ink-mute">
            {libraryLabel(location.library, teams)} · 上传的素材默认只在这里可见
          </span>
        )}
        {/* 回收站与历史素材是**个人库**的状态视图：团队库没有"回收站"
            这个概念（成员删掉的东西由团队管理员统一处理，见 M5）。 */}
        {location.library === 'personal' && (
          <div className="flex gap-1.5">
            <Button
              size="sm"
              variant={!params.get('view') ? 'primary' : 'secondary'}
              aria-pressed={!params.get('view')}
              onClick={() => setParam('view', '')}
            >
              在用
            </Button>
            <Button
              size="sm"
              variant={params.get('view') === 'trash' ? 'primary' : 'secondary'}
              aria-pressed={params.get('view') === 'trash'}
              onClick={() => setParam('view', 'trash')}
            >
              回收站
            </Button>
            <Button
              size="sm"
              variant={params.get('view') === 'history' ? 'primary' : 'secondary'}
              aria-pressed={params.get('view') === 'history'}
              onClick={() => setParam('view', 'history')}
            >
              历史素材
            </Button>
          </div>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {/* 主体是"从素材库进去看的东西"：入口放在这里而不是侧栏，
              因为它是装配单位，不是并列的一级产品 */}
          <a className="text-xs text-ink-soft underline" href="/assets/subjects">
            主体
          </a>
          {referenceManager && (
            <a className="text-xs text-ink-soft underline" href="/assets/references">
              参考素材维护
            </a>
          )}
          {packManager && (
            <a className="text-xs text-ink-soft underline" href="/assets/packs">
              封装与发布
            </a>
          )}
        </div>
      </div>

      {/* ② 待确认的生成结果：**在分类条之上**，因为它不属于任何分类。
          候选是"待办"——它不进分类树、不进列表、不计配额，只在这里出现。
          为 0 时组件自己返回 null，整段消失（空待办只是噪音）。 */}
      <CandidateBanner />

      {/* ③ 分类条。库切换后它整棵换掉——分类树属于库，不跨库共享 */}
      <CategoryBar
        library={location.library}
        categoryId={location.categoryId}
        mediaType={params.get('mediaType') ?? ''}
        onCategory={next => setParam('category', next)}
        onMediaType={next => setParam('mediaType', next)}
      />

      {/* ④ 筛选条 */}
      <TagFilterBar
        selected={tagIds}
        onChange={setTags}
        savedQuery={{
          library: location.library,
          category: location.categoryId,
          mediaType: params.get('mediaType') ?? '',
          q: params.get('q') ?? '',
          tagIds,
        }}
      />

      {/* ⑤⑥ */}
      <SourceLibrary
        scope={scope}
        teamId={teamId}
        categoryId={location.categoryId}
        mediaType={params.get('mediaType') ?? ''}
        tagIds={tagIds}
        toolbar="control"
        emptyHint={
          location.library === LIBRARY_BRAND
            ? '公共库还没有素材：等专员发布，或把你自己的原片分享出来。'
            : teamId
              ? '这个团队库还没有素材：队友上传后才会出现在这里。'
              : undefined
        }
      />

      <ArchivedResults />
    </div>
  );
}
