import { useEffect, useState, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';

export interface LibraryConfirmModalProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  /** 必填理由：管理员强制删除、驳回草稿、撤销共享都用它 */
  requireReason?: boolean;
  reasonLabel?: string;
  reasonPlaceholder?: string;
  busy?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}

/** 统一确认弹窗：不可逆动作、需要留痕的动作都走这一处，措辞随场景传入 */
export function LibraryConfirmModal({
  open,
  title,
  description,
  confirmLabel = '确认',
  danger,
  requireReason,
  reasonLabel = '理由',
  reasonPlaceholder,
  busy,
  onClose,
  onConfirm,
}: LibraryConfirmModalProps) {
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (open) setReason('');
  }, [open]);

  const blocked = !!requireReason && !reason.trim();

  return (
    <Modal open={open} onClose={onClose} title={title} width={460}>
      <div className="space-y-3 p-[18px]">
        <div className="flex items-start gap-2 rounded-lg bg-panel-soft px-2.5 py-2 text-[13.5px] leading-relaxed text-ink-soft">
          {danger && <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" />}
          <div className="min-w-0">{description}</div>
        </div>

        {requireReason && (
          <div>
            <div className="mb-1 text-[13.5px] text-ink-soft">
              {reasonLabel} <span className="text-danger">*</span>
            </div>
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={3}
              placeholder={reasonPlaceholder}
              className="w-full resize-y rounded-lg border border-line bg-panel-soft px-2.5 py-2 text-[14px] leading-relaxed text-ink outline-none focus:border-primary-line focus:bg-white focus:shadow-ring"
              aria-label={reasonLabel}
            />
          </div>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            size="sm"
            variant={danger ? 'danger' : 'primary'}
            loading={busy}
            disabled={blocked}
            onClick={() => onConfirm(reason.trim())}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
