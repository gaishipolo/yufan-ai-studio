import { ConfirmDialog } from '@/shared/ui';

export interface GenerationModelSwitchDialogProps {
  open: boolean;
  /** 已选参考素材数量；0 时不会打开这个框。 */
  referenceCount: number;
  nextModelLabel?: string;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * 换模型前确认：参考素材与参数属于当前模型，不能静默清空。
 *
 * 两个直接生成页共用同一份文案和“会丢什么”的事实清单，避免一个页面说
 * 「参考图」、另一个说「参考素材」，或者漏掉参数重置这件事。
 */
export function GenerationModelSwitchDialog({
  open,
  referenceCount,
  nextModelLabel,
  onCancel,
  onConfirm,
}: GenerationModelSwitchDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="更换模型"
      description="参考素材与已调参数属于当前模型，换模型后会一并清空。"
      details={[
        { label: '已选参考素材', value: `${referenceCount} 张` },
        { label: '新模型', value: nextModelLabel ?? '—' },
      ]}
      notice="素材本身还在素材库里，重新添加即可。"
      confirmTone="danger"
      confirmLabel="清空并更换"
      onCancel={onCancel}
      onConfirm={onConfirm}
    />
  );
}
