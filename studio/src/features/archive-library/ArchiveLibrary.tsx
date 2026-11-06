import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import { ARCHIVE_ACCEPT, archiveCategory, type ArchiveCategory, type ArchivedAsset } from '@/entities/asset/model/archive.types';
import { ArchiveDetail } from './ArchiveDetail';
import { ArchiveGrid } from './ArchiveGrid';
import { ArchiveImportForm } from './ArchiveImportForm';
import { ArchiveToolbar } from './ArchiveToolbar';
import { ArchiveUploadPanel } from './ArchiveUploadPanel';
import { ArchivedResults } from './ArchivedResults';
import { hasUploading, isBatchLocked } from './model/upload-batch';
import { useUploadBatchStore } from './model/use-upload-batch';

/**
 * 素材资产库容器：只做编排 —— 分类进 URL、查询、上传入口、详情选中。
 * 三态与文案都交给 ArchiveGrid，容器不判断"有没有数据"。
 *
 * 上传不再由容器自己循环：批次状态在模块级 store，切走页面再回来仍然知道
 * 「哪几个已归档、哪一个待核对」。
 */
export function ArchiveLibrary() {
  const [params, setParams] = useSearchParams();
  const category = archiveCategory(params.get('category'));
  const query = useArchiveAssets(category);
  const batch = useUploadBatchStore(state => state.batch);
  const startBatch = useUploadBatchStore(state => state.start);
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<ArchivedAsset | null>(null);
  const locked = isBatchLocked(batch);

  function setCategory(value: ArchiveCategory) {
    if (locked) return;
    setParams(previous => {
      const next = new URLSearchParams(previous);
      next.set('category', value);
      return next;
    }, {replace: true});
  }
  /* 刷新后所选素材已不在当前分类：派生判断，不把列表镜像进 state。 */
  const missing = !!selected && !!query.data && !query.isFetching
    && !query.data.items.some(item => item.id === selected.id);
  return <section className="space-y-5">
    <div className="rounded-xl border border-primary/20 bg-primary/5 p-4">
      <p className="text-sm font-semibold text-ink">统一素材归档</p>
      <p className="mt-1 break-all text-xs text-ink-soft">归档位置：{query.data?.root ?? '—'}</p>
      <p className="mt-1 text-xs text-ink-mute">
        按原片、人物、产品、场景和音频分类；归档成功不等于可用于生成，替换适用性见卡片标注。
      </p>
    </div>
    <ArchiveToolbar category={category} onCategoryChange={setCategory} onPick={() => input.current?.click()}
      locked={locked} uploading={hasUploading(batch)}
      stats={{
        total: query.data?.total,
        usedBytes: query.data?.usedBytes,
        /* 只要还没有成功返回过数据（加载中或失败），统计就是未知，不能是 0。 */
        pending: !query.data,
        refreshing: query.isFetching && !!query.data,
      }} />
    <input ref={input} type="file" multiple hidden aria-label="上传归档素材" accept={ARCHIVE_ACCEPT[category]}
      disabled={locked}
      onChange={event => {
        const files = Array.from(event.target.files ?? []);
        /* 立刻清空：否则再选同一个文件不会触发 change，用户会以为没反应。 */
        event.target.value = '';
        if (files.length) startBatch(files, category);
      }} />
    <Can permission="asset:create"><ArchiveImportForm category={category} disabled={locked} /></Can>
    <ArchiveUploadPanel />
    {missing && <div role="alert"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel-soft px-3 py-2 text-xs text-ink-soft">
      所选素材已不在当前分类列表中，可能已切换分类或记录被移除。
      <Button size="sm" onClick={() => setSelected(null)}>关闭提示</Button>
    </div>}
    <ArchiveGrid category={category} assets={query.data?.items} error={query.error}
      isPending={query.isPending} isFetching={query.isFetching} onRetry={() => {void query.refetch();}}
      onPick={() => input.current?.click()} onOpen={setSelected} />
    <ArchiveDetail asset={missing ? null : selected} onClose={() => setSelected(null)} />
    <ArchivedResults />
  </section>;
}
