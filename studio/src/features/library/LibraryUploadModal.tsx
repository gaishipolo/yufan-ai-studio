import { useRef, useState } from 'react';
import { CheckCircle2, FileUp, Loader2, Upload, XCircle } from 'lucide-react';
import { Button, Chip, Modal, MultiSelect } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import type { LibraryTagOut } from '@/shared/api/schema';
import { usePatchAsset, useUploadAsset } from '@/entities/library/api/library.queries';

interface UploadItem {
  file: File;
  kind: 'image' | 'audio' | null;
  status: 'ready' | 'uploading' | 'done' | 'failed';
  ratio: number;
  error?: string;
}

export interface LibraryUploadModalProps {
  open: boolean;
  tags: LibraryTagOut[];
  onClose: () => void;
}

const MAX_BYTES = 200 * 1024 * 1024;

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

  const reset = () => {
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
      const kind = file.type.startsWith('image/')
        ? 'image'
        : file.type.startsWith('audio/')
          ? 'audio'
          : null;
      if (!kind) {
        return { file, kind, status: 'failed', ratio: 0, error: '只支持图片与音频；视频请走模板库原片入口' };
      }
      if (file.size > MAX_BYTES) {
        return { file, kind, status: 'failed', ratio: 0, error: '单个文件不能超过 200 MB' };
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

    for (const target of targets) {
      const index = items.indexOf(target);
      setItems((previous) =>
        previous.map((item, i) => (i === index ? { ...item, status: 'uploading', ratio: 0 } : item)),
      );
      try {
        const asset = await upload.mutateAsync({
          file: target.file,
          kind: target.kind as 'image' | 'audio',
          onProgress: (ratio) =>
            setItems((previous) => previous.map((item, i) => (i === index ? { ...item, ratio } : item))),
        });
        if (tagIds.length) {
          await patch.mutateAsync({ id: asset.id, body: { tagIds } });
        }
        done += 1;
        setItems((previous) =>
          previous.map((item, i) =>
            i === index ? { ...item, status: 'done', ratio: 1 } : item,
          ),
        );
      } catch (error) {
        failed += 1;
        const message = error instanceof Error ? error.message : '上传失败';
        setItems((previous) =>
          previous.map((item, i) =>
            i === index ? { ...item, status: 'failed', error: message } : item,
          ),
        );
      }
    }

    setBusy(false);
    if (done) toast(`已上传 ${done} 个文件`, 'success');
    if (failed) toast(`${failed} 个文件没上传成功`, 'error');
    if (!failed) {
      /* 全部成功才自动关闭：有失败时留着让用户看清是哪一条 */
      setTimeout(() => {
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
          accept="image/*,audio/*"
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
          <span className="text-[12.5px] font-medium">选择图片或音频</span>
          <span className="text-[11px] text-ink-mute">可多选；单个不超过 200 MB，相同内容自动秒传</span>
        </button>

        <div>
          <div className="mb-1 text-[11.5px] text-ink-soft">给这批文件统一打标签（可选）</div>
          <MultiSelect
            options={tags.map((tag) => ({ value: tag.id, label: tag.name }))}
            value={tagIds}
            onChange={setTagIds}
            placeholder="未选择标签"
            emptyText="还没有标签"
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
                  <span className="min-w-0 flex-1 truncate text-[12px] text-ink">{item.file.name}</span>
                  <span className="text-[11px] text-ink-mute">{formatBytes(item.file.size)}</span>
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
                  <p className="mt-1 text-[11px] text-danger">{item.error}</p>
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
