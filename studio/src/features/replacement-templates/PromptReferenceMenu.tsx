import { useEffect, useRef, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { ChevronRight, Image as ImageIcon, Search, Video, X } from 'lucide-react';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import type { PromptMediaItem } from './PromptMediaView';
import { promptMediaLabel } from './promptMentions';

export interface PromptReferenceOption extends PromptMediaItem { label: string; subtitle?: string; disabled: boolean; referenced?: boolean }
export type PromptReferenceKind = 'all' | 'image' | 'video';
interface Props {
  id: string; options: PromptReferenceOption[]; prompt: string; query: string; active: number;
  anchor: {left: number; top: number}; focusSearch: boolean;
  kind: PromptReferenceKind; onKind: (kind: PromptReferenceKind) => void;
  onQuery: (query: string) => void; onActive: (index: number) => void;
  onChoose: (token: string) => void; onClose: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
}

export function PromptReferenceMenu({id, options, prompt, query, active, anchor, focusSearch, kind, onKind, onQuery, onActive, onChoose, onClose, onKeyDown}: Props) {
  const search = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const visible = options;
  const selectable = visible.filter(item => !item.disabled);
  const current = selectable[active % Math.max(1, selectable.length)];
  // 跨封装候选项的 token 是 `pack:<id>:<version>:<assetId>`，永远不会出现在正文里，
  // 所以「是否已引用」由编辑器按 assetId 算好传进来；没有该字段时才回退到正文匹配。
  const isReferenced = (item: PromptReferenceOption) =>
    item.referenced ?? (!!item.token && prompt.includes(item.token));
  const groups = [
    {name: '已引用', items: visible.filter(isReferenced)},
    {name: '当前模板其他素材', items: visible.filter(item => !item.subtitle && !isReferenced(item))},
    {name: '全部已封装素材', items: visible.filter(item => !!item.subtitle && !isReferenced(item))},
  ];
  useEffect(() => {if (focusSearch) search.current?.focus();}, [focusSearch]);
  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (panel.current?.contains(event.target as Node) || (event.target as HTMLElement).closest?.('.prompt-canvas-input, .prompt-canvas-toolbar')) return;
      onClose();
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [onClose]);
  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
      if (event.nativeEvent.isComposing || event.keyCode === 229) {event.stopPropagation(); return;}
      if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); onClose();}
      else if (event.key === 'Enter' && current?.token) {event.preventDefault(); onChoose(current.token);}
      else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault(); onActive((active + (event.key === 'ArrowDown' ? 1 : -1) + selectable.length) % Math.max(1, selectable.length));
      } else onKeyDown(event);
  }
  return createPortal(<div ref={panel} role="dialog" aria-label="插入引用素材" className="prompt-reference-menu" style={{left: anchor.left, top: anchor.top}}>
    <div className="prompt-reference-search"><Search size={15} /><input ref={search} aria-label="搜索引用素材" value={query} placeholder="搜索包名、图片名称或用途" onKeyDown={handleKeyDown} onChange={event => {onQuery(event.target.value); onActive(0);}} /><button type="button" aria-label="收起引用菜单" onKeyDown={handleKeyDown} onClick={onClose}><X size={14} /></button></div>
    <div className="prompt-reference-types" aria-label="引用素材类型">
      {([{key: 'all', label: '全部'}, {key: 'image', label: '图片'}, {key: 'video', label: '视频'}] as const).map(item => <button key={item.key} type="button" aria-pressed={kind === item.key} onKeyDown={handleKeyDown} onClick={() => {onKind(item.key); onActive(0);}}>{item.label}</button>)}
    </div>
    <div id={id} role="listbox" aria-label="选择引用素材" className="prompt-reference-results">
      {groups.map(group => group.items.length > 0 && <div key={group.name} role="group" aria-label={group.name}>
        <p className="prompt-reference-heading">{group.name}</p>
        {group.items.map(item => <button key={item.token} type="button" role="option" id={`${id}-option-${options.indexOf(item)}`}
          aria-selected={item.token === current?.token} disabled={item.disabled} title={item.label}
          onKeyDown={handleKeyDown}
          onMouseDown={event => event.preventDefault()} onClick={() => item.token && onChoose(item.token)}>
          <span className="prompt-reference-thumb"><ArchivedThumbnail url={item.previewUrl || item.url} type={item.type} /></span>
          <span className="min-w-0 flex-1"><span className="block truncate">{item.label}</span><small>{item.subtitle || promptMediaLabel(item)}{item.disabled ? ' · 暂不可用' : ''}</small></span>
        </button>)}
      </div>)}
      {!visible.length && <p role="status" className="p-3 text-xs text-ink-mute">没有匹配素材</p>}
    </div>
    <div className="prompt-reference-footer"><span>素材引用</span><button type="button" onKeyDown={handleKeyDown} onClick={() => {onKind('image'); onActive(0);}}><ImageIcon size={15} />图片<ChevronRight size={13} /></button><button type="button" onKeyDown={handleKeyDown} onClick={() => {onKind('video'); onActive(0);}}><Video size={15} />视频<ChevronRight size={13} /></button></div>
  </div>, document.body);
}
