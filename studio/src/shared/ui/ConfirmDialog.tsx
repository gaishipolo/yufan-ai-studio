import type { ReactNode } from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import { Button } from './Button';
import { Modal } from './Modal';

export interface ConfirmDialogDetail {
  label: string;
  value: ReactNode;
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** 一句话说明「这次按下确认会发生什么」。 */
  description?: ReactNode;
  /**
   * 事实清单：这次动作的关键参数（模板、模型、素材数、计费…）。
   *
   * 二次确认的价值全在这些行上 —— 只写「确定吗？」等于让人盲按。
   */
  details?: ConfirmDialogDetail[];
  /** 提醒条：需要特别注意的那一句（如「这次会消耗积分」）。 */
  notice?: ReactNode;
  noticeTone?: 'warn' | 'danger' | 'info';
  /**
   * 确认按钮的样式：`danger` 用于**破坏性动作**（删除、下架）。
   *
   * 默认是主按钮（蓝），但「移进回收站」这种动作点下去要有红色 —— 用户
   * 扫一眼按钮就知道自己在按什么，不用读文字。
   */
  confirmTone?: 'primary' | 'danger';
  confirmLabel?: string;
  cancelLabel?: string;
  /** 提交中：确认按钮转圈并禁用，取消也禁用。 */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

const NOTICE_TONE = {
  warn: 'border-warn-line bg-warn-soft text-warn-deep',
  danger: 'border-danger-line bg-danger-soft text-danger-deep',
  info: 'border-primary-line bg-primary-soft text-primary-deep',
} as const;

/**
 * 通用二次确认框。
 *
 * 放 `shared/ui` 而不是某个 feature 里：确认动作是跨页面的（提交生成、删除、
 * 停用……），弹层的骨架（标题 / 说明 / 事实清单 / 提醒条 / 两个按钮）在各处
 * 都一样，差别只在传什么文案。业务组件自己写一遍，第二遍就开始长得不一样。
 *
 * 「事实清单」是这个组件与普通 `window.confirm` 的分界：
 * 确认之前把**这次到底会发生什么**摊开写出来，用户才有得判断。
 */
export function ConfirmDialog({
  open,
  title,
  description,
  details,
  notice,
  noticeTone = 'warn',
  confirmTone = 'primary',
  confirmLabel = '确认',
  cancelLabel = '取消',
  busy,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const rows = details ?? [];

  return (
    <Modal open={open} onClose={onCancel} title={title} width={440}>
      <div className="flex flex-col gap-3 p-[18px]">
        {description && (
          <p className="text-body leading-relaxed text-ink-soft">{description}</p>
        )}

        {rows.length > 0 && (
          <dl className="overflow-hidden rounded-lg border border-line-soft">
            {rows.map((row, index) => (
              <div
                key={row.label}
                className={
                  index === 0
                    ? 'flex items-start gap-3 px-2.5 py-1.5'
                    : 'flex items-start gap-3 border-t border-line-soft px-2.5 py-1.5'
                }
              >
                <dt className="w-16 shrink-0 pt-px text-meta text-ink-mute">{row.label}</dt>
                <dd className="min-w-0 flex-1 text-body text-ink">{row.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {notice && (
          <p
            className={
              'flex items-start gap-1.5 rounded-lg border px-2.5 py-2 text-body leading-relaxed ' +
              NOTICE_TONE[noticeTone]
            }
          >
            {noticeTone === 'info' ? (
              <Info size={13} className="mt-0.5 shrink-0" />
            ) : (
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            )}
            <span className="min-w-0">{notice}</span>
          </p>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button size="sm" variant={confirmTone} loading={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
