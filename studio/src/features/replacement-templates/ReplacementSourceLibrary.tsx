import { Film, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { VideoTemplateOut } from '@/shared/api/schema';
import { Can } from '@/shared/access/Can';
import { Button, EmptyState } from '@/shared/ui';
import { CardSkeletonGrid } from '@/shared/ui/EmptyState';
import { ErrorState } from '@/shared/ui/ErrorState';
import { TYPES } from './replacementOptions';
import { SourceTemplateCard } from './SourceTemplateCard';
import type { ReplacementWorkbench } from './useReplacementWorkbench';
import { TemplatePromptDialog } from './TemplatePromptDialog';

/** 封装过的卡片和 0507 高端姐共用同一个组件，只多一枚「默认替换类型」标识。 */
function templateBadge(binding: VideoTemplateOut | undefined) {
  if (!binding) return undefined;
  const raw = binding.defaultKind
    ?? (typeof binding.rules?.defaultKind === 'string' ? binding.rules.defaultKind : 'product');
  const label = TYPES.find(item => item.kind === raw)?.label ?? '产品替换';
  return `视频模板 · ${label}`;
}

interface ReplacementSourceLibraryProps { model: ReplacementWorkbench }
export function ReplacementSourceLibrary({ model }: ReplacementSourceLibraryProps) {
  const [previewId, setPreviewId] = useState<string | null>(null);
  const previewSource = model.videos.find(video => video.id === previewId);
  const previewTemplates = (model.videoTemplates.data?.items ?? []).filter(item => item.sourceVideoAssetId === previewId);
  const {assets, search, setSearch, sourceId, setSourceId, selectSource, templateForSource, videos, upload, busy, pickUpload} = model;
  return (
      <section className="replacement-sources" aria-label="原片模板库" aria-busy={assets.isLoading}>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} aria-pressed={model.sourceScope === 'mine'} onClick={() => model.setSourceScope('mine')}>我的原片</Button>
          <Button disabled={busy} aria-pressed={model.sourceScope === 'public'} onClick={() => model.setSourceScope('public')}>公共原片</Button>
        </div>
        {model.linkedSource?.error && <ErrorState error={model.linkedSource.error} title="带入的原片不可用，请重新选择" />}
        <div className="replacement-toolbar">
          <label className="replacement-search"><Search size={15} /><input type="search" aria-label="搜索原片" placeholder="搜索模板名称…" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <Can permission="asset:create"><Button size="sm" variant="primary" icon={<Plus size={14} />} onClick={() => pickUpload()} disabled={busy} loading={upload.isPending}>上传原片</Button></Can>
        </div>
        <div className="replacement-library-meta"><span>{search ? '匹配模板' : '全部模板'} <strong>{assets.isLoading && !assets.data ? '—' : videos.length}</strong></span><span>已选 {sourceId ? 1 : 0} 条{!!sourceId && <button disabled={busy} onClick={() => setSourceId(null)}>清空选择</button>}</span></div>
        {assets.error && <ErrorState error={assets.error} title={assets.data ? '素材更新失败，以下为上次加载的内容' : '原片与参考素材加载失败'} onRetry={() => {void assets.refetch();}} />}
        {assets.isLoading && !assets.data ? <div role="status" aria-label="正在加载原片模板"><p className="replacement-operation-status">正在加载原片模板…</p><CardSkeletonGrid count={6} /></div> : <>
        <div className="replacement-waterfall">
          {videos.map(video => <SourceTemplateCard key={video.id} video={video} badge={templateBadge(templateForSource(video))} selected={sourceId === video.id} disabled={busy} onToggle={() => selectSource(sourceId === video.id ? null : video.id)} onViewPrompt={templateForSource(video) ? () => setPreviewId(video.id) : undefined} />)}
        </div>
        {!assets.error && !videos.length && <EmptyState icon={search ? <Search size={36} /> : <Film size={36} />} title={search ? '没有找到相关模板' : model.sourceScope === 'public' ? '暂无公共原片' : '添加你的第一条原片'} description={search ? '试试其他关键词，或清空搜索查看全部模板。' : '上传个人原片，再选择专员发布的对象进行替换。'} action={search ? <Button onClick={() => setSearch('')}>清空搜索</Button> : <div className="space-y-3"><Can permission="asset:create"><Button variant="primary" onClick={() => pickUpload()} disabled={busy} loading={upload.isPending}>上传第一条原片</Button></Can><p><Link to="/assets" className="text-xs text-primary">前往素材资产库 →</Link></p></div>} />}
        </>}
        <div className="flex gap-2"><Button disabled={busy || model.sourcePage <= 1} onClick={() => model.setSourcePage(model.sourcePage - 1)}>上一页</Button><Button disabled={busy || !assets.data || model.sourcePage * 24 >= assets.data.total} onClick={() => model.setSourcePage(model.sourcePage + 1)}>下一页</Button></div>
        <p className="replacement-source-note">每次选择 1 条原片 · 原片需为 1–15 秒、含音轨的完整 MP4，最大 300MB</p>
        {previewSource && previewTemplates.length > 0 && <TemplatePromptDialog key={previewSource.id} source={previewSource} templates={previewTemplates} packs={model.catalog.data?.packs ?? []} onClose={() => setPreviewId(null)} />}
      </section>
  );
}
