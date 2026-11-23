/**
 * 参考图控件（图生图 / 多图合成 / 视频参考）。
 *
 * 刻意**不**塞进 `ParamFields`：参数区完全由 `paramSchema` 渲染，值只能是
 * 字符串 / 数字 / 布尔；参考图是一批图片，走的是提交接口上的独立字段
 * （生图与视频都是 `referenceLibraryIds`，两边的校验规则也不同：生图要读字节交给
 * 供应商，视频只如实记录用了哪几张）。所以它是业务控件，不是 schema 控件。
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
import { kindOf, metaOf } from './reference-kinds';
import { cn } from '@/shared/lib/cn';
import { hasLibraryDrag, readLibraryDrag, type LibraryAssetDrag } from '@/shared/lib/libraryDrag';

export interface ReferenceItem {
  id: string;
  name: string;
  /** 后端拼好的取件地址（相对 API 前缀），直接给 `<img>` 用 */
  url: string;
  /**
   * 素材类型。缺省按图片理解（生图页只有图片）。
   *
   * 必须有：提示词里的编号是**按类型各自从 1 开始**的（`<Picture 1>` /
   * `<Audio 1>`），界面角标要跟它一致，否则用户看到「3」而提示词里要写
   * 「第 2 张图」，指代整体错位 —— 出片正常，只是内容不对。
   */
  kind?: string;
}

export interface ReferenceImagesProps {
  items: ReferenceItem[];
  /** 调用方声明的**总数**上限（图片 + 音频加起来） */
  max: number;
  /**
   * 这个模型能收哪几类（`image` / `audio` …）。缺省只有图片。
   *
   * 决定标题、文件选择器的 accept、以及下面按类的余量提示。
   */
  kinds?: string[];
  /**
   * 按类型的上限：`{ image: 5, audio: 3 }`。缺省 = 供应商没声明，
   * 只守总数。
   */
  limits?: Record<string, number>;
  busy?: boolean;
  onPick: (files: FileList | null) => void;
  onRemove: (id: string) => void;
  /** 素材库拖进来 / 点「＋」加入。不传 = 只支持本地上传 */
  onAddAsset?: (asset: LibraryAssetDrag) => void;
  /** 打开素材库悬浮栏；不传 = 不显示那个按钮 */
  onOpenLibrary?: () => void;
  /** 悬浮栏当前是否展开，只影响按钮高亮 */
  libraryOpen?: boolean;
}

/** 各类型素材在界面上的称呼与可上传格式见 `reference-kinds.ts`（调用点也要用）。 */
const IMAGE_ONLY = ['image'];

export function ReferenceImages({
  items,
  max,
  kinds = IMAGE_ONLY,
  limits,
  busy = false,
  onPick,
  onRemove,
  onAddAsset,
  onOpenLibrary,
  libraryOpen = false,
}: ReferenceImagesProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  // 预览的那张图：点缩略图打开，只为了看清细节，不影响选择
  const [preview, setPreview] = useState<ReferenceItem | null>(null);
  const full = items.length >= max;

  /** 只收图片时用老文案（「参考图」「第 N 张」），别把生图页的说法也改掉。 */
  const imageOnly = kinds.length === 1 && kinds[0] === 'image';
  const counts: Record<string, number> = {};
  for (const item of items) {
    const kind = kindOf(item);
    counts[kind] = (counts[kind] ?? 0) + 1;
  }
  const kindFull = (kind: string) => {
    const limit = limits?.[kind];
    return limit !== undefined && (counts[kind] ?? 0) >= limit;
  };
  // 总数没满、且至少还有一类有余量才给「添加」：图片满了但音频没满时，
  // 按钮必须还在（accept 里音频照样选得到）
  const canAdd = !full && kinds.some((kind) => !kindFull(kind));
  const accept =
    kinds.flatMap((kind) => metaOf(kind).accept).join(',') ||
    metaOf('image').accept.join(',');

  /** 这个素材在**它自己那一类**里是第几个（单类时就是全局序号）。 */
  const nthOf = (item: ReferenceItem, index: number): number => {
    if (imageOnly) return index + 1;
    const kind = kindOf(item);
    let nth = 0;
    for (let i = 0; i <= index; i += 1) if (kindOf(items[i]) === kind) nth += 1;
    return nth;
  };

  /** 角标：只收图片时是全局序号；多类时按类型各自编号，与提示词里的指代对齐。 */
  const badgeOf = (item: ReferenceItem, index: number): string => {
    if (imageOnly) return String(index + 1);
    return `${metaOf(kindOf(item)).short}${nthOf(item, index)}`;
  };

  /** 单个素材的称呼（`参考图 1` / `图 2`），按钮的无障碍名与预览标题都用它。 */
  const itemLabel = (item: ReferenceItem, index: number): string => {
    if (imageOnly) return `参考图 ${index + 1}`;
    return `${metaOf(kindOf(item)).label} ${nthOf(item, index)}`;
  };

  const previewTitle = (item: ReferenceItem): string => {
    const index = items.findIndex((one) => one.id === item.id);
    return `${itemLabel(item, index)}：${item.name}`;
  };

  /* 第二行只留「数得出来」和「看不见会踩坑」的信息：各类的余量、编号规则。
     单类时保持原来的说法，免得把生图页的布局也一起动。 */
  const summary = imageOnly
    ? `最多 ${max} 张，已选 ${items.length} · 顺序即「第 N 张」`
    : `${kinds
        .map((kind) => {
          const limit = limits?.[kind];
          return `${metaOf(kind).label} ${counts[kind] ?? 0}${limit ? `/${limit}` : ''}`;
        })
        .join(' · ')} · 编号按类型各自计数`;

  /**
   * 能不能接这次拖拽。
   *
   * dragover 阶段读不到载荷内容（浏览器安全限制），只能看类型；真正的上限判断
   * 在 drop 里由 `onAddAsset` 的调用方兜底（超量时它会给提示），这里先不接，
   * 免得光标显示「可放下」却什么都没发生。
   */
  const accepts = (dataTransfer: DataTransfer) =>
    !full && !!onAddAsset && hasLibraryDrag(dataTransfer.types);

  /**
   * 从操作系统拖进来的文件（不是素材库里的资产）。
   *
   * 必须认这一类：投放区看起来就是个虚线框，用户从桌面把图拖进来是最自然的动作。
   * 不接住的话浏览器按默认动作打开 `file:///…`，**整页导航**——还没提交的提示词、
   * 参数、已选的参考图全丢，且没有任何提示。
   */
  const carriesFiles = (dataTransfer: DataTransfer) =>
    Array.from(dataTransfer.types ?? []).includes('Files');

  return (
    <div className="mb-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-meta text-ink-soft">
        {/* 标题与按钮都 shrink-0：说明文字很长，不钉住它们就会被挤成「参考」「图」两行 */}
        <span className="shrink-0 whitespace-nowrap">{imageOnly ? '参考图' : '参考素材'}</span>
        {/* 这一行只留「数得出来」和「看不见会踩坑」的信息：
            每类还剩几个、编号规则。空列表时那句「也可以从右侧素材库把图拖进来」
            是把旁边的按钮又念了一遍 —— 按钮就在那儿，不用替它说话。 */}
        <span className="min-w-0 flex-1 text-micro leading-snug text-ink-mute">{summary}</span>
        {onOpenLibrary && (
          <button
            type="button"
            onClick={onOpenLibrary}
            aria-pressed={libraryOpen}
            className={cn(
              'inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-[2px] text-micro transition',
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
        aria-label={imageOnly ? '参考图投放区' : '参考素材投放区'}
        // 整块都是投放区：拖到缩略图上、空白处、「添加」按钮上都应该能落下
        onDragOver={(event) => {
          // 文件先拦：只要不 preventDefault，浏览器就会把这次投放当成「打开文件」，
          // drop 永远不会到我们手上，页面直接跳走
          if (carriesFiles(event.dataTransfer)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
            setDragOver(!full);
            return;
          }
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
          // 桌面上拖来的文件直接走「本地上传」那条路（先入库换 id）：让它落进
          // 一个真正能用的入口，比只弹一句「请先上传到素材库」少两步
          const files = event.dataTransfer.files;
          if (files?.length) {
            event.preventDefault();
            setDragOver(false);
            onPick(files);
            return;
          }
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
              aria-label={`预览${previewTitle(item)}`}
              onClick={() => setPreview(item)}
              className="block h-full w-full"
            >
              <CoverImage src={item.url} seed={item.id} alt={item.name} className="h-full w-full" />
            </button>
            <span className="pointer-events-none absolute bottom-0 left-0 right-0 bg-black/45 px-1 text-micro text-white">
              {badgeOf(item, index)}
            </span>
            <button
              type="button"
              aria-label={`移除${itemLabel(item, index)}`}
              onClick={() => onRemove(item.id)}
              className="absolute right-0.5 top-0.5 rounded-full bg-black/50 p-0.5 text-white opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
            >
              <X size={11} />
            </button>
          </div>
        ))}

        {canAdd && (
          <button
            type="button"
            disabled={busy}
            onClick={() => input.current?.click()}
            className="flex h-16 w-16 shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-line text-micro text-ink-mute transition hover:border-primary-line hover:text-primary-deep disabled:opacity-60"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />}
            {busy ? '上传中' : '添加'}
          </button>
        )}

        {!items.length && onAddAsset && (
          /* 只说「按钮上看不出来的」那一件事：从素材库拖图进来。
             原来那句还把旁边的「素材库」按钮又念了一遍 —— 按钮就在那儿。 */
          <span className="min-w-0 self-center text-micro leading-snug text-ink-mute">
            可从素材库拖入
          </span>
        )}
      </div>

      {/* 隐藏的 file input：点「添加」触发，选完立刻清空 value，
          否则同一张图连续选两次不会触发 onChange */}
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple
        className="hidden"
        aria-label={imageOnly ? '上传参考图' : '上传参考素材'}
        onChange={(event) => {
          onPick(event.target.files);
          event.target.value = '';
        }}
      />

      <Modal
        open={preview !== null}
        onClose={() => setPreview(null)}
        title={preview ? previewTitle(preview) : ''}
        width={720}
      >
        {/* 预览只读：关掉就回到表单，不改变已选列表。
            音频没有「大图」可看，给一个能试听的播放器 —— 用 `<img>` 画它只会
            显示裂图，让人以为素材坏了。 */}
        {preview && (
          <div className="bg-[#0f1a1e] p-3">
            {kindOf(preview) === 'audio' ? (
              <audio controls src={preview.url} aria-label={preview.name} className="mx-auto w-full max-w-xl">
                {/* 与 `ArchivedMedia` 同一写法：音频没有字幕可加，空轨道只为满足
                    `jsx-a11y/media-has-caption`（那条规则不区分音视频） */}
                <track kind="captions" />
              </audio>
            ) : (
              <img
                src={preview.url}
                alt={preview.name}
                className="mx-auto max-h-[70vh] w-auto max-w-full object-contain"
              />
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
