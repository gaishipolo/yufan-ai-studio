import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import type { PromptMediaItem } from './PromptMediaView';
import { atomicPromptRange, getPromptSelection, PROMPT_TOKEN_PATTERN, readPromptDom, setPromptSelection, type PromptRange } from './promptEditorDom';
import { promptMediaLabel } from './promptMentions';

export interface PromptCanvasHandle {
  selection: () => PromptRange;
  replace: (range: PromptRange, text: string) => void;
  caretRect: () => DOMRect | undefined;
}
interface Props {
  id: string;
  value: string;
  media: PromptMediaItem[];
  disabled: boolean;
  describedBy: string;
  activeOption?: string;
  onChange: (text: string, selection: PromptRange) => void;
  onSelection: (selection: PromptRange) => void;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onComposing: (value: boolean) => void;
  onPreview: (media: PromptMediaItem) => void;
  onFiles: (files: File[]) => void;
}
interface Snapshot { text: string; range: PromptRange }

export const PromptCanvasInput = forwardRef<PromptCanvasHandle, Props>(function PromptCanvasInput({
  id, value, media, disabled, describedBy, activeOption, onChange, onSelection, onKeyDown, onComposing, onPreview, onFiles,
}, ref) {
  const root = useRef<HTMLDivElement>(null);
  const composing = useRef(false);
  const pending = useRef<PromptRange | null>(null);
  const lastRange = useRef<PromptRange>({start: value.length, end: value.length});
  const beforeRange = useRef<PromptRange>(lastRange.current);
  const history = useRef<{past: Snapshot[]; future: Snapshot[]}>({past: [], future: []});
  const [version, setVersion] = useState(0);
  const [chips, setChips] = useState<{target: HTMLElement; token: string}[]>([]);
  const mediaKey = media.map(item => `${item.token}:${item.id}:${item.url ?? ''}:${item.name}`).join('|');
  const rendered = useRef('');
  const scrollToCaret = useRef(false);

  function selection() {return (root.current && getPromptSelection(root.current)) || lastRange.current;}
  function commit(text: string, range: PromptRange, remember = true) {
    if (disabled) return;
    if (remember && text !== value) {
      history.current.past.push({text: value, range: beforeRange.current});
      if (history.current.past.length > 100) history.current.past.shift();
      history.current.future = [];
    }
    pending.current = range;
    lastRange.current = range;
    onChange(text, range);
    setVersion(previous => previous + 1);
  }
  function replace(range: PromptRange, text: string) {
    beforeRange.current = range;
    commit(value.slice(0, range.start) + text + value.slice(range.end), {start: range.start + text.length, end: range.start + text.length});
  }
  function undo(redo = false) {
    const from = redo ? history.current.future : history.current.past;
    const to = redo ? history.current.past : history.current.future;
    const next = from.pop();
    if (!next) return;
    to.push({text: value, range: selection()});
    commit(next.text, next.range, false);
  }
  function notifySelection() {
    const range = root.current && getPromptSelection(root.current);
    if (!range) return;
    lastRange.current = range;
    beforeRange.current = range;
    onSelection(range);
  }

  useImperativeHandle(ref, () => ({selection, replace, caretRect: () => {
    const range = window.getSelection()?.rangeCount ? window.getSelection()?.getRangeAt(0) : undefined;
    return range && root.current?.contains(range.startContainer) && typeof range.getBoundingClientRect === 'function'
      ? range.getBoundingClientRect() : root.current?.getBoundingClientRect();
  }}));

  useLayoutEffect(() => {
    const element = root.current;
    if (!element || composing.current) return;
    const key = `${value}\0${mediaKey}\0${version}`;
    if (rendered.current === key) return;
    rendered.current = key;
    const range = pending.current ?? (document.activeElement === element ? getPromptSelection(element) : null);
    const scrollTop = element.scrollTop;
    const fragment = document.createDocumentFragment();
    const targets: {target: HTMLElement; token: string}[] = [];
    let offset = 0;
    for (const match of value.matchAll(PROMPT_TOKEN_PATTERN)) {
      fragment.append(document.createTextNode(value.slice(offset, match.index)));
      const target = document.createElement('span');
      target.contentEditable = 'false';
      target.dataset.promptToken = match[0];
      target.className = 'prompt-canvas-token';
      fragment.append(target);
      targets.push({target, token: match[0]});
      offset = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(value.slice(offset)));
    element.replaceChildren(fragment);
    element.scrollTop = scrollTop;
    setChips(targets);
    if (range) {
      element.focus();
      setPromptSelection(element, range);
      scrollToCaret.current = true;
    }
    pending.current = null;
  }, [value, mediaKey, version]);

  // 标签门户渲染完成后再测量：缩略图和标签宽度会影响实际换行位置。
  useLayoutEffect(() => {
    const element = root.current;
    if (!scrollToCaret.current || !element) return;
    scrollToCaret.current = false;
    const selection = window.getSelection();
    if (!selection?.rangeCount) return;
    const range = selection.getRangeAt(0).cloneRange();
    if (!element.contains(range.endContainer) || typeof range.getBoundingClientRect !== 'function') return;
    range.collapse(false);
    let rect = range.getBoundingClientRect();
    if (!rect.height && range.endContainer.nodeType === Node.TEXT_NODE && range.endOffset > 0) {
      range.setStart(range.endContainer, range.endOffset - 1);
      rect = range.getBoundingClientRect();
    } else if (!rect.height && range.endOffset > 0 && range.endContainer.nodeType === Node.ELEMENT_NODE) {
      range.setStartBefore(range.endContainer.childNodes[range.endOffset - 1]);
      rect = range.getBoundingClientRect();
    }
    if (!rect.height) return;
    const bounds = element.getBoundingClientRect();
    if (rect.bottom > bounds.bottom - 8) element.scrollTop += rect.bottom - bounds.bottom + 8;
    else if (rect.top < bounds.top + 8) element.scrollTop -= bounds.top - rect.top + 8;
  }, [chips]);

  return <>
    <div ref={root} id={id} role="textbox" aria-label="封装提示词原文" aria-multiline="true"
      aria-describedby={describedBy} aria-readonly={disabled} aria-autocomplete="list" aria-activedescendant={activeOption}
      tabIndex={0} contentEditable={!disabled} suppressContentEditableWarning spellCheck={false}
      className="prompt-canvas-input" data-placeholder="输入提示词，使用 @ 引用素材…"
      onInput={event => {
        if (composing.current) return;
        const range = getPromptSelection(event.currentTarget) ?? lastRange.current;
        commit(readPromptDom(event.currentTarget), range);
      }}
      onBeforeInput={event => {
        beforeRange.current = selection();
        const type = (event.nativeEvent as InputEvent).inputType;
        if (type === 'historyUndo' || type === 'historyRedo') {event.preventDefault(); undo(type === 'historyRedo');}
        if (!composing.current && (type === 'deleteContentBackward' || type === 'deleteContentForward')) {
          event.preventDefault(); replace(atomicPromptRange(value, selection(), type === 'deleteContentBackward' ? 'backward' : 'forward'), '');
        }
      }}
      onKeyDown={event => {
        onKeyDown(event);
        if (disabled || event.defaultPrevented || composing.current || event.nativeEvent.isComposing) return;
        if ((event.ctrlKey || event.metaKey) && ['z', 'y'].includes(event.key.toLowerCase())) {
          event.preventDefault(); undo(event.shiftKey || event.key.toLowerCase() === 'y');
        } else if (event.key === 'Enter') {event.preventDefault(); replace(selection(), '\n');}
        else if (event.key === 'Backspace' || event.key === 'Delete') {
          event.preventDefault(); replace(atomicPromptRange(value, selection(), event.key === 'Backspace' ? 'backward' : 'forward'), '');
        }
      }}
      onKeyUp={notifySelection} onMouseUp={notifySelection}
      onCompositionStart={() => {beforeRange.current = selection(); composing.current = true; onComposing(true);}}
      onCompositionEnd={event => {composing.current = false; onComposing(false); commit(readPromptDom(event.currentTarget), getPromptSelection(event.currentTarget) ?? lastRange.current);}}
      onPaste={event => {
        event.preventDefault();
        if (disabled) return;
        const files = [...event.clipboardData.files];
        if (files.length) onFiles(files);
        else replace(selection(), event.clipboardData.getData('text/plain'));
      }}
      onCopy={event => {event.preventDefault(); const range = selection(); event.clipboardData.setData('text/plain', value.slice(range.start, range.end));}}
      onCut={event => {event.preventDefault(); const range = selection(); event.clipboardData.setData('text/plain', value.slice(range.start, range.end)); if (!disabled) replace(range, '');}}
      onDragOver={event => event.preventDefault()}
      onDrop={event => {event.preventDefault(); if (!disabled && event.dataTransfer.files.length) onFiles([...event.dataTransfer.files]);}}
    />
    {chips.map(({target, token}, index) => {
      const item = media.find(entry => entry.token === token);
      return createPortal(item ? <button type="button" tabIndex={-1} aria-label={`预览引用：${item.name}`} title={`${token} · ${item.name}`}
        className="prompt-canvas-chip" draggable={false} onMouseDown={event => event.preventDefault()} onClick={() => onPreview(item)}>
        <span className="prompt-canvas-chip-thumb"><ArchivedThumbnail url={item.previewUrl || item.url} type={item.type} /></span>{promptMediaLabel(item)}
      </button> : <span className="prompt-canvas-variable" title={token}>{token === '{{ScenePrompt}}' ? '场景描述' : token === '{{ReplacementScope}}' ? '替换范围' : token}</span>, target, `${token}:${index}`);
    })}
  </>;
});
