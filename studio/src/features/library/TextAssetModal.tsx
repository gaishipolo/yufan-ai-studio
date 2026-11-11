import { useEffect, useState } from 'react';
import { FileText, Save } from 'lucide-react';
import { Button, Modal, Select, TextInput } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { errorHttpStatus } from '@/shared/api/types';
import type { TextAssetIn, TextAssetUpdateIn } from '@/shared/api/schema';
import {
  useCreateTextAsset,
  useReadText,
  useUpdateText,
} from '@/entities/library/api/library.queries';

export interface TextAssetModalProps {
  open: boolean;
  /** create = 站内新建；edit = 在线编辑已有资产 */
  mode: 'create' | 'edit';
  /** edit 时必填 */
  assetId?: string;
  assetName?: string;
  onClose: () => void;
  onSaved?: () => void;
}

/** 409 既可能是业务码 409xx，也可能是 mock 直接透传的 HTTP 409，统一按状态段判定 */
function isConflict(error: Error): boolean {
  const code = (error as { code?: number }).code;
  return typeof code === 'number' && errorHttpStatus(code) === 409;
}

/**
 * 文本资产的「新建 / 编辑」共用一个弹窗。
 *
 * 编辑时先读 `GET /library/assets/{id}/text` 拿正文与**内容版本**，保存时把它
 * 作为 `expectedVersion` 带回去：两个人同时打开编辑框，后保存的人要看到明确的
 * 版本冲突提示，而不是静默覆盖掉对方的正文。
 *
 * 正文一律按纯文本处理（不渲染 Markdown），后缀只给 md / txt 两档。
 */
export function TextAssetModal({
  open,
  mode,
  assetId,
  assetName,
  onClose,
  onSaved,
}: TextAssetModalProps) {
  const isEdit = mode === 'edit';
  const read = useReadText(isEdit ? assetId ?? null : null, open && isEdit);
  const create = useCreateTextAsset();
  const update = useUpdateText();

  const [name, setName] = useState('');
  const [extension, setExtension] = useState<'md' | 'txt'>('md');
  const [content, setContent] = useState('');
  const [note, setNote] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [error, setError] = useState('');

  /* 打开时把本地态复位；正文读回来后填进输入框（依赖只在数据变化时更新，
     不会在用户打字过程中把内容冲掉）。 */
  useEffect(() => {
    if (!open) return;
    setError('');
    setNote('');
    if (isEdit) {
      setContent(read.data?.content ?? '');
      setVersion(read.data?.version ?? null);
    } else {
      setName('');
      setExtension('md');
      setContent('');
      setVersion(null);
    }
  }, [open, isEdit, read.data?.content, read.data?.version]);

  const busy = create.isPending || update.isPending || (isEdit && read.isPending);

  const submit = () => {
    setError('');
    if (!content.trim()) {
      setError('正文不能为空');
      return;
    }

    if (isEdit) {
      if (!assetId) return;
      const body: TextAssetUpdateIn = {
        content,
        expectedVersion: version ?? undefined,
        note: note.trim() || undefined,
      };
      update.mutate(
        { id: assetId, body },
        {
          onSuccess: () => {
            toast('已保存为新版本', 'success');
            onSaved?.();
            onClose();
          },
          onError: (e) =>
            setError(
              isConflict(e)
                ? '正文已被其他编辑改过（版本冲突），请重新打开编辑框再保存'
                : e.message,
            ),
        },
      );
      return;
    }

    if (!name.trim()) {
      setError('请填写名称');
      return;
    }
    const body: TextAssetIn = { name: name.trim(), content, extension };
    create.mutate(body, {
      onSuccess: () => {
        toast('已新建文本资产', 'success');
        onSaved?.();
        onClose();
      },
      onError: (e) => setError(e.message),
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? `编辑文本 · ${assetName ?? ''}` : '新建文本资产'}
      width={620}
    >
      <div className="space-y-3 p-[18px]">
        {isEdit ? (
          <div className="flex items-center gap-2 text-[12.5px] text-ink-mute">
            <FileText size={14} />
            <span>当前版本 v{version ?? '—'}</span>
            {read.isPending && <span>正在读取正文…</span>}
            {read.isError && <span className="text-danger">正文读取失败，请关闭后重试</span>}
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-2.5">
            <div className="min-w-[200px] flex-1">
              <div className="mb-1 text-[13.5px] text-ink-soft">名称</div>
              <TextInput
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例：分镜脚本"
                aria-label="文本资产名称"
              />
            </div>
            <div className="w-[120px]">
              <div className="mb-1 text-[13.5px] text-ink-soft">格式</div>
              <Select
                block
                options={[
                  { value: 'md', label: '.md' },
                  { value: 'txt', label: '.txt' },
                ]}
                value={extension}
                onChange={(event) => setExtension(event.target.value as 'md' | 'txt')}
                aria-label="文本格式"
              />
            </div>
          </div>
        )}

        <div>
          <div className="mb-1 flex items-center justify-between text-[13.5px] text-ink-soft">
            <span>正文（纯文本）</span>
            <span className="text-[11px] text-ink-mute">{content.length} 字</span>
          </div>
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            rows={12}
            placeholder="直接写正文；不会做 Markdown → HTML 渲染"
            aria-label="文本正文"
            className="w-full resize-y rounded-lg border border-line bg-panel-soft px-2.5 py-2 font-mono text-[12.5px] leading-relaxed text-ink outline-none transition placeholder:text-[#a9b8bf] focus:border-primary-line focus:bg-white focus:shadow-ring"
          />
        </div>

        {isEdit && (
          <div>
            <div className="mb-1 text-[13.5px] text-ink-soft">版本备注（选填）</div>
            <TextInput
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="例：补充第二段的场景描述"
              aria-label="版本备注"
            />
          </div>
        )}

        {error && (
          <p className="rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1.5 text-[13px] text-danger-deep" role="alert">
            {error}
          </p>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <span className="min-w-0 flex-1 truncate text-[11px] text-ink-mute">
            {isEdit ? '保存会追加一个新版本，历史版本仍可恢复' : 'UTF-8 纯文本，最多 1 MB'}
          </span>
          <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            size="sm"
            variant="primary"
            icon={<Save size={13} />}
            loading={busy}
            disabled={isEdit && read.isPending}
            onClick={submit}
          >
            {isEdit ? '保存' : '创建'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
