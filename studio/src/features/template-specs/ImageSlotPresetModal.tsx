import { useEffect, useRef, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import {
  loadPresets,
  newPresetId,
  savePresets,
  SELF_REF,
  type ImageSlotPreset,
} from './imageSlotPresets';

/**
 * 图片插槽配置：维护「加槽位 → 图片 → 二级选项」里那一组预设。
 *
 * 全局共用（存浏览器本地），不属于任何一份模板草稿 —— 作者配一次，
 * 之后每一份模板封装都能用。所以这里的改动**即时落盘**，没有「保存」按钮：
 * 摆一个保存按钮又忘点，下次打开全是空的，比没有配置更糟。
 */
export function ImageSlotPresetModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [presets, setPresets] = useState<ImageSlotPreset[]>(() => loadPresets());
  const refs = useRef<Record<string, HTMLTextAreaElement | null>>({});

  /* 每次打开都重新读一遍：本地存储可能被另一个标签页改过，
     用组件挂载时那份旧数据覆盖回去等于静默丢别人的改动。 */
  useEffect(() => {
    if (open) setPresets(loadPresets());
  }, [open]);

  const change = (next: ImageSlotPreset[]) => {
    setPresets(next);
    savePresets(next);
  };

  const patch = (id: string, value: Partial<ImageSlotPreset>) =>
    change(presets.map((preset) => (preset.id === id ? {...preset, ...value} : preset)));

  /** 在文案光标处插入单张占位符 —— 手打 `{{slot:key}}` 容易写错。
      没有整组按钮：槽位恒为单张，整组引用在这儿没有语义。 */
  const insertRef = (preset: ImageSlotPreset, token: string) => {
    const area = refs.current[preset.id];
    const start = area?.selectionStart ?? preset.filledText.length;
    const end = area?.selectionEnd ?? start;
    const next = preset.filledText.slice(0, start) + token + preset.filledText.slice(end);
    patch(preset.id, {filledText: next});
    requestAnimationFrame(() => {
      area?.focus();
      area?.setSelectionRange(start + token.length, start + token.length);
    });
  };

  const add = () =>
    change([...presets, {id: newPresetId(), name: '', filledText: `参考 ${SELF_REF}`}]);

  const remove = (id: string) => {
    change(presets.filter((preset) => preset.id !== id));
    toast('已删除预设');
  };

  return (
    <Modal open={open} onClose={onClose} title="图片插槽配置" width={620}>
      <div className="space-y-3 px-[18px] py-4">
        <p className="text-body text-ink-soft">
          配置「加槽位 → 图片」下的二级选项。名称会同时作为菜单项和槽位名；
          选择素材时文案里的「插引用」表示这次选中的那张图。
        </p>

        {presets.length === 0 && (
          <p className="rounded-lg border border-dashed border-line-strong bg-panel-soft px-3 py-4 text-meta text-ink-mute">
            还没有预设。加一个，以后封装模板就不用每次手写选中时文案了。
          </p>
        )}

        {/* 卡片底用 panel-soft：弹窗本身是白的，白卡片贴白底只剩一条淡线，
            多个预设连在一起看不出「哪几行是一项」。浅灰底把每一项分出来。 */}
        <ul className="max-h-[420px] space-y-2 overflow-auto">
          {presets.map((preset, index) => (
            <li key={preset.id} className="rounded-xl border border-line-soft bg-panel-soft p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="grid size-5 shrink-0 place-items-center rounded-full bg-panel text-micro font-semibold text-ink-mute ring-1 ring-inset ring-line-soft">
                  {index + 1}
                </span>
                <input
                  aria-label={`预设 ${index + 1} 名称`}
                  value={preset.name}
                  placeholder="名称（如：场景参考）"
                  onChange={(event) => patch(preset.id, {name: event.target.value})}
                  className="min-w-0 flex-1 rounded-md border border-line bg-panel px-2 py-1 text-body text-ink outline-none transition-colors placeholder:text-ink-mute focus:border-primary-line focus:ring-2 focus:ring-primary/10"
                />
                <button
                  type="button"
                  aria-label={`删除预设 ${index + 1}`}
                  title="删除这个预设"
                  onClick={() => remove(preset.id)}
                  className="rounded-md p-1 text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger"
                >
                  <Trash2 size={13} />
                </button>
              </div>

              <textarea
                ref={(node) => {
                  refs.current[preset.id] = node;
                }}
                aria-label={`预设 ${index + 1} 选择素材时文案`}
                value={preset.filledText}
                rows={3}
                placeholder="选择素材时怎么说，如：参考「插引用」中的场景"
                onChange={(event) => patch(preset.id, {filledText: event.target.value})}
                className="w-full resize-y rounded-md border border-line bg-panel px-2 py-1.5 text-body text-ink outline-none transition-colors placeholder:text-ink-mute focus:border-primary-line focus:ring-2 focus:ring-primary/10"
              />
              <div className="mt-1.5 flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => insertRef(preset, SELF_REF)}
                  className="rounded-md border border-line-soft bg-panel-soft px-1.5 py-[2px] text-meta text-ink-soft transition hover:border-primary-line hover:text-primary"
                >
                  插引用（这次选中的图）
                </button>
              </div>
            </li>
          ))}
        </ul>

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="secondary" icon={<Plus size={13} />} onClick={add}>
            加一个预设
          </Button>
          <Button size="sm" variant="primary" className="ml-auto" onClick={onClose}>
            完成
          </Button>
        </div>
      </div>
    </Modal>
  );
}
