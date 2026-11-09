import { useMemo } from 'react';
import { Inbox, PackagePlus, Upload } from 'lucide-react';
import { Button, Chip, MultiSelect, SearchInput, Select, Tabs } from '@/shared/ui';
import type { LibraryFacets, LibraryTagOut } from '@/shared/api/schema';
import {
  KIND_OPTIONS,
  SORT_OPTIONS,
  VIEWS,
  viewMeta,
  viewAllowsCreate,
  type KindFilter,
  type LibrarySortKey,
  type LibraryView,
} from '@/entities/library/model/types';

export interface LibraryToolbarProps {
  view: LibraryView;
  facets?: LibraryFacets;
  total?: number;
  kind: KindFilter;
  keyword: string;
  sort: LibrarySortKey;
  tags: LibraryTagOut[];
  tagIds: string[];
  canCreate: boolean;
  pendingCount: number;
  busy?: boolean;
  onView: (view: LibraryView) => void;
  onKind: (kind: KindFilter) => void;
  onKeyword: (keyword: string) => void;
  onSort: (sort: LibrarySortKey) => void;
  onTags: (tagIds: string[]) => void;
  onUpload: () => void;
  onPack: () => void;
  onRequests: () => void;
}

/**
 * 五个一级入口 + 二级筛选（类型 / 标签 / 关键词 / 排序）。
 *
 * 上传与封装只出现在「个人」入口：草稿箱的内容来自生成入库，回收站里的东西
 * 不该再被建封装——把按钮留在那儿只会让人点出一个 404。
 */
export function LibraryToolbar(props: LibraryToolbarProps) {
  const meta = viewMeta(props.view);
  const items = useMemo(
    () =>
      VIEWS.map((item) => ({
        value: item.value,
        label: item.label,
        count: props.facets?.[item.facet],
      })),
    [props.facets],
  );

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2 rounded-card border border-line-soft bg-panel px-2.5 py-2">
        <Tabs items={items} value={props.view} onChange={props.onView} />
        <span className="ml-auto text-[11px] text-ink-mute">
          {props.total == null ? '统计中…' : `共 ${props.total} 条`}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          items={KIND_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
          value={props.kind}
          onChange={props.onKind}
        />

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <div className="w-[190px]">
            <SearchInput
              value={props.keyword}
              placeholder="搜索名称…"
              onChange={(event) => props.onKeyword(event.target.value)}
              aria-label="搜索资产名称"
            />
          </div>

          <div className="w-[132px]">
            <MultiSelect
              options={props.tags.map((tag) => ({ value: tag.id, label: tag.name }))}
              value={props.tagIds}
              onChange={props.onTags}
              placeholder="标签"
              emptyText="还没有标签"
              ariaLabel="按标签筛选"
            />
          </div>

          <Select
            options={SORT_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
            value={props.sort}
            onChange={(event) => props.onSort(event.target.value as LibrarySortKey)}
            aria-label="排序"
          />

          <Button
            size="sm"
            variant="ghost"
            icon={<Inbox size={13} />}
            onClick={props.onRequests}
            title="共享与撤销申请"
          >
            待我审核
            {props.pendingCount > 0 && (
              <Chip tone="danger" className="ml-0.5">
                {props.pendingCount}
              </Chip>
            )}
          </Button>

          {props.canCreate && viewAllowsCreate(props.view) && (
            <>
              <Button
                size="sm"
                variant="secondary"
                icon={<PackagePlus size={13} />}
                disabled={props.busy}
                onClick={props.onPack}
              >
                封装
              </Button>
              <Button
                size="sm"
                variant="primary"
                icon={<Upload size={13} />}
                disabled={props.busy}
                onClick={props.onUpload}
              >
                上传
              </Button>
            </>
          )}
        </div>
      </div>

      <p className="text-[11px] text-ink-mute">{meta.hint}</p>
    </div>
  );
}
