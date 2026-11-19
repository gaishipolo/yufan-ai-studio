import { Button } from '@/shared/ui';

/**
 * 权限勾选列表上方的一行摘要：已选几个 + 「保存后整组替换」+ 全部清空。
 *
 * 「保存后整组替换」是**约束**，不是解释 —— 取消勾选某个码再保存，
 * 这一层就真的不再授予它了（不是「只是这次不勾」）。用户不看输入框猜不到，
 * 所以必须留。
 *
 * 之前它被手抄了三份（`PermissionPicker` / `RolePermissionsModal` /
 * `GroupEditorModal` 的成员页），措辞已经开始漂移。抽到这里只留一份，
 * 三个地方共用同一个组件 —— 约束只有一处，就只可能被改坏一次。
 *
 * `unit` 换的是**对象**（权限点 / 成员），不是文案风格：
 * 「已选 5 个权限点」与「已选 3 名成员」说的是两件事，不能用一个复数硬套。
 *
 * `breakdown` 是**分层计数**（页面 / 接口各几个）。权限点分成两层之后，
 * 只报一个总数会让人看不出「3 个页面 + 2 个接口」和「5 个接口」的差别 ——
 * 而这两者后果完全不同（前者决定看得见哪一页，后者决定能做什么）。
 */
export function PermissionSummaryBar({
  count,
  unit,
  breakdown,
  onClear,
}: {
  count: number;
  /** 计数的单位，如「个权限点」「名成员」 */
  unit: string;
  /** 分层计数，如「页面 3」「接口 12」 */
  breakdown?: { label: string; count: number }[];
  onClear: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-meta text-ink-mute">
      <span>
        已选 <span className="font-semibold tabular-nums text-ink">{count}</span> {unit}
      </span>
      {breakdown?.map((part) => (
        <span key={part.label} className="flex items-center gap-1">
          <span className="text-line-strong">·</span>
          {part.label}{' '}
          <span className="font-semibold tabular-nums text-ink">{part.count}</span>
        </span>
      ))}
      <span className="text-line-strong">·</span>
      <span>保存后整组替换</span>
      <Button
        size="sm"
        variant="ghost"
        className="ml-auto"
        disabled={count === 0}
        onClick={onClear}
      >
        全部清空
      </Button>
    </div>
  );
}
