import { useState } from 'react';
import { Send } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import type { LibraryGroupOut, ShareTarget } from '@/shared/api/schema';

export interface LibraryBatchShareValue {
  target: ShareTarget;
  groupId?: string;
  reason: string;
}

export interface LibraryBatchShareModalProps {
  count: number;
  groups: LibraryGroupOut[];
  /** `library:share:direct`：自己的资产才当场生效 */
  canDirect: boolean;
  /** 整批是不是都归我。只要有一条是别人的，那一批就只能整体走审批 */
  allOwned: boolean;
  busy?: boolean;
  onClose: () => void;
  onSubmit: (value: LibraryBatchShareValue) => void;
}

interface TargetOption {
  key: string;
  target: ShareTarget;
  groupId?: string;
  label: string;
}

/**
 * 批量共享：一批素材共享到**同一个**目标。
 *
 * 目标选项与单条共享一致（团队 / 我所在的小组）；逐条能不能生效由后端判：
 * 已共享过的、版本过期的、不在库内的会在 `BulkOut.results` 里带明细，
 * 不因为一条失败就拖累整批。这里的 Chip 说的是「整批里我自己的那些」，
 * 有一别人的条目就整批标「需审批」，免得用户以为全部立即生效。
 */
export function LibraryBatchShareModal({
  count,
  groups,
  canDirect,
  allOwned,
  busy,
  onClose,
  onSubmit,
}: LibraryBatchShareModalProps) {
  const instant = canDirect && allOwned;
  const [targetKey, setTargetKey] = useState('team');
  const [reason, setReason] = useState('');

  if (count <= 0) return null;

  const options: TargetOption[] = [
    { key: 'team', target: 'team', label: '团队（全员可见）' },
    ...groups.map((group) => ({
      key: `group:${group.id}`,
      target: 'group' as const,
      groupId: group.id,
      label: `小组 · ${group.name || group.id}`,
    })),
  ];

  const submit = () => {
    const option = options.find((item) => item.key === targetKey);
    if (!option) {
      toast('先选一个共享目标', 'error');
      return;
    }
    onSubmit({ target: option.target, groupId: option.groupId, reason: reason.trim() });
  };

  return (
    <Modal open onClose={onClose} title={`共享到… · 已选 ${count} 项`} width={520}>
      <div className="space-y-3 p-[18px]">
        <div className="space-y-1.5">
          {options.map((option) => (
            <label
              key={option.key}
              className={cn(
                'flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-body transition',
                targetKey === option.key
                  ? 'border-primary-line bg-primary-soft/40 text-ink'
                  : 'border-line-soft bg-panel text-ink-soft hover:border-line-strong',
              )}
            >
              <input
                type="radio"
                name="batch-share-target"
                checked={targetKey === option.key}
                onChange={() => setTargetKey(option.key)}
              />
              {option.label}
            </label>
          ))}
        </div>

        <div>
          <div className="mb-1 text-body text-ink-soft">理由 / 说明</div>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            placeholder="可选：说明这批素材的用途"
            className="w-full resize-y rounded-lg border border-line bg-panel-soft px-2.5 py-2 text-body leading-relaxed text-ink outline-none focus:border-primary-line focus:bg-panel focus:shadow-ring"
          />
        </div>

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Chip tone={instant ? 'ok' : 'warn'}>{instant ? '立即生效' : '需审批'}</Chip>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            size="sm"
            variant="primary"
            icon={<Send size={13} />}
            loading={busy}
            onClick={submit}
          >
            提交共享
          </Button>
        </div>
      </div>
    </Modal>
  );
}
