import { useState } from 'react';
import { Check, X } from 'lucide-react';
import { Button, Chip, Modal, Skeleton, Tabs } from '@/shared/ui';
import { formatDateTime } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import type { ShareRequestOut } from '@/shared/api/schema';
import {
  useCancelShareRequest,
  useDecideShareRequest,
  useShareRequests,
} from '@/entities/library/api/library.queries';

export interface ShareRequestsModalProps {
  open: boolean;
  onClose: () => void;
}

/**
 * 「待我审核」与「我提交的」两栏。
 *
 * 审批资格按申请的目标范围判（全站 / 团队 / 所在小组），还要查当前组成员
 * 关系，前端算不出来也不该自己算：列表已由服务端按资格收窄。这里按「服务端
 * 返回」呈现，不按权限点藏按钮——否则持细分审批码的人会看到一张永远点不动的
 * 列表，或反过来「看不到却能通过接口点」。
 */
/**
 * 申请之后资产被改过没有。
 *
 * `requestStateVersion` 是申请单入库时的快照，`stateVersion` 是资产当前版本：
 * 两者对不上就说明资产在申请之后被改过（改名、换标签、改可见性）。
 * 这时**提示**审批人，不替他决定——申请单本来就是快照，审批权在审批人手里。
 */
function isStaleRequest(item: ShareRequestOut): boolean {
  return (
    item.status === 'pending' &&
    item.requestStateVersion !== undefined &&
    item.requestStateVersion > 0 &&
    item.stateVersion !== undefined &&
    item.requestStateVersion !== item.stateVersion
  );
}

export function ShareRequestsModal({ open, onClose }: ShareRequestsModalProps) {
  const [scope, setScope] = useState<'pending' | 'mine'>('pending');
  const pending = useShareRequests('pending', open);
  const mine = useShareRequests('mine', open);
  const decide = useDecideShareRequest();
  const cancel = useCancelShareRequest();

  const query = scope === 'pending' ? pending : mine;
  const items = query.data ?? [];

  const act = (
    item: ShareRequestOut,
    action: 'approve' | 'reject' | 'cancel',
  ) => {
    const onError = (error: Error) => toast(error.message, 'error');
    if (action === 'cancel') {
      cancel.mutate(item.id, { onSuccess: () => toast('已撤回'), onError });
      return;
    }
    decide.mutate(
      {
        id: item.id,
        approve: action === 'approve',
        // 通过时带上审批人**看到**的资产版本：后端比对不一致就 409，
        // 避免「基于旧界面的审批」生效（原先固定传 0，等于把这道校验关掉了）。
        // 驳回不传版本：驳回一条过时的申请单是正当操作，不能被 409 拦住。
        body: { expectedVersion: action === 'approve' ? (item.stateVersion ?? 0) : 0 },
      },
      {
        onSuccess: () => toast(action === 'approve' ? '已通过' : '已驳回'),
        onError,
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title="共享与撤销申请" width={640}>
      <div className="space-y-3 p-[18px]">
        <Tabs
          items={[
            { value: 'pending', label: '待我审核', count: pending.data?.length },
            { value: 'mine', label: '我提交的', count: mine.data?.length },
          ]}
          value={scope}
          onChange={setScope}
        />

        {query.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : query.error ? (
          <p className="rounded-lg bg-panel-soft px-3 py-6 text-center text-body text-danger">
            {query.error.message}
          </p>
        ) : items.length ? (
          <ul className="max-h-[420px] space-y-2 overflow-auto pr-1">
            {items.map((item) => (
              <li key={item.id} className="rounded-lg border border-line-soft bg-panel p-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Chip tone={item.action === 'share' ? 'primary' : 'warn'}>
                    {item.action === 'share' ? '共享' : '撤销共享'}
                  </Chip>
                  <span className="min-w-0 flex-1 truncate text-body text-ink">
                    {item.assetName || item.assetId}
                  </span>
                  <Chip
                    tone={
                      item.status === 'approved' ? 'ok' : item.status === 'pending' ? 'warn' : 'danger'
                    }
                  >
                    {item.status === 'pending' ? '待审' : item.status === 'approved' ? '已通过' : '已驳回'}
                  </Chip>
                  {isStaleRequest(item) && <Chip tone="warn">资产已变更</Chip>}
                </div>
                <p className="mt-1 text-body text-ink-mute">
                  {item.action === 'share' ? '共享到' : '撤销'}
                  {item.target === 'team' ? '团队' : `小组 · ${item.groupName || item.groupId}`}
                  {' · 申请人 '}
                  {item.applicantName || item.applicantId}
                  {item.createdAt ? ` · ${formatDateTime(item.createdAt)}` : ''}
                </p>
                {item.reason && <p className="mt-1 text-body text-ink-soft">理由：{item.reason}</p>}

                {item.status === 'pending' && (
                  <div className="mt-2 flex items-center gap-1.5">
                    {scope === 'pending' ? (
                      <>
                        <Button
                          size="sm"
                          variant="primary"
                          icon={<Check size={12} />}
                          onClick={() => act(item, 'approve')}
                        >
                          通过
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<X size={12} />}
                          onClick={() => act(item, 'reject')}
                        >
                          驳回
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => act(item, 'cancel')}>
                        撤回
                      </Button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded-lg bg-panel-soft px-3 py-8 text-center text-body text-ink-mute">
            {scope === 'pending' ? '没有待你处理的申请。' : '你还没有提交过共享申请。'}
          </p>
        )}
      </div>
    </Modal>
  );
}
