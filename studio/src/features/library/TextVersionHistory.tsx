import { useState } from 'react';
import { History, RotateCcw } from 'lucide-react';
import { Button, Chip, Modal, Skeleton } from '@/shared/ui';
import { formatDateTime } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import { isStateChanged } from '@/shared/api/types';
import {
  useReadText,
  useRestoreTextVersion,
  useTextVersion,
  useTextVersions,
} from '@/entities/library/api/library.queries';

export interface TextVersionHistoryProps {
  open: boolean;
  assetId: string;
  assetName?: string;
  onClose: () => void;
  onRestored?: () => void;
}

/**
 * 版本冲突用**共享判定**（`details.reason === 'state_changed'`），不自己判 409。
 *
 * 409 有两类：业务冲突（库里已有相同正文）与版本冲突（正文被别人改过）。
 * 只看状态码分不出来——把业务冲突也说成「版本冲突」会让用户去重开版本历史，
 * 而真正要做的却是换个名字再存一次。
 */
function isVersionConflict(error: unknown): boolean {
  return isStateChanged(error);
}

/**
 * 版本历史：列出每一版（默认不带正文），点「查看」才去读那一版的全文。
 *
 * 恢复不是「把记录拨回旧版」：服务端把旧版正文**写成新的一版**，历史行一条
 * 都不改，所以列表里会多一条最新版本。界面上必须把这句话说出来，否则用户
 * 会以为点一下就能抹掉中间几次编辑。
 */
export function TextVersionHistory({
  open,
  assetId,
  assetName,
  onClose,
  onRestored,
}: TextVersionHistoryProps) {
  const versions = useTextVersions(assetId, open);
  const current = useReadText(assetId, open);
  const restore = useRestoreTextVersion();
  const [viewing, setViewing] = useState<number | null>(null);
  const detail = useTextVersion(assetId, viewing, open);

  /* 当前版本没读到就**不能恢复**：expectedVersion 缺省时后端跳过乐观锁，
     别人刚改过的正文会被这次恢复静默覆盖。正文读取比版本列表慢，先把按钮
     锁住，等版本号到手再放行。 */
  const currentVersion = current.data?.version;
  const canRestore = currentVersion != null && !current.isPending && !current.isError;

  const restoreTo = (version: number) => {
    if (!canRestore || currentVersion == null) {
      toast('当前版本还没读到，请稍后再试', 'error');
      return;
    }
    restore.mutate(
      {
        id: assetId,
        body: { version, expectedVersion: currentVersion },
      },
      {
        onSuccess: () => {
          toast(`已把 v${version} 写成新的一版`, 'success');
          setViewing(null);
          onRestored?.();
        },
        onError: (e) =>
          toast(
            isVersionConflict(e)
              ? '正文已被其他编辑改过，已刷新，请重新打开版本历史再试'
              : e.message,
            'error',
          ),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title={`版本历史 · ${assetName ?? ''}`} width={720}>
      <div className="space-y-3 p-[18px]">
        <p className="rounded-lg border border-line-soft bg-panel-soft px-2.5 py-1.5 text-xs leading-relaxed text-ink-soft">
          恢复会把所选版本的正文<strong className="font-semibold">写成新的一版</strong>，
          不改动、也不删除任何历史版本；恢复后列表最上面会多一条最新版本。
        </p>

        {versions.isPending ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-14 w-full" />
            ))}
          </div>
        ) : versions.isError ? (
          <p className="text-body text-danger-deep">版本历史加载失败：{versions.error.message}</p>
        ) : (versions.data ?? []).length ? (
          <ul className="max-h-[320px] space-y-1.5 overflow-auto pr-1">
            {(versions.data ?? []).map((item) => (
              <li key={item.version} className="rounded-lg border border-line-soft bg-panel p-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Chip tone="primary">v{item.version}</Chip>
                  <span className="text-xs text-ink-mute">{formatDateTime(item.createdAt)}</span>
                  <span className="text-xs text-ink-mute">{item.createdBy}</span>
                  <code className="ml-auto text-meta text-ink-mute" title={item.sha256}>
                    {item.sha256.slice(0, 8)}
                  </code>
                </div>
                {item.note && <p className="mt-1 text-xs text-ink-soft">备注：{item.note}</p>}
                <p className="mt-1 line-clamp-2 whitespace-pre-wrap break-words text-xs text-ink-mute">
                  {item.excerpt || '（空文本）'}
                </p>
                <div className="mt-1.5 flex items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setViewing((previous) => (previous === item.version ? null : item.version))}
                  >
                    {viewing === item.version ? '收起正文' : '查看'}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={<RotateCcw size={12} />}
                    loading={restore.isPending}
                    disabled={!canRestore}
                    onClick={() => restoreTo(item.version)}
                  >
                    恢复到此版
                  </Button>
                </div>
                {viewing === item.version && (
                  <pre className="mt-1.5 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-panel-soft px-2.5 py-2 text-xs leading-relaxed text-ink-soft">
                    {detail.isPending ? '正在读取正文…' : detail.data?.content || '（空文本）'}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex flex-col items-center gap-1.5 py-8 text-ink-mute">
            <History size={22} />
            <span className="text-xs">还没有历史版本</span>
          </div>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <span className="min-w-0 flex-1 truncate text-meta text-ink-mute">
            当前版本 v{current.data?.version ?? '—'}
          </span>
          <Button size="sm" variant="ghost" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </Modal>
  );
}
