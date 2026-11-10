import { useMemo } from 'react';
import { Inbox, PackagePlus, Upload } from 'lucide-react';
import { Button, Chip, SearchInput, Select, Tabs } from '@/shared/ui';
import type { LibraryFacets } from '@/shared/api/schema';
import {
  KIND_OPTIONS,
  SORT_OPTIONS,
  VIEWS,
  viewAllowsCreate,
  type KindFilter,
  type LibrarySortKey,
  type LibraryView,
} from '@/entities/library/model/types';

export interface LibraryToolbarProps {
  view: LibraryView;
  facets?: LibraryFacets;
  kind: KindFilter;
  keyword: string;
  sort: LibrarySortKey;
  canCreate: boolean;
  pendingCount: number;
  busy?: boolean;
  onView: (view: LibraryView) => void;
  onKind: (kind: KindFilter) => void;
  onKeyword: (keyword: string) => void;
  onSort: (sort: LibrarySortKey) => void;
  onUpload: () => void;
  onPack: () => void;
  onRequests: () => void;
}

/**
 * 六个一级入口 + 二级筛选（类型 / 关键词 / 排序）。
 *
 * 入口顺序与徽章都由 `VIEWS` 一处定义：总览在最前，草稿箱与回收站挪到末尾。
 * 标签筛选刻意不放在这里：下拉框只能看到「选中的那几个」，用户要看到词表里
 * 全部标签、每个标签有多少素材、并直接点一下就筛，那是一个独立的标签条
 * （`LibraryTagBar`），不是塞在筛选行里的第三个控件。
 *
 * 上传与封装只在「全部素材」「个人」出现：草稿箱的内容来自生成入库，回收站
 * 里的东西不该再被建封装——把按钮留在那儿只会让人点出一个 404。
 */
export function LibraryToolbar(props: LibraryToolbarProps) {
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
    <div className="space-y-2">
      {/* 一级入口与二级筛选收进同一张卡：两块独立面板 + 一行悬空的小字，
          视觉上像三个互不相干的区块，用户得先分辨「哪一块在管列表」。 */}
      <div className="rounded-card border border-line-soft bg-panel px-2.5 py-2">
        <Tabs items={items} value={props.view} onChange={props.onView} />

        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line-soft pt-2">
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
      </div>
    </div>
  );
}
