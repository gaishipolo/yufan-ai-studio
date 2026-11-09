import { useState } from 'react';
import { useCan } from '@/shared/access/use-access';
import { CardSkeletonGrid, EmptyState, ErrorState, Pager } from '@/shared/ui';
import { useSubjects } from '@/entities/asset/api/subject.api';
import { SubjectCard } from './SubjectCard';
import { SubjectDetailPanel } from './SubjectDetailPanel';

const PAGE_SIZE = 24;

/**
 * 主体列表：堆叠卡片 + 详情浮层。
 *
 * 这一页**不做组装**（拖图、改角色、调顺序）——组装台要按模板槽位才知道
 * "该按哪些角色摆"，M3.5 才有槽位契约。现在做一遍等于做错一遍。
 *
 * 分页用现成的 `Pager`：主体数量会随 M3.5 的组装台快速增长，
 * "一次全给我"的接口早晚要改，不如一开始就按页取。
 */
export function SubjectLibrary() {
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState('');
  const {data, isLoading, error, refetch, isFetching} = useSubjects({page, pageSize: PAGE_SIZE});
  const canPublish = useCan('asset:update');

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  if (isLoading) return <CardSkeletonGrid count={8} />;
  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }
  if (!items.length) {
    return (
      <EmptyState
        title="还没有主体"
        description="主体是「一组素材」的装配单位：同一角色的几张参考图放进去，模板才能按角色调用。当前演示数据由旧素材包生成，组装台在 M3.5 上线。"
      />
    );
  }

  // 0 表示"还不知道总数"（加载中/失败）：Pager 据此不做越界回收
  const totalPages = total ? Math.ceil(total / PAGE_SIZE) : 0;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {items.map(subject => (
          <SubjectCard key={subject.id} subject={subject} onOpen={setOpenId} />
        ))}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-ink-mute">
          共 {total} 个主体{isFetching ? ' · 更新中…' : ''}
        </span>
        <Pager page={page} totalPages={totalPages} busy={isFetching} onChange={setPage} />
      </div>
      {!!openId && (
        <SubjectDetailPanel
          subjectId={openId}
          canPublish={canPublish}
          onClose={() => setOpenId('')}
        />
      )}
    </div>
  );
}
