/**
 * 参考图控件（图生图 / 多图合成 / 视频参考）。
 *
 * 刻意**不**塞进 `ParamFields`：参数区完全由 `paramSchema` 渲染，值只能是
 * 字符串 / 数字 / 布尔；参考图是一批图片，走的是提交接口上的独立字段
 * （生图 `referenceLibraryIds`、视频 `referenceAssetIds`），校验规则也完全不同
 * （要查资产、要读字节）。所以它是业务控件，不是 schema 控件。
 *
 * 是否显示由调用方决定（生图看模型的 `maxReferences`，视频看工作台自己的档位数）：
 * 给一个根本不收参考图的模型画上传框，用户传了不会被用上，图照样出、只是不像参考图。
 *
 * 顺序有意义：多图合成时提示词里的「第一张 / 第二张」指的就是这个顺序，
 * 所以缩略图按提交顺序排列，且不允许拖乱（要换顺就删掉重加）。
 *
 * 三个入口：本地上传（先传进素材库换 id）、素材库悬浮栏拖进来、点「＋」加入；
 * 点击缩略图看大图（64px 上分不清角色细节，选错一张的代价是一次完整生成）。
 */
import { useRef, useState } from 'react';
import { ImagePlus, Library, Loader2, X } from 'lucide-react';
import { CoverImage } from './CoverImage';
import { Modal } from './Modal';
import { cn } from '@/shared/lib/cn';
import { hasLibraryDrag, readLibraryDrag, type LibraryAssetDrag } from '@/shared/lib/libraryDrag';

export interface ReferenceItem {
  id: string;
  name: string;
  /** 后端拼好的取件地址（相对 API 前缀），直接给 `<img>` 用 */
  url: string;
}

export interface ReferenceImagesProps {
  items: ReferenceItem[];
  /** 调用方声明的张数上限 */
  max: number;
  busy?: boolean;
  onPick: (files: FileList | null) => void;
  onRemove: (id: string) => void;
  /** 素材库拖进来 / 点「＋」加入。不传 = 只支持本地上传 */
  onAddAsset?: (asset: LibraryAssetDrag) => void;
  /** 打开素材库悬浮栏；不传 = 不显示那个按钮 */
  onOpenLibrary?: () => void;
  /** 悬浮栏当前是否展开，只影响按钮高亮 */
  libraryOpen?: boolean;
  /** 空列表时的引导文案 */
  emptyHint?: string;
}

/** 只收供应商认的三种格式：png / jpeg / webp（与后端白名单一致） */
const ACCEPT = 'image/png,image/jpeg,image/webp';

export function ReferenceImages({
  items,
  max,
  busy = false,
  onPick,
  onRemove,
  onAddAsset,
  onOpenLibrary,
  libraryOpen = false,
  emptyHint = '也可以从右侧素材库把图拖进来',
}: ReferenceImagesProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  // 预览的那张图：点缩略图打开，只为了看清细节，不影响选择
  const [preview, setPreview] = useState<ReferenceItem | null>(null);
  const full = items.length >= max;

  /**
   * 能不能接这次拖拽。
   *
   * dragover 阶段读不到载荷内容（浏览器安全限制），只能看类型；真正的上限判断
   * 在 drop 里由 `onAddAsset` 的调用方兜底（超量时它会给提示），这里先不接，
   * 免得光标显示「可放下」却什么都没发生。
   */
  const accepts = (dataTransfer: DataTransfer) =>
    !full && !!onAddAsset && hasLibraryDrag(dataTransfer.types);

  return (
    <div className="mb-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11.5px] text-ink-soft">
        {/* 标题与按钮都 shrink-0：说明文字很长，不钉住它们就会被挤成「参考」「图」两行 */}
        <span className="shrink-0 whitespace-nowrap">参考图</span>
        <span className="min-w-0 flex-1 text-[10.5px] leading-snug text-ink-mute">
          最多 {max} 张，已选 {items.length} · 顺序即提示词里的「第 N 张」
        </span>
        {onOpenLibrary && (
          <button
            type="button"
            onClick={onOpenLibrary}
            aria-pressed={libraryOpen}
            className={cn(
              'inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-[2px] text-[10.5px] transition',
              libraryOpen
                ? 'border-primary-line bg-primary-soft text-primary-deep'
                : 'border-line-soft bg-panel-soft text-ink-soft hover:border-line-strong',
            )}
          >
            <Library size={11} />
            素材库
          </button>
        )}
      </div>

      <div
        role="group"
        aria-label="参考图投放区"
        // 整块都是投放区：拖到缩略图上、空白处、「添加」按钮上都应该能落下
        onDragOver={(event) => {
          if (!accepts(event.dataTransfer)) return;
          // 不 preventDefault 浏览器就不认这是投放目标，drop 永远不会触发
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
          setDragOver(true);
        }}
        onDragLeave={(event) => {
          // 移进子元素也会触发 leave：relatedTarget 还在块内就说明没真正离开
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setDragOver(false);
        }}
        onDrop={(event) => {
          if (!accepts(event.dataTransfer)) return;
          event.preventDefault();
          setDragOver(false);
          const asset = readLibraryDrag(event.dataTransfer);
          if (asset) onAddAsset?.(asset);
        }}
        className={cn(
          'flex flex-wrap gap-2 rounded-lg border border-dashed p-1.5 transition',
          dragOver
            ? 'border-primary bg-primary-soft/50'
            : 'border-transparent hover:border-line-soft',
        )}
      >
        {items.map((item, index) => (
          <div
            key={item.id}
            className="group relative h-16 w-16 shrink-0 overflow-hidden rounded-lg border border-line-soft"
          >
            <button
              type="button"
              aria-label={`预览参考图 ${index + 1}：${item.name}`}
              onClick={() => setPreview(item)}
              className="block h-full w-full"
            >
              <CoverImage src={item.url} seed={item.id} alt={item.name} className="h-full w-full" />
            </button>
            <span className="pointer-events-none absolute bottom-0 left-0 right-0 bg-black/45 px-1 text-[10px] text-white">
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
            className="flex h-16 w-16 shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-line text-[10px] text-ink-mute transition hover:border-primary-line hover:text-primary-deep disabled:opacity-60"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />}
            {busy ? '上传中' : '添加'}
          </button>
        )}

        {!items.length && onAddAsset && (
          <span className="min-w-0 self-center text-[10.5px] leading-snug text-ink-mute">
            {emptyHint}
          </span>
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

      <Modal
        open={preview !== null}
        onClose={() => setPreview(null)}
        title={preview ? `参考图 ${items.findIndex((one) => one.id === preview.id) + 1}：${preview.name}` : ''}
        width={720}
      >
        {/* 预览只读：关掉就回到表单，不改变已选列表 */}
        {preview && (
          <div className="bg-[#0f1a1e] p-3">
            <img
              src={preview.url}
              alt={preview.name}
              className="mx-auto max-h-[70vh] w-auto max-w-full object-contain"
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
