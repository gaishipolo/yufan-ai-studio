import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileUp, Loader2, Upload, XCircle } from 'lucide-react';
import { Button, Chip, Modal, MultiSelect } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { usePatchAsset, useUploadAsset } from '@/entities/library/api/library.queries';
import { tagOptions } from '@/entities/library/model/tagTree';
import {
  MAX_BYTES,
  TEXT_MAX_BYTES,
  UPLOAD_ACCEPT,
  uploadKindOf,
} from '@/entities/library/model/uploadLimits';

interface UploadItem {
  file: File;
  kind: 'image' | 'audio' | 'text' | null;
  status: 'ready' | 'uploading' | 'done' | 'failed';
  ratio: number;
  error?: string;
  /** 文件已经在库里、只是标签没打上：与「上传失败」分开说 */
  tagError?: string;
}

export interface LibraryUploadModalProps {
  open: boolean;
  tags: LibraryTagStatOut[];
  onClose: () => void;
}

/**
 * 上传弹窗：一次挑多个文件，逐条上传、逐条给结果。
 *
 * **逐条**而不是「一个请求带多个文件」：后端上传接口是单文件流式，前端并发
 * 三条即可；一条失败不影响其余（与批量写同一个原则），失败原因就地显示，
 * 不让用户猜是哪个文件出了问题。
 */
export function LibraryUploadModal({ open, tags, onClose }: LibraryUploadModalProps) {
  const input = useRef<HTMLInputElement>(null);
  const upload = useUploadAsset();
  const patch = usePatchAsset();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [tagIds, setTagIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  /* 上传成功后的自动关闭定时器：**必须存下来**。
     600ms 内点「关闭」再重新打开，旧定时器照样触发 → 刚选的文件无声消失、
     弹窗自己关掉。关闭 / 卸载时都清掉。 */
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearCloseTimer = () => {
    if (closeTimer.current === null) return;
    clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };

  /* 卸载时清掉：否则定时器在组件没了之后还会跑一次 reset + onClose */
  useEffect(() => clearCloseTimer, []);

  const reset = () => {
    clearCloseTimer();
    setItems([]);
    setTagIds([]);
    setBusy(false);
    if (input.current) input.current.value = '';
  };

  const close = () => {
    if (busy) return;
    reset();
    onClose();
  };

  const pick = (files: FileList | null) => {
    const next: UploadItem[] = Array.from(files ?? []).map((file) => {
      /* 按**扩展名**判类型，与后端白名单同一份：
         `file.type` 由浏览器随意给（.md 常给空串、.webp 常给 octet-stream），
         照它判会把服务端肯收的合法文件先拒掉，也会放过 .gif/.svg 这种服务端
         必然拒的。 */
      const kind = uploadKindOf(file.name);
      if (!kind) {
        return { file, kind, status: 'failed', ratio: 0, error: '只支持 .png/.jpg/.jpeg/.webp 图片、.mp3/.wav 音频与 .txt/.md 文本；视频请走模板库原片入口' };
      }
      if (kind === 'text' && file.size > TEXT_MAX_BYTES) {
        return { file, kind, status: 'failed', ratio: 0, error: '文本文件不能超过 1 MB（UTF-8 纯文本）' };
      }
      if (kind !== 'text' && file.size > MAX_BYTES) {
        return { file, kind, status: 'failed', ratio: 0, error: '单个文件不能超过 300 MB' };
      }
      return { file, kind, status: 'ready', ratio: 0 };
    });
    setItems((previous) => [...previous, ...next]);
  };

  const start = async () => {
    const targets = items.filter((item) => item.status === 'ready');
    if (!targets.length) return;
    setBusy(true);
    let done = 0;
    let failed = 0;
    let tagFailed = 0;

    for (const target of targets) {
      const index = items.indexOf(target);
      setItems((previous) =>
        previous.map((item, i) => (i === index ? { ...item, status: 'uploading', ratio: 0 } : item)),
      );

      let assetId: string;
      try {
        const asset = await upload.mutateAsync({
          file: target.file,
          kind: target.kind as 'image' | 'audio' | 'text',
          onProgress: (ratio) =>
            setItems((previous) => previous.map((item, i) => (i === index ? { ...item, ratio } : item))),
        });
        assetId = asset.id;
      } catch (error) {
        failed += 1;
        const message = error instanceof Error ? error.message : '上传失败';
        setItems((previous) =>
          previous.map((item, i) =>
            i === index ? { ...item, status: 'failed', error: message } : item,
          ),
        );
        continue;
      }

      /* 打标签是**第二段**：文件此时已经在库里了。
         把它并进上传的 try 里，一旦 PATCH 失败就会被整条报成「上传失败」，
         用户重传 → 库里多一条重复素材，而标签其实一个也没打上。 */
      let tagError = '';
      if (tagIds.length) {
        try {
          await patch.mutateAsync({ id: assetId, body: { tagIds } });
        } catch (error) {
          tagFailed += 1;
          tagError = error instanceof Error ? error.message : '未知原因';
        }
      }

      done += 1;
      setItems((previous) =>
        previous.map((item, i) =>
          i === index ? { ...item, status: 'done', ratio: 1, tagError } : item,
        ),
      );
    }

    setBusy(false);
    if (done) toast(`已上传 ${done} 个文件`, 'success');
    if (failed) toast(`${failed} 个文件没上传成功`, 'error');
    if (tagFailed) toast(`${tagFailed} 个文件已上传，但标签没打上`, 'error');
    if (!failed && !tagFailed) {
      /* 全部成功才自动关闭：有失败时留着让用户看清是哪一条 */
      clearCloseTimer();
      closeTimer.current = setTimeout(() => {
        closeTimer.current = null;
        reset();
        onClose();
      }, 600);
    }
  };

  return (
    <Modal open={open} onClose={close} title="上传到个人库" width={620}>
      <div className="space-y-3 p-[18px]">
        <input
          ref={input}
          type="file"
          multiple
          accept={UPLOAD_ACCEPT}
          className="sr-only"
          aria-label="选择上传文件"
          onChange={(event) => {
            pick(event.target.files);
            event.target.value = '';
          }}
        />

        <button
          type="button"
          onClick={() => input.current?.click()}
          disabled={busy}
          className={cn(
            'flex w-full flex-col items-center justify-center gap-1.5 rounded-card border border-dashed px-4 py-7 transition',
            'border-line-strong bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-white',
            busy && 'cursor-not-allowed opacity-60',
          )}
        >
          <FileUp size={22} className="text-ink-mute" />
          <span className="text-body font-medium">选择图片、音频或文本</span>
          {/* 「可多选」删掉 —— `input multiple` 说的是同一件事，
              而**大小与格式上限**是猜不到的约束，要留。 */}
          <span className="text-body text-ink-mute">
            图片/音频单个 ≤ 300 MB，.txt/.md 为 UTF-8 纯文本、≤ 1 MB
          </span>
        </button>

        <div>
          {/* 「给这批文件统一打标签」= 这批文件**共用**这组标签。
              「统一」两个字是这里唯一的语义 —— 单条打标在卡片上的 ＋ 里，
              两个入口长得一样，得靠这几个字区分开。 */}
          <div className="mb-1 text-body text-ink-soft">给这批文件统一打标签（可选）</div>
          <MultiSelect
            options={tagOptions(tags)}
            value={tagIds}
            onChange={setTagIds}
            placeholder="未选择标签"
            emptyText="还没有可用标签"
            disabled={busy}
            ariaLabel="上传标签"
          />
        </div>

        {items.length > 0 && (
          <ul className="max-h-[260px] space-y-1.5 overflow-auto">
            {items.map((item, index) => (
              <li
                key={`${item.file.name}-${index}`}
                className="rounded-lg border border-line-soft bg-panel p-2"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-body text-ink">{item.file.name}</span>
                  <span className="text-body text-ink-mute">{formatBytes(item.file.size)}</span>
                  {item.status === 'uploading' && <Loader2 size={13} className="animate-spin text-primary" />}
                  {item.status === 'done' && <CheckCircle2 size={13} className="text-ok" />}
                  {item.status === 'failed' && <XCircle size={13} className="text-danger" />}
                </div>
                {item.status === 'uploading' && (
                  <div className="mt-1 h-1 overflow-hidden rounded-pill bg-line-soft">
                    <div
                      className="h-full rounded-pill bg-primary transition-all"
                      style={{ width: `${Math.round(item.ratio * 100)}%` }}
                    />
                  </div>
                )}
                {item.status === 'failed' && item.error && (
                  <p className="mt-1 text-body text-danger">{item.error}</p>
                )}
                {/* 文件已在库里，只是标签没打上：与「上传失败」分开显示 */}
                {item.status === 'done' && item.tagError && (
                  <p className="mt-1 text-body text-warn">
                    标签没打上：{item.tagError}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Chip>{items.filter((item) => item.status === 'ready').length} 个待上传</Chip>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={close} disabled={busy}>
            关闭
          </Button>
          <Button
            size="sm"
            variant="primary"
            icon={<Upload size={13} />}
            loading={busy}
            disabled={!items.some((item) => item.status === 'ready')}
            onClick={() => void start()}
          >
            开始上传
          </Button>
        </div>
      </div>
    </Modal>
  );
}
