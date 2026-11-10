import { useState } from 'react';
import type { ArchivedAsset, ReplacementPack, VideoTemplateOut } from '@/shared/api/schema';
import { Modal } from '@/shared/ui';
import { MediaGallery, PromptMediaView } from './PromptMediaView';
import { templatePromptMedia } from './templatePromptMedia';
import { videoTemplateLabel } from './videoTemplateFunctions';

interface Props {
  source?: ArchivedAsset;
  scenePrompt?: string;
  templates: VideoTemplateOut[];
  packs: ReplacementPack[];
  selectedTemplateId?: string;
  currentPack?: ReplacementPack;
  currentPacks?: ReplacementPack[];
  onClose: () => void;
}

export function TemplatePromptDialog({source, templates, packs, selectedTemplateId, scenePrompt, currentPack, currentPacks, onClose}: Props) {
  const [selected, setSelected] = useState(selectedTemplateId ?? templates[0]?.id ?? '');
  const template = templates.find(item => item.id === selected) ?? templates[0];
  if (!template) return null;
  const pack = currentPack ?? packs.find(item => item.id === template.packId);
  const textScene = template.rules?.inputMode === 'video_scene_text';
  const description = scenePrompt ?? (typeof template.rules?.scenePromptDefault === 'string' ? template.rules.scenePromptDefault : '');
  const prompt = textScene ? template.prompt.replaceAll('{{ScenePrompt}}', () => description) : template.prompt;
  const media = templatePromptMedia(template, source, textScene ? undefined : pack, textScene ? undefined : currentPacks);
  const missingReferences = media.some(item => item.type === 'image' && !item.url);
  const selectionKey = currentPacks?.map(item => `${item.id}:${item.version}`).join(',') ?? pack?.id;
  return <Modal open onClose={onClose} title="模板提示词与输入预览" width={900}>
    <div className="space-y-4 p-5">
      <p className="text-sm font-semibold text-ink">{template.name}</p>
      {templates.length > 1 && <label className="block space-y-1 text-xs text-ink-soft">查看封装功能
        <select aria-label="查看封装功能" value={template.id} onChange={event => setSelected(event.target.value)} className="ml-2 rounded-lg border border-line bg-panel p-2">
          {templates.map(item => <option key={item.id} value={item.id}>{videoTemplateLabel(item)} · {item.name}</option>)}
        </select>
      </label>}
      <p className="rounded-lg bg-panel-soft p-3 text-xs leading-5 text-ink-soft">准备前预览 · {textScene ? '文字换场景：只上传原片，使用当前场景描述' : <>{currentPacks?.length ? '当前所选人物：' : currentPack ? '当前所选素材包：' : '默认素材包：'}{currentPacks?.length ? currentPacks.map(item => item.name).join('、') : pack?.name ?? template.packName ?? '暂不可用'}</>}。创建任务后以当次输入快照为准；这里只查看，不提交生成。</p>
      {(!textScene && (!pack || pack.available === false || missingReferences)) && <p role="status" className="text-xs text-danger">请补齐人物选择或所需参考用途；媒体预览可能不完整，准备任务时仍需校验。</p>}
      <MediaGallery key={`gallery:${template.id}:${selectionKey}`} media={media} prompt={prompt} preview />
      <p className="text-xs font-semibold text-ink">封装提示词 · 点击缩略图核对引用</p>
      {template.prompt.includes('{{ReplacementScope}}') && <p className="text-xs text-ink-mute">范围参数将在准备任务时替换为你的选择。</p>}
      <PromptMediaView key={`prompt:${template.id}:${selectionKey}`} prompt={prompt} media={media} />
    </div>
  </Modal>;
}
