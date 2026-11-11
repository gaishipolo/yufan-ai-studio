import { useState } from 'react';
import { ArrowDown, ArrowUp, FileText, X } from 'lucide-react';
import { Button, Chip } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LibraryPicker } from '@/entities/library/ui/LibraryPicker';
import { displayName } from '@/entities/library/model/types';
import { retargetTextTokens } from './textRefs';

export interface TextRefPickerProps {
  /** 当前绑定的文本资产，顺序就是提示词里 {{Text N}} 的编号顺序 */
  value: LibraryAssetOut[];
  onChange: (assets: LibraryAssetOut[]) => void;
  /** 提示词当前值；插入 / 解绑时同步改写其中的 {{Text N}} */
  prompt: string;
  onPromptChange: (next: string) => void;
  /** 最多绑定份数，默认 8（与后端契约一致） */
  max?: number;
  disabled?: boolean;
}

/**
 * 「插入文本引用」入口：按钮 + 已绑定列表 + 文本资产选择器。
 *
 * 选择器只放显式 `kinds={['text']}`，图片/视频选择器默认不会混进文本（见 LibraryPicker）。
 * 绑定的增删与重排都走 `retargetTextTokens`：按 id 重编号，删中间项时后面的
 * {{Text N}} 自动前移，不会静默错位。上层提交时把 `value` 的 id 顺序放进 `textLibraryIds`。
 */
export function TextRefPicker({
  value,
  onChange,
  prompt,
  onPromptChange,
  max = 8,
  disabled,
}: TextRefPickerProps) {
  const [open, setOpen] = useState(false);

  const apply = (next: LibraryAssetOut[]) => {
    const bounded = next.slice(0, max);
    const result = retargetTextTokens(prompt, value, bounded);
    onPromptChange(result.text);
    onChange(bounded);
    if (result.dropped) {
      toast(`已移除 ${result.dropped} 处失效的 {{Text N}} 引用，其余引用已重新编号`);
    }
  };

  /** 重排也是一次 apply：retargetTextTokens 按 id 把提示词里的编号改成新位置 */
  const move = (from: number, to: number) => {
    if (to < 0 || to >= value.length) return;
    const next = [...value];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    apply(next);
  };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          icon={<FileText size={12} />}
          disabled={disabled || value.length >= max}
          onClick={() => setOpen(true)}
        >
          插入文本引用
        </Button>
        <span className="text-[11px] leading-4 text-ink-mute">
          提示词里写 {'{{Text 1}}'} / {'{{Text 2}}'}，编号对应下方顺序，最多 {max} 份
        </span>
        <Chip tone={value.length ? 'primary' : 'default'}>{value.length}/{max}</Chip>
      </div>

      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {value.map((asset, index) => (
            <li
              key={asset.id}
              className="inline-flex max-w-[240px] items-center gap-1 rounded-lg border border-line-soft bg-panel-soft px-2 py-1 text-[11px] text-ink-soft"
            >
              <span className="shrink-0 text-ink-mute">{`{{Text ${index + 1}}}`}</span>
              <span className="truncate" title={asset.name}>
                {displayName(asset.name)}
              </span>
              <span className="shrink-0 text-ink-mute">v{asset.textVersion ?? 1}</span>
              <button
                type="button"
                aria-label={`上移文本引用 ${displayName(asset.name)}`}
                disabled={disabled || index === 0}
                onClick={() => move(index, index - 1)}
                className="text-ink-mute hover:text-primary disabled:opacity-30"
              >
                <ArrowUp size={11} />
              </button>
              <button
                type="button"
                aria-label={`下移文本引用 ${displayName(asset.name)}`}
                disabled={disabled || index === value.length - 1}
                onClick={() => move(index, index + 1)}
                className="text-ink-mute hover:text-primary disabled:opacity-30"
              >
                <ArrowDown size={11} />
              </button>
              <button
                type="button"
                aria-label={`解绑文本引用 ${displayName(asset.name)}`}
                onClick={() => apply(value.filter((item) => item.id !== asset.id))}
                className="text-ink-mute hover:text-danger"
              >
                <X size={11} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* 条件挂载：没打开就不挂载 LibraryPicker，避免无谓的 selection 查询 */}
      {open && (
        <LibraryPicker
          open
          title="选择文本资产"
          multiple
          kinds={['text']}
          value={value.map((asset) => asset.id)}
          onClose={() => setOpen(false)}
          onConfirm={(assets) => {
            apply(assets);
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}
