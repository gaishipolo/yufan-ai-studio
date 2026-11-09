import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import type { ArchivedAsset } from '@/shared/api/schema';
import { Can } from '@/shared/access/Can';
import { Button, EmptyState } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { ArchiveCard } from './ArchiveCard';
import { ArchiveDetail } from './ArchiveDetail';
import { AssetActions } from './AssetActions';
import { ArchiveUploadPanel } from './ArchiveUploadPanel';
import { StickyControls } from './StickyControls';
import type { Density } from './model/controls';
import { useUploadBatchStore } from './model/use-upload-batch';
import { isBatchLocked } from './model/upload-batch';
import type { ArchiveCategory } from '@/entities/asset/model/archive.types';

/**
 * 第④⑤段：控制条 + 内容区。
 *
 * 为什么控制条放在这里而不是外层壳：`total` 与 `isFetching` 都来自这个查询，
 * 往上提要给整个页面加一层 props 透传，而它们只有一个使用点。段的位置由
 * 外层壳的排列决定，段的内部不需要为此拆开。
 *
 * **`density` 只影响渲染**：两种密度共用同一个 `useArchiveAssets` 的结果。
 * 两套查询是这个仓付过一次代价的地方——同一能力做成两套，问题永远只在其中
 * 一套里被修。
 */
export function SourceLibrary({
  scope,
  teamId = '',
  categoryId = '',
  mediaType = '',
  tagIds = [],
  density = 'full',
  toolbar = 'full',
  emptyHint,
}: {
  scope: 'mine' | 'public' | 'trash' | 'history' | 'team';
  /** `scope=team` 时必填；服务端拿它做可见性判定，缺了是 400 */
  teamId?: string;
  /** 分类树上的位置；空串表示全部素材 */
  categoryId?: string;
  /** 二级媒体类型分面；空串表示不限 */
  mediaType?: string;
  /** 受控标签 id，**AND 语义** */
  tagIds?: string[];
  density?: Density;
  /**
   * `full` = 自带搜索/上传/计数（独立使用时的全套）；
   * `control` = 只要第④段控制条（外层已有 ③ 筛选条）；
   * `none` = 只渲染内容区。
   */
  toolbar?: 'full' | 'control' | 'none';
  /** 空列表时的一句话，按库不同（团队库空了和我的库空了不是同一件事） */
  emptyHint?: string;
}) {
  const [params, setParams] = useSearchParams();
  const keyword = params.get('q') ?? '';
  const debouncedKeyword = useDebouncedValue(keyword);
  const state =
    params.get('state') === 'shared' ? 'shared' : params.get('state') === 'private' ? 'private' : 'all';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const sort = params.get('sort') ?? 'created_desc';
  const query = useArchiveAssets('all', {
    scope: scope === 'history' ? 'mine' : scope,
    keyword: debouncedKeyword,
    state,
    page,
    pageSize: 24,
    tagIds,
    categoryId,
    mediaType,
    sort,
    ...(teamId ? {teamId} : {}),
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = query.data?.items.find(item => item.id === selectedId);
  const input = useRef<HTMLInputElement>(null);
  const start = useUploadBatchStore(s => s.start);
  const batch = useUploadBatchStore(s => s.batch);
  const locked = isBatchLocked(batch);
  const update = (key: string, value: string) =>
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set(key, value);
      if (key !== 'page') next.set('page', '1');
      return next;
    });
  const open = (asset: ArchivedAsset) => setSelectedId(asset.id);
  // 上传的落点按分类走：旧接口用枚举，新分类树用 id。两者都没有时按原片，
  // 这是 `POST /assets/upload` 的默认值，写出来比依赖默认值更可读。
  const uploadCategory: ArchiveCategory = 'source';
  return (
    <section className="space-y-3">
      {toolbar !== 'none' && (
        <StickyControls
          total={query.data?.total}
          density={density}
          sort={sort}
          isFetching={query.isFetching}
          onDensity={next => update('density', next)}
          onSort={next => update('sort', next)}
          onRefresh={() => {
            void query.refetch();
          }}
          onUpload={scope === 'mine' ? () => input.current?.click() : undefined}
          uploadDisabled={locked}
          uploadLabel="上传个人原片"
        />
      )}
      {toolbar === 'full' && (
        <div className="flex flex-wrap gap-2">
          <label className="text-xs">
            名称搜索
            <input
              type="search"
              className="ml-2 rounded border border-line bg-panel p-2"
              value={keyword}
              onChange={e => update('q', e.target.value)}
            />
          </label>
          {scope === 'mine' && (
            <label className="text-xs">
              可见性
              <select
                value={state}
                onChange={e => update('state', e.target.value)}
                className="ml-2 rounded border border-line bg-panel p-2"
              >
                <option value="all">全部</option>
                <option value="private">私有</option>
                <option value="shared">已分享</option>
              </select>
            </label>
          )}
          <Can permission="asset:create">
            <Button disabled={locked} onClick={() => input.current?.click()}>
              上传个人原片
            </Button>
          </Can>
        </div>
      )}
      <input
        ref={input}
        type="file"
        multiple
        accept=".mp4"
        hidden
        aria-label="上传个人原片"
        disabled={locked}
        onChange={e => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = '';
          if (files.length) start(files, uploadCategory);
        }}
      />
      {toolbar === 'full' && scope === 'mine' && (
        <>
          <p className="text-xs text-ink-mute">上传默认仅自己可见；分享后其他用户才可在公共原片中调用。</p>
          <ArchiveUploadPanel />
        </>
      )}
      {query.error && (
        <ErrorState
          error={query.error}
          title={query.data ? '更新失败，保留上次内容' : '素材读取失败'}
          onRetry={() => {
            void query.refetch();
          }}
        />
      )}
      {query.isPending && !query.data && <p role="status">正在读取素材…</p>}
      {query.data && !query.data.items.length && !query.error && (
        <EmptyState
          title={keyword ? '没有找到匹配的素材' : emptyHint ?? emptyTitle(scope)}
          description={
            scope === 'public'
              ? '可切换到我的素材上传原片。'
              : scope === 'trash'
                ? '删除的素材会在这里保留一段时间。'
                : '上传素材后可在这里管理。'
          }
        />
      )}
      <div
        className={
          density === 'compact'
            ? 'divide-y divide-line-soft rounded-xl border border-line bg-panel'
            : 'grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-3'
        }
      >
        {query.data?.items.map(asset =>
          density === 'compact' ? (
            <article key={asset.id} className="flex items-center gap-3 p-2">
              <span className="min-w-0 flex-1 truncate text-sm">{asset.name}</span>
              <AssetActions asset={asset} />
            </article>
          ) : (
            <article key={asset.id} className="rounded-xl border border-line bg-panel p-2">
              {asset.deletedAt ? (
                <p className="p-3 text-sm">{asset.name}</p>
              ) : (
                <ArchiveCard asset={asset} onOpen={open} />
              )}
              <AssetActions asset={asset} />
            </article>
          ),
        )}
      </div>
      <div className="flex items-center gap-3">
        <Button disabled={page <= 1 || query.isFetching} onClick={() => update('page', String(page - 1))}>
          上一页
        </Button>
        <span className="text-xs">第 {page} 页</span>
        <Button
          disabled={!query.data || page * 24 >= query.data.total || query.isFetching}
          onClick={() => update('page', String(page + 1))}
        >
          下一页
        </Button>
      </div>
      <ArchiveDetail asset={selected ?? null} onClose={() => setSelectedId(null)} />
    </section>
  );
}

/** 空列表的默认文案。团队库空了与自己的库空了不是同一件事：一个要等同事，一个要自己传。 */
function emptyTitle(scope: string): string {
  if (scope === 'public') return '暂无公共原片';
  if (scope === 'trash') return '回收站为空';
  if (scope === 'team') return '这个团队库还没有素材';
  return '暂无素材';
}
