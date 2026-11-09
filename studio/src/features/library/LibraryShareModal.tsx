import { useEffect, useState } from 'react';
import { Send, Undo2 } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import type { LibraryAssetOut, LibraryGroupOut, ShareTarget } from '@/shared/api/schema';
import { useCreateShareRequest } from '@/entities/library/api/library.queries';

export interface LibraryShareModalProps {
  asset: LibraryAssetOut | null;
  mode: 'share' | 'revoke';
  groups: LibraryGroupOut[];
  /** `library:share:direct`：共享立即生效；撤销**始终**要走审批 */
  canDirect: boolean;
  onClose: () => void;
}

interface TargetOption {
  key: string;
  target: ShareTarget;
  groupId?: string;
  label: string;
}

/**
 * 共享 / 取消共享。
 *
 * 两者的表单长得像，语义完全不同：共享在有 `share:direct` 时立即生效，
 * 撤销则**一律**生成申请单（后端同样强制），理由是「同事正在用的素材突然
 * 拿不到」需要留痕。界面把这句话直接写在输入框下面，避免用户以为点完就生效。
 */
export function LibraryShareModal({ asset, mode, groups, canDirect, onClose }: LibraryShareModalProps) {
  const request = useCreateShareRequest();
  const [targetKey, setTargetKey] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const options: TargetOption[] = [];
  if (asset) {
    if (mode === 'share') {
      options.push({ key: 'team', target: 'team', label: '团队（全员可见）' });
      for (const group of groups) {
        options.push({ key: `group:${group.id}`, target: 'group', groupId: group.id, label: `小组 · ${group.name || group.id}` });
      }
    } else if (asset.visibility === 'team') {
      options.push({ key: 'team', target: 'team', label: '团队（全员可见）' });
    } else {
      for (const groupId of asset.groupIds ?? []) {
        const group = groups.find((item) => item.id === groupId);
        options.push({
          key: `group:${groupId}`,
          target: 'group',
          groupId,
          label: `小组 · ${group?.name || groupId}`,
        });
      }
    }
  }

  useEffect(() => {
    if (!asset) return;
    setReason('');
    setTargetKey(mode === 'share' ? 'team' : (options[0]?.key ?? ''));
    // options 由 asset/mode 派生，这里只关心打开的是哪一条资产
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asset?.id, mode]);

  if (!asset) return null;

  const submit = () => {
    const option = options.find((item) => item.key === targetKey);
    if (!option) {
      toast('先选一个共享目标', 'error');
      return;
    }
    if (mode === 'revoke' && !reason.trim()) {
      toast('撤销共享必须填写理由', 'error');
      return;
    }
    setBusy(true);
    request.mutate(
      {
        assetId: asset.id,
        action: mode,
        target: option.target,
        groupId: option.groupId,
        reason: reason.trim(),
        expectedVersion: asset.stateVersion ?? 0,
      },
      {
        onSuccess: (result) => {
          setBusy(false);
          toast(
            mode === 'revoke'
              ? '撤销申请已提交，审批通过后才会生效'
              : result.status === 'approved'
                ? '已共享'
                : '共享申请已提交，等待审核',
            'success',
          );
          onClose();
        },
        onError: (error) => {
          setBusy(false);
          toast(error.message, 'error');
        },
      },
    );
  };

  const title = mode === 'share' ? '共享到…' : '取消共享';

  return (
    <Modal open onClose={onClose} title={`${title} · ${asset.name}`} width={520}>
      <div className="space-y-3 p-[18px]">
        <div className="space-y-1.5">
          {options.length ? (
            options.map((option) => (
              <label
                key={option.key}
                className={cn(
                  'flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-[12px] transition',
                  targetKey === option.key
                    ? 'border-primary-line bg-primary-soft/40 text-ink'
                    : 'border-line-soft bg-panel text-ink-soft hover:border-line-strong',
                )}
              >
                <input
                  type="radio"
                  name="share-target"
                  checked={targetKey === option.key}
                  onChange={() => setTargetKey(option.key)}
                />
                {option.label}
              </label>
            ))
          ) : (
            <p className="rounded-lg bg-panel-soft px-3 py-3 text-[11.5px] text-ink-mute">
              这条资产还没有共享给任何小组。
            </p>
          )}
        </div>

        <div>
          <div className="mb-1 flex items-center gap-1.5 text-[11.5px] text-ink-soft">
            理由 / 说明 {mode === 'revoke' && <span className="text-danger">*</span>}
          </div>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            placeholder={
              mode === 'revoke'
                ? '例：这版人物形象已废弃，换成 v3 重新上传'
                : '可选：说明这批素材的用途'
            }
            className="w-full resize-y rounded-lg border border-line bg-panel-soft px-2.5 py-2 text-[12px] leading-relaxed text-ink outline-none focus:border-primary-line focus:bg-white focus:shadow-ring"
          />
        </div>

        <p className="rounded-lg bg-panel-soft px-2.5 py-1.5 text-[11px] leading-relaxed text-ink-mute">
          {mode === 'revoke'
            ? '撤销共享一律需要审批：同事可能正在用这条素材，审批通过后才会真正生效。'
            : canDirect
              ? '你持有直接共享权限，提交后立即生效。'
              : '共享需要小组负责人或管理员审批，提交后可在「待我审核」里查看进度。'}
        </p>

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          {mode === 'share' && <Chip tone={canDirect ? 'ok' : 'warn'}>{canDirect ? '立即生效' : '需审批'}</Chip>}
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            size="sm"
            variant="primary"
            icon={mode === 'share' ? <Send size={13} /> : <Undo2 size={13} />}
            loading={busy}
            onClick={submit}
          >
            {mode === 'share' ? '提交共享' : '提交撤销申请'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
