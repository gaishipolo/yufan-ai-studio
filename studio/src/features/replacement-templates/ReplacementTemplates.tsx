import { useRef, useState } from 'react';
import { Box, Film, Mountain, UserRound, ArrowRight, RefreshCw, Search, SlidersHorizontal, Layers, Plus } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { useArchiveAssets, useArchiveUpload } from '@/entities/asset/api/archive.queries';
import type { ArchiveCategory } from '@/entities/asset/model/archive.types';
import { ArchivedMedia, ArchivedThumbnail } from '@/entities/asset/ui/ArchivedMedia';
import { useExecuteReplacement, usePrepareReplacement, useReplacementCatalog } from '@/entities/template/api/replacement.queries';
import { replacementKind, type ReplacementKind, type ReplacementRun } from '@/entities/template/model/replacement.types';
import { Button, EmptyState, Modal } from '@/shared/ui';
import { SourceTemplateCard } from './SourceTemplateCard';
import './replacement-templates.css';
import { Can } from '@/shared/access/Can';
import { toast } from '@/shared/lib/toast';

const TYPES = [
  {kind: 'character' as const, label: '人物替换', short: '人物', icon: UserRound, description: '替换人物身份，延续原片表演与镜头'},
  {kind: 'product' as const, label: '产品替换', short: '产品', icon: Box, description: '保留创意与动作，展示你的产品'},
  {kind: 'scene' as const, label: '场景替换', short: '场景', icon: Mountain, description: '保留人物和商品，切换背景空间'},
];
const SCOPES = ['人物上身穿着', '手持展示产品', '盒内可见产品', '屏幕商品'];
const STATUS: Record<string, string> = {preparing: '正在准备输入', prepare_failed: '准备失败 · 可继续', prepared: '待核对', queued: '排队中', uploading: '上传及审核中',
  uploaded: '已上传', submitting: '提交中', submitted: '已提交', running: '生成中', generated: '下载中',
  success: '已完成 · 待验收', waiting_free: '报价非零 · 等待免费', waiting_executor: '等待旧执行器退出',
  submission_unknown: '提交结果未知 · 禁止重提', service_error: '连接待恢复', needs_login: '需要登录',
  failed: '生成失败', timeout: '上游超时'};
const CONTINUE = ['prepared', 'waiting_free', 'waiting_executor', 'service_error', 'needs_login'];
const field = 'w-full rounded-lg border border-line bg-panel p-2.5 text-sm text-ink outline-none focus:border-primary';

export function ReplacementTemplates() {
  const [params, setParams] = useSearchParams();
  const kind = replacementKind(params.get('kind'));
  const setKind = (value: ReplacementKind) => setParams(previous => {previous.set('kind', value); return previous;}, {replace: true});
  const [sources, setSources] = useState<string[]>([]);
  const [references, setReferences] = useState<string[]>([]);
  const [target, setTarget] = useState('');
  const [packId, setPackId] = useState('');
  const [wardrobeMode, setWardrobeMode] = useState<'preserve' | 'reference'>('preserve');
  const [scopes, setScopes] = useState(SCOPES);
  const [notes, setNotes] = useState('');
  const [reviewIds, setReviewIds] = useState<string[]>([]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const search = params.get('q') ?? '';
  const setSearch = (value: string) => setParams(previous => {if (value) previous.set('q', value); else previous.delete('q'); return previous;}, {replace: true});
  const [preparing, setPreparing] = useState(false);
  const requestIds = useRef(new Map<string, string>());
  const uploadInput = useRef<HTMLInputElement>(null);
  const [uploadCategory, setUploadCategory] = useState<ArchiveCategory>('source');
  const assets = useArchiveAssets();
  const catalog = useReplacementCatalog();
  const prepare = usePrepareReplacement();
  const execute = useExecuteReplacement();
  const upload = useArchiveUpload();
  const template = TYPES.find(item => item.kind === kind) ?? TYPES[0];
  const images = assets.data?.items.filter(asset => asset.category === kind) ?? [];
  const videos = assets.data?.items.filter(asset => asset.category === 'source' && asset.name.toLowerCase().includes(search.toLowerCase())) ?? [];
  const currentDetail = catalog.data?.runs.find(run => run.id === detailId);
  const review = catalog.data?.runs.filter(run => reviewIds.includes(run.id)) ?? [];
  function toggle(values: string[], id: string) {
    return values.includes(id) ? values.filter(value => value !== id) : [...values, id];
  }
  function pickUpload(category: ArchiveCategory) {
    setUploadCategory(category);
    if (uploadInput.current) {
      uploadInput.current.accept = category === 'source' ? '.mp4' : '.jpg,.jpeg,.png,.webp';
      uploadInput.current.click();
    }
  }
  async function prepareAll() {
    setPreparing(true);
    const prepared: ReplacementRun[] = [];
    try {
      for (const sourceId of sources) {
        const config = {kind, target: target.trim(), sourceId, referenceIds: references, wardrobeMode,
          packId: packId || null,
          replacementScope: scopes.join('、'), notes, aspectRatio: 'adaptive' as const};
        const key = JSON.stringify(config);
        let requestId = requestIds.current.get(key);
        if (!requestId) {requestId = crypto.randomUUID(); requestIds.current.set(key, requestId);}
        prepared.push(await prepare.mutateAsync({...config, requestId}));
      }
      setReviewIds(prepared.map(run => run.id));
    } catch (reason) {
      if (prepared.length) setReviewIds(prepared.map(run => run.id));
      toast(reason instanceof Error ? reason.message : '准备失败', 'error');
    } finally {setPreparing(false);}
  }
  async function submitRuns(runs: ReplacementRun[]) {
    try {
      for (const run of runs) await execute.mutateAsync(run.id);
      setReviewIds([]);
      toast('已进入仅 0 积分生产队列', 'success');
    } catch (reason) {toast(reason instanceof Error ? reason.message : '入队失败', 'error');}
  }
  return <div className="replacement-library">
    {(assets.error || catalog.error) && <p role="alert" className="rounded-lg bg-danger/5 p-3 text-sm text-danger">{assets.error?.message ?? catalog.error?.message}</p>}
    <div className="replacement-layout">
      <section className="replacement-sources" aria-label="原片模板库">
        <div className="replacement-toolbar">
          <label className="replacement-search"><Search size={15} /><input type="search" aria-label="搜索原片" placeholder="搜索模板名称…" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <Can permission="asset:create"><Button size="sm" variant="primary" icon={<Plus size={14} />} onClick={() => pickUpload('source')} loading={upload.isPending}>上传原片</Button></Can>
        </div>
        <div className="replacement-library-meta"><span>全部模板 <strong>{videos.length}</strong></span><span>已选 {sources.length} 条{sources.length > 0 && <button onClick={() => setSources([])}>清空选择</button>}</span></div>
        <div className="replacement-waterfall">
          {videos.map(video => <SourceTemplateCard key={video.id} video={video} selected={sources.includes(video.id)} onToggle={() => setSources(toggle(sources, video.id))} />)}
        </div>
        {!videos.length && <EmptyState title={assets.isLoading ? '正在读取模板…' : search ? '没有找到相关模板' : '添加你的第一条原片'} description={search ? '试试其他关键词，或清空搜索查看全部模板。' : '上传原片后，即可在这里预览并选择替换模板。'} />}
        <p className="replacement-source-note">支持批量选择 · 原片需为 1–15 秒、含音轨的完整 MP4，最大 300MB</p>
      </section>
      <aside className="replacement-config" aria-label="替换配置">
        <div className="replacement-config-heading"><SlidersHorizontal size={16} /><h2>替换配置</h2><span>{sources.length ? `已选 ${sources.length} 条` : '等待选择'}</span></div>
        <div className="replacement-kind-picker" aria-label="替换类型">
          {TYPES.map(item => <button key={item.kind} aria-pressed={kind === item.kind} onClick={() => {setKind(item.kind); setReferences([]); setPackId(''); setTarget('');}}><item.icon size={17} />{item.label}</button>)}
        </div>
        {!sources.length ? <div className="replacement-config-empty"><div><Layers size={32} strokeWidth={1.25} /></div><h3>选择一个模板，开始创作</h3><p>从左侧选择原片模板，再配置你的<br />{template.short}素材与替换要求。</p></div> : <div className="space-y-4">
        <div className="replacement-selected-summary"><Film size={15} /><span>已选择 {sources.length} 条原片</span><button onClick={() => setSources([])}>重选</button></div>
        <p className="text-xs leading-5 text-ink-mute">{template.description}</p>
        <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold text-ink">目标{template.short}</h3><Can permission="asset:create"><Button size="sm" onClick={() => pickUpload(kind)} loading={upload.isPending}>上传参考图</Button></Can></div>
        <label className="block space-y-2 text-xs text-ink-soft"><span>已封装素材包</span><select className={field} value={packId} onChange={event => {
          const value = catalog.data?.packs?.find(pack => pack.id === event.target.value);
          setPackId(value?.id ?? ''); setReferences(value?.referenceIds ?? []); setTarget(value?.name ?? '');
          if (value) setWardrobeMode(value.wardrobeMode ?? 'preserve');
        }}><option value="">手动选择参考图</option>{catalog.data?.packs?.filter(pack => pack.kind === kind).map(pack => <option key={pack.id} value={pack.id}>{pack.name} · {pack.referenceIds.length} 张参考</option>)}</select></label>
        <label className="block space-y-2 text-xs text-ink-soft"><span>目标名称</span><input className={field} value={target} readOnly={!!packId} maxLength={160} placeholder={`例如：目标${template.short}的名称`} onChange={event => setTarget(event.target.value)} /></label>
        <p className="text-xs text-ink-mute">参考图按勾选顺序绑定，最多 10 张。人物多视图应属于同一人。</p>
        <div className="grid max-h-52 grid-cols-2 gap-2 overflow-auto">
          {images.map(image => <label key={image.id} className={`flex cursor-pointer flex-col gap-2 rounded-lg border p-3 text-xs ${references.includes(image.id) ? 'border-primary bg-primary/5' : 'border-line'}`}>
            {image.previewUrl && <div className="h-20 w-full"><ArchivedThumbnail url={image.previewUrl} name={image.name} /></div>}
            <input type="checkbox" checked={references.includes(image.id)} disabled={!!packId || (references.length >= 10 && !references.includes(image.id))} onChange={() => setReferences(toggle(references, image.id))} />
            <span className="break-all text-ink">{references.includes(image.id) && <span className="mr-1 text-primary">#{references.indexOf(image.id) + 1}</span>}{image.name}</span>
          </label>)}
        </div>
        {!images.length && <p className="text-xs text-ink-mute">暂无{template.short}参考图，请先上传或到素材资产库登记。</p>}
        <div className="border-t border-line pt-4"><h2 className="mb-3 font-semibold text-ink">替换范围</h2>
          {kind === 'character' && <label className="block space-y-2 text-xs text-ink-soft"><span>人物与穿搭</span><select className={field} value={wardrobeMode} disabled={!!packId} onChange={event => setWardrobeMode(event.target.value === 'reference' ? 'reference' : 'preserve')}>
            <option value="preserve">只换人物身份，保留原穿搭和商品</option><option value="reference">人物及参考服装一起替换</option></select></label>}
          {kind === 'product' && <fieldset className="grid grid-cols-2 gap-3"><legend className="mb-2 text-xs text-ink-soft">原片中允许替换的位置</legend>{SCOPES.map(scope => <label key={scope} className="flex gap-2 text-xs text-ink"><input type="checkbox" checked={scopes.includes(scope)} onChange={() => setScopes(toggle(scopes, scope))} />{scope}</label>)}</fieldset>}
          {kind === 'scene' && <p className="rounded-lg bg-panel-soft p-3 text-xs leading-6 text-ink-soft">仅替换背景环境、建筑、材质与陈设；保留原片人物、商品、手持物及前景交互关系。</p>}
        </div>
        <label className="block space-y-2 text-xs text-ink-soft"><span>补充要求（选填）</span><textarea className={field} rows={3} value={notes} maxLength={2000} onChange={event => setNotes(event.target.value)} placeholder="需要保留的动作、出现时机或细节" /></label>
        <Can permission="template:use"><Button block variant="primary" icon={<ArrowRight size={15} />} loading={preparing}
          disabled={!sources.length || !references.length || !target.trim() || (kind === 'product' && !scopes.length) || !!assets.error}
          onClick={() => {void prepareAll();}}>预览提示词并归档输入</Button></Can>
        <p className="text-xs leading-5 text-ink-mute">先保存输入快照与编译提示词，核对后再加入生产队列。提交前重新估价，非零报价等待免费，不自动切换付费。</p>
        </div>}
        <div className="replacement-config-footer"><span>LibTV · 720P · 仅 0 积分</span><Link to="/assets">管理素材 →</Link></div>
      </aside>
    </div>
    <input ref={uploadInput} type="file" hidden aria-label="替换模板上传素材" onChange={event => {
      const file = event.target.files?.[0]; if (!file) return;
      upload.mutate({file, category: uploadCategory}, {onSuccess: asset => {
        if (asset.category === 'source') setSources(values => [...values, asset.id]);
        else {setPackId(''); setReferences(values => values.length < 10 ? [...values, asset.id] : values); if (!target) setTarget(asset.name.replace(/\.[^.]+$/, ''));}
        toast('已保存到 Z 盘素材库', 'success');
      }, onError: reason => toast(reason.message, 'error')});
      event.target.value = '';
    }} />
    <section className="replacement-task-history rounded-xl border border-line bg-panel p-4">
      <div className="mb-4 flex items-center justify-between"><h2 className="font-semibold text-ink">替换任务与调用档案</h2><Button size="sm" icon={<RefreshCw size={13} />} onClick={() => {void catalog.refetch();}}>刷新</Button></div>
      {!catalog.data?.runs.length ? <p className="py-4 text-sm text-ink-mute">尚无替换任务。每次准备的输入、提示词及后续结果都会保存在同一调用目录。</p> :
        <div className="space-y-2">{catalog.data.runs.map(run => <button key={run.id} onClick={() => setDetailId(run.id)} className="flex w-full flex-wrap items-center gap-3 rounded-xl border border-line p-3 text-left hover:border-primary/40">
          <span className="text-sm font-semibold text-ink">{run.target}</span><span className="text-xs text-ink-mute">{TYPES.find(item => item.kind === run.kind)?.label}</span>
          <span className="ml-auto rounded-full bg-panel-soft px-3 py-1 text-xs text-ink-soft">{STATUS[run.status] ?? run.status}</span>
          <span className="text-xs text-primary">查看档案 →</span>
        </button>)}</div>}
    </section>
    <Modal open={review.length > 0} onClose={() => setReviewIds([])} title="核对替换任务" width={880}>
      <div className="space-y-4"><p className="text-sm text-ink-soft">已准备 {review.length} 条原片。以下是本次最终编译提示词，输入实物副本已存入归档盘。</p>
        {review.map(run => <details key={run.id} open={review.length === 1} className="rounded-xl border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">{run.target} · {run.archivePath}</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs leading-6 text-ink-soft">{run.prompt}</pre></details>)}
        <p className="text-xs text-ink-mute">确认后将原片与参考图上传到 LibTV，并仅在估价为 0 积分时提交。</p>
        <Can permission="template:use"><Button variant="primary" loading={execute.isPending} onClick={() => {void submitRuns(review);}}>确认并加入零积分生产队列</Button></Can>
      </div>
    </Modal>
    <Modal open={!!currentDetail} onClose={() => setDetailId(null)} title="调用档案" width={880}>
      {currentDetail && <div className="space-y-4">
        <p className="text-sm font-semibold text-ink">{currentDetail.target} · {STATUS[currentDetail.status] ?? currentDetail.status}</p>
        <p className="break-all text-xs text-ink-mute">{currentDetail.archivePath}</p>
        <p className="text-xs text-ink-soft">任务 ID：{currentDetail.taskId ?? '尚未提交'} · 估价：{currentDetail.estimatedCredits ?? '待查询'} · 实际积分：{currentDetail.credits ?? '待回传'} · 人工验收：待验收</p>
        {currentDetail.lastError && <p role="alert" className="rounded-lg bg-danger/5 p-3 text-xs text-danger">{currentDetail.lastError}</p>}
        {currentDetail.outputUrl && <ArchivedMedia url={currentDetail.outputUrl} type="video" name={currentDetail.target} />}
        <details><summary className="cursor-pointer text-sm text-primary">查看最终提示词</summary><pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-6 text-ink-soft">{currentDetail.prompt}</pre></details>
        {(CONTINUE.includes(currentDetail.status) || (currentDetail.taskId && !['success', 'failed', 'timeout'].includes(currentDetail.status))) &&
          <Can permission="template:use"><Button variant="primary" loading={execute.isPending} onClick={() => {void submitRuns([currentDetail]);}}>{currentDetail.taskId ? '继续查询已有任务' : '确认并继续零积分提交'}</Button></Can>}
      </div>}
    </Modal>
  </div>;
}
