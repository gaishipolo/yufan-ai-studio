import { useId, useLayoutEffect, useRef, useState } from 'react';
import { SlidersHorizontal, Layers, ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Can } from '@/shared/access/Can';
import { Button } from '@/shared/ui';
import { TYPES, FIELD_CLASS } from './replacementOptions';
import { ReplacementScope } from './ReplacementScope';
import { ReplacementPackPicker } from './ReplacementPackPicker';
import { ReplacementSelectedSources } from './ReplacementSelectedSources';
import type { ReplacementWorkbench } from './useReplacementWorkbench';
import { TemplatePromptDialog } from './TemplatePromptDialog';
import { TextRefPicker } from '@/features/library/TextRefPicker';

interface ReplacementConfigProps { model: ReplacementWorkbench }
export function ReplacementConfig({ model }: ReplacementConfigProps) {
  const [showPrompt, setShowPrompt] = useState(false);
  const id = useId();
  const panelRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    let frame = 0;
    const measure = () => {
      const top = Math.max(70, panel.getBoundingClientRect().top);
      panel.style.setProperty('--replacement-config-top', `${top}px`);
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener('scroll', schedule, {passive: true, capture: true});
    window.addEventListener('resize', schedule);
    // 反馈条展开、标题换行等也会改变面板的起始位置。
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(schedule);
    if (panel.parentElement) observer?.observe(panel.parentElement);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
    };
  }, []);
  const {sourceId, kind, setKind, notes, setNotes, busy, template,
    assets, catalog, missing, preparing, prepareTask} = model;
  return (
      <aside ref={panelRef} className="replacement-config" aria-label="替换配置">
        <div className="replacement-config-heading"><SlidersHorizontal size={16} /><h2>替换配置</h2><span>{sourceId ? '已选 1 条' : '等待选择'}</span></div>
        {model.boundTemplate && <div className="px-4 pb-2"><Button size="sm" onClick={() => setShowPrompt(true)}>查看当前功能提示词</Button></div>}
        <div className="replacement-kind-picker" aria-label="替换类型">
          {model.boundTemplate ? model.functionOptions.map(item => <button key={item.id} disabled={busy} aria-pressed={model.boundTemplate?.id === item.id} onClick={() => model.selectFunction(item.id)}><item.icon size={17} />{item.label}</button>) : TYPES.map(item => <button key={item.kind} disabled={busy || model.videoTemplates.isPending || !!model.videoTemplates.error} aria-pressed={kind === item.kind} onClick={() => setKind(item.kind)}><item.icon size={17} />{item.label}</button>)}
        </div>
        <div className="replacement-config-body">
        <fieldset disabled={busy} className="min-w-0">
        <label htmlFor={id + '-channel'} className="mb-4 block space-y-2 text-sm font-semibold"><span>模型渠道</span>
          <select aria-label="模型渠道" id={id + '-channel'} className={FIELD_CLASS} value={model.channel} onChange={event => model.setChannel(event.target.value)}>
            {model.channels.map(item => <option key={item.id} value={item.id} disabled={!item.enabled}>{item.label}{!item.enabled ? ' · ' + (item.unavailableReason || '暂不可用') : ''}</option>)}
          </select>
          <span className="block text-xs font-normal text-ink-mute">{model.channel === 'libtv' ? 'LibTV 积分队列' : '阿里云 API 计费'}</span>
        </label>
        {!sourceId ? <div className="replacement-config-empty"><div><Layers size={32} strokeWidth={1.25} /></div><h3>选择一个模板，开始创作</h3><p>从左侧选择原片模板，再配置你的<br />{template.short}素材包与替换要求。</p></div> : <div className="space-y-4">
        <details className="text-xs text-ink-soft"><summary className="cursor-pointer py-1">查看已选原片</summary><div className="mt-2"><ReplacementSelectedSources model={model} /></div></details>
        {model.isTextScene ? <div className="space-y-2">
          <label htmlFor={`${id}-scene`} className="block text-sm font-semibold">场景描述</label>
          <textarea id={`${id}-scene`} className={FIELD_CLASS} rows={7} value={model.scenePrompt} maxLength={4000} onChange={event => model.setScenePrompt(event.target.value)} aria-describedby={`${id}-scene-hint`} />
          <div className="flex items-center justify-between gap-2 text-xs text-ink-mute"><Button size="sm" disabled={busy || model.scenePrompt === model.defaultScenePrompt} onClick={() => model.setScenePrompt(model.defaultScenePrompt)}>恢复默认场景描述</Button><span>{model.scenePrompt.length}/4000</span></div>
          <p id={`${id}-scene-hint`} className="text-xs leading-5 text-ink-soft">描述想要的环境、空间与氛围。人物、产品、动作和音频等保留要求由模板固定；下一步可核对完整提示词。</p>
          <TextRefPicker value={model.textRefs} onChange={model.setTextRefs} prompt={model.scenePrompt} onPromptChange={model.setScenePrompt} />
        </div> : <>
        <ReplacementPackPicker model={model} hintId={`${id}-pack-hint`} />
        <ReplacementScope model={model} id={id} />
        <details className="text-xs text-ink-soft"><summary className="cursor-pointer py-1">补充要求（选填）</summary><label htmlFor={`${id}-notes`} className="mt-2 block space-y-2"><span>需要特别保留或调整的细节</span><textarea id={`${id}-notes`} className={FIELD_CLASS} rows={3} value={notes} maxLength={2000} onChange={event => setNotes(event.target.value)} placeholder="需要保留的动作、出现时机或细节" /></label></details>
        <TextRefPicker value={model.textRefs} onChange={model.setTextRefs} prompt={notes} onPromptChange={setNotes} />
        <p className="text-xs leading-5 text-ink-mute">占位符也可写在场景描述、补充要求与替换范围里；编号按上方绑定顺序对应。</p>
        </>}
        </div>}
        </fieldset>
        </div>
        {!!sourceId && <div className="replacement-config-action">
        <Can permission="template:use"><Button block variant="primary" icon={<ArrowRight size={15} />} loading={preparing}
          disabled={!!missing || busy || assets.isLoading || !!assets.error || !!catalog.error || catalog.isLoading}
          onClick={() => {void prepareTask();}}>下一步：确认替换</Button></Can>
        {missing && <p className="text-xs text-ink-mute">{missing}，完成后即可准备任务。</p>}
        <p className="text-xs leading-5 text-ink-mute">{model.channel === 'libtv' ? '核对后再生成，仅零积分，不自动付费。' : '核对后再生成，使用阿里云 API 计费。'}</p>
        </div>}
        <div className="replacement-config-footer"><span>{model.channel === 'libtv' ? 'LibTV · 720P · 仅 0 积分' : (model.currentChannel?.label ?? '百炼') + ' · 阿里云 API 计费'}</span><Link to="/assets">管理素材 →</Link></div>
        {showPrompt && model.boundTemplate && <TemplatePromptDialog key={model.boundTemplate.id} source={model.videos.find(video => video.id === sourceId) ?? model.linkedSource.data ?? undefined} templates={model.boundTemplates} selectedTemplateId={model.boundTemplate.id} packs={catalog.data?.packs ?? []} scenePrompt={model.isTextScene ? model.scenePrompt : undefined} currentPack={model.selectedPack} currentPacks={model.slots > 1 ? model.chosenPacks : undefined} onClose={() => setShowPrompt(false)} />}
      </aside>
  );
}
