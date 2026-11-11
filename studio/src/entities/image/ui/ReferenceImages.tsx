/**
 * 参考图控件（图生图 / 多图合成）。
 *
 * 刻意**不**塞进 `ParamFields`：参数区完全由 `paramSchema` 渲染，值只能是
 * 字符串 / 数字 / 布尔；参考图是一批图片，走的是提交接口上的独立字段
 * （`referenceLibraryIds`），校验规则也完全不同（要查资产、要读字节）。
 *
 * 是否显示由模型的 `maxReferences` 决定，不是所有生图模型都有——给纯文生图
 * 模型画一个上传框，用户传了不会被用上，图照样出、只是完全不像参考图。
 *
 * 顺序有意义：多图合成时提示词里的「第一张 / 第二张」指的就是这个顺序，
 * 所以缩略图按提交顺序排列，且不允许拖乱（要换顺就删掉重传）。
 */
import { useRef } from 'react';
import { ImagePlus, Loader2, X } from 'lucide-react';
import { CoverImage } from '@/shared/ui';

export interface ReferenceItem {
  id: string;
  name: string;
  /** 后端拼好的取件地址（相对 API 前缀），直接给 `<img>` 用 */
  url: string;
}

export interface ReferenceImagesProps {
  items: ReferenceItem[];
  /** 模型声明的张数上限 */
  max: number;
  busy?: boolean;
  onPick: (files: FileList | null) => void;
  onRemove: (id: string) => void;
}

/** 只收供应商认的三种格式：png / jpeg / webp（与后端白名单一致） */
const ACCEPT = 'image/png,image/jpeg,image/webp';

export function ReferenceImages({
  items,
  max,
  busy = false,
  onPick,
  onRemove,
}: ReferenceImagesProps) {
  const input = useRef<HTMLInputElement>(null);
  const full = items.length >= max;

  return (
    <div className="mb-2.5">
      <div className="mb-1.5 flex items-baseline gap-1.5 text-[11.5px] text-ink-soft">
        参考图
        <span className="text-[10.5px] text-ink-mute">
          最多 {max} 张，按上传顺序生效（提示词里的「第一张」指的就是第一张）
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {items.map((item, index) => (
          <div
            key={item.id}
            className="group relative h-16 w-16 overflow-hidden rounded-lg border border-line-soft"
          >
            <CoverImage src={item.url} seed={item.id} alt={item.name} className="h-full w-full" />
            <span className="absolute bottom-0 left-0 right-0 bg-black/45 px-1 text-[10px] text-white">
              {index + 1}
            </span>
            <button
              type="button"
              aria-label={`移除参考图 ${index + 1}`}
              onClick={() => onRemove(item.id)}
              className="absolute right-0.5 top-0.5 rounded-full bg-black/50 p-0.5 text-white opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
            >
              <X size={11} />
            </button>
          </div>
        ))}

        {!full && (
          <button
            type="button"
            disabled={busy}
            onClick={() => input.current?.click()}
            className="flex h-16 w-16 flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-line text-[10px] text-ink-mute transition hover:border-primary-line hover:text-primary-deep disabled:opacity-60"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />}
            {busy ? '上传中' : '添加'}
          </button>
        )}
      </div>

      {/* 隐藏的 file input：点「添加」触发，选完立刻清空 value，
          否则同一张图连续选两次不会触发 onChange */}
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        multiple
        className="hidden"
        aria-label="上传参考图"
        onChange={(event) => {
          onPick(event.target.files);
          event.target.value = '';
        }}
      />
    </div>
  );
}
