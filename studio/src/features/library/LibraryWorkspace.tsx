import { useMemo, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Download, PackageCheck } from 'lucide-react';
import { Button, Chip, PageHeader, Pager } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useCan, useSubject } from '@/shared/access/use-access';
import { ApiError } from '@/shared/api/types';
import type { LibraryAssetOut } from '@/shared/api/schema';
import {
  deleteOffer,
  parseView,
  viewMeta,
  type KindFilter,
  type LibrarySortKey,
  type LibraryView,
} from '@/entities/library/model/types';
import {
  useBulkAction,
  useCreateDownload,
  useDeleteAsset,
  useDraftDecision,
  useLibraryList,
  useLibraryTags,
  useMyGroups,
  usePurgeAsset,
  useRestoreAsset,
  useSaveAssetFile,
  useShareRequests,
} from '@/entities/library/api/library.queries';
import { LibraryBulkBar, type BulkKind } from './LibraryBulkBar';
import { LibraryConfirmModal } from './LibraryConfirmModal';
import { LibraryDetail } from './LibraryDetail';
import { LibraryDownloadsModal } from './LibraryDownloadsModal';
import { LibraryGrid, LibrarySelectAll } from './LibraryGrid';
import { LibraryPackModal } from './LibraryPackModal';
import { LibraryShareModal } from './LibraryShareModal';
import { LibraryToolbar } from './LibraryToolbar';
import { LibraryUploadModal } from './LibraryUploadModal';
import { ShareRequestsModal } from './ShareRequestsModal';
import type { LibraryCardAction } from './LibraryCard';

const PAGE_SIZE = 24;
/* 不含 `pack`：类型筛选里没有「封装」这一档（见 KIND_OPTIONS 注释）。
   老的 `?kind=pack` 链接会落到「全部」，而不是筛出一个空列表。 */
const KINDS = new Set(['all', 'video', 'image', 'audio']);
const SORTS = new Set(['-createdAt', 'createdAt', 'name', '-sizeBytes']);

interface ConfirmState {
  title: string;
  description: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  requireReason?: boolean;
  reasonLabel?: string;
  reasonPlaceholder?: string;
  onConfirm: (reason: string) => void;
}

/**
 * 资产库 v2 容器：URL 是筛选条件的唯一真相，页面只做编排。
 *
 * 三条约定：
 * 1. `view` / `kind` / `tags` / `q` / `sort` / `page` 全部写进 URL —— 刷新、
 *    分享、前进后退都能还原，与项目其它列表页一致；
 * 2. 写操作成功后由查询层统一失效列表与分面，容器不手工拼新数据（避免
 *    「删了还在」这类本地状态与服务器不一致）；
 * 3. 删除/驳回/强删这类需要留痕的动作统一走 `LibraryConfirmModal`，
 *    必填理由的字段就在那里，不散落到各处。
 */
export function LibraryWorkspace() {
  const [params, setParams] = useSearchParams();
  const view = parseView(params.get('view'));
  const kindRaw = params.get('kind') ?? 'all';
  const kind: KindFilter = KINDS.has(kindRaw) ? (kindRaw as KindFilter) : 'all';
  const sortRaw = params.get('sort') ?? '-createdAt';
  const sort: LibrarySortKey = SORTS.has(sortRaw) ? (sortRaw as LibrarySortKey) : '-createdAt';
  const tagIds = useMemo(
    () => (params.get('tags') ?? '').split(',').filter(Boolean),
    [params],
  );
  const keyword = params.get('q') ?? '';
  const debouncedKeyword = useDebouncedValue(keyword, 250);
  const page = Math.max(1, Number(params.get('page') ?? 1) || 1);

  const subject = useSubject();
  const canCreate = useCan('library:create');
  const canUpdate = useCan('library:update');
  const canDeleteAny = useCan('library:delete:any');
  const canShareDirect = useCan('library:share:direct');
  const canShare = useCan('library:share');
  /* 并存观察期：有旧库权限的人给一条回头路。
     这里查 `asset:read` 而不是写死 true —— 只配了新库权限的账号点进去会 403，
     那是个「看得见但用不了」的入口，比没有更糟。 */
  const canReadLegacy = useCan('asset:read');
  const viewer = useMemo(
    () => ({ id: subject?.id ?? '', canDeleteAny }),
    [subject?.id, canDeleteAny],
  );

  const list = useLibraryList({
    view,
    kind: kind === 'all' ? undefined : kind,
    tagIds: tagIds.length ? tagIds : undefined,
    keyword: debouncedKeyword || undefined,
    sort,
    page,
    pageSize: PAGE_SIZE,
  });
  const tags = useLibraryTags();
  const groups = useMyGroups();
  const pending = useShareRequests('pending');
  const del = useDeleteAsset();
  const restore = useRestoreAsset();
  const purge = usePurgeAsset();
  const decision = useDraftDecision();
  const bulk = useBulkAction();
  const createDownload = useCreateDownload();
  const saveFile = useSaveAssetFile();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pack, setPack] = useState<{ open: boolean; preselected: string[] }>({
    open: false,
    preselected: [],
  });
  const [share, setShare] = useState<{ asset: LibraryAssetOut; mode: 'share' | 'revoke' } | null>(
    null,
  );
  const [requestsOpen, setRequestsOpen] = useState(false);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);

  const meta = viewMeta(view);
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const hasFilters = kind !== 'all' || tagIds.length > 0 || !!debouncedKeyword;

  function update(patch: Record<string, string | null>, replace = true) {
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        for (const [key, value] of Object.entries(patch)) {
          if (value === null || value === '') next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace },
    );
  }

  function changeView(next: LibraryView) {
    /* 切入口时清掉筛选与选择：留着 kind=pack 进草稿箱只会看到空列表 */
    setSelected(new Set());
    update({ view: next, kind: null, tags: null, q: null, page: null });
  }

  function toggle(id: string) {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll(ids: string[], checked: boolean) {
    setSelected((previous) => {
      const next = new Set(previous);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  const fail = (error: Error) => toast(error.message, 'error');

  function askDelete(asset: LibraryAssetOut) {
    const force = deleteOffer(asset, viewer) === 'force';
    setConfirm({
      title: force ? '强制移入回收站' : '移入回收站',
      description: force
        ? `「${asset.name}」属于 ${asset.ownerName || asset.ownerId}。强制删除会同时补一张自审的撤销共享单，理由会进审计。`
        : `「${asset.name}」会进入回收站，30 天内可以恢复；被封装或任务引用时不能彻底删除。`,
      confirmLabel: '移入回收站',
      danger: true,
      requireReason: force,
      reasonLabel: '强制删除理由',
      reasonPlaceholder: '例：上传错误版本，已与作者确认',
      onConfirm: (reason) => {
        del.mutate(
          { id: asset.id, reason },
          {
            onSuccess: () => {
              setConfirm(null);
              toast('已移入回收站');
            },
            onError: (error) => {
              setConfirm(null);
              const details = error instanceof ApiError ? (error.details as { canRevokeAndDelete?: boolean } | undefined) : undefined;
              if (details?.canRevokeAndDelete) {
                /* §3.4.1 窄口：本人所有 + 只共享给本组 + 我是组长，一步撤销并删除 */
                askRevokeAndDelete(asset);
                return;
              }
              fail(error);
            },
          },
        );
      },
    });
  }

  function askRevokeAndDelete(asset: LibraryAssetOut) {
    setConfirm({
      title: '撤销共享并删除',
      description: `「${asset.name}」只共享给你负责的小组，且你是组长：可以一步撤销共享并移入回收站。仍会写一张自审申请单，理由进审计。`,
      confirmLabel: '撤销并删除',
      danger: true,
      requireReason: true,
      reasonLabel: '撤销理由',
      reasonPlaceholder: '例：这批素材已替换成新版，旧版下架',
      onConfirm: (reason) => {
        del.mutate(
          { id: asset.id, revokeOwnGroupShare: true, reason },
          {
            onSuccess: () => {
              setConfirm(null);
              toast('已撤销共享并移入回收站');
            },
            onError: (error) => {
              setConfirm(null);
              fail(error);
            },
          },
        );
      },
    });
  }

  function askPurge(asset: LibraryAssetOut) {
    setConfirm({
      title: '彻底删除',
      description: `「${asset.name}」的字节会被永久删除，**不可恢复**。被模板 / 任务 / 封装引用时后端会拒绝并列出引用方。`,
      confirmLabel: '彻底删除',
      danger: true,
      onConfirm: () => {
        purge.mutate(asset.id, {
          onSuccess: () => {
            setConfirm(null);
            toast('已彻底删除');
          },
          onError: (error) => {
            setConfirm(null);
            fail(error);
          },
        });
      },
    });
  }

  function askReject(asset: LibraryAssetOut) {
    setConfirm({
      title: '驳回草稿',
      description: `「${asset.name}」会被移入回收站，恢复时回到草稿箱。驳回原因会记在资产上，生成方能看到。`,
      confirmLabel: '驳回',
      danger: true,
      requireReason: true,
      reasonLabel: '驳回原因',
      reasonPlaceholder: '例：人物手部变形，重新生成',
      onConfirm: (reason) => {
        decision.mutate(
          {
            id: asset.id,
            action: 'reject',
            body: { reason, expectedVersion: asset.stateVersion ?? 0 },
          },
          {
            onSuccess: () => {
              setConfirm(null);
              toast('已驳回');
            },
            onError: (error) => {
              setConfirm(null);
              fail(error);
            },
          },
        );
      },
    });
  }

  function onAction(action: LibraryCardAction, asset: LibraryAssetOut) {
    switch (action) {
      case 'open':
        setDetailId(asset.id);
        return;
      case 'share':
        setShare({ asset, mode: 'share' });
        return;
      case 'revoke':
        setShare({ asset, mode: 'revoke' });
        return;
      case 'delete':
        askDelete(asset);
        return;
      case 'purge':
        askPurge(asset);
        return;
      case 'reject':
        askReject(asset);
        return;
      case 'approve':
        decision.mutate(
          { id: asset.id, action: 'approve', body: { expectedVersion: asset.stateVersion ?? 0 } },
          {
            onSuccess: () => toast('已入库'),
            onError: fail,
          },
        );
        return;
      case 'restore':
        restore.mutate(asset.id, {
          onSuccess: () => toast('已恢复'),
          onError: fail,
        });
        return;
      case 'download':
        saveFile.mutate(
          { id: asset.id, filename: asset.name },
          { onSuccess: () => toast('已开始下载'), onError: fail },
        );
        return;
    }
  }

  function runBulk(kindOfAction: BulkKind, payload?: { tagIds?: string[] }) {
    const ids = [...selected];
    if (!ids.length) return;
    const done = (message: string) => {
      setSelected(new Set());
      toast(message, 'success');
    };
    const onError = (error: Error) => {
      setSelected(new Set());
      fail(error);
    };

    if (kindOfAction === 'download') {
      createDownload.mutate(
        { ids },
        {
          onSuccess: (job) => {
            const skipped = job.skipped?.length ?? 0;
            setSelected(new Set());
            setDownloadsOpen(true);
            toast(skipped ? `已创建打包任务，${skipped} 项被跳过` : '已创建打包任务');
          },
          onError,
        },
      );
      return;
    }
    if (kindOfAction === 'pack') {
      setPack({ open: true, preselected: ids });
      return;
    }
    if (kindOfAction === 'reject') {
      setConfirm({
        title: '批量驳回草稿',
        description: `选中的 ${ids.length} 条草稿会进入回收站，恢复时回到草稿箱。驳回原因对整批生效。`,
        confirmLabel: '批量驳回',
        danger: true,
        requireReason: true,
        reasonLabel: '驳回原因',
        onConfirm: (reason) => {
          bulk.mutate(
            { ids, action: 'rejectDraft', payload: { reason } },
            {
              onSuccess: (result) => {
                setConfirm(null);
                done(`已驳回 ${result.succeeded ?? 0} 条，失败 ${result.failed ?? 0} 条`);
              },
              onError: (error) => {
                setConfirm(null);
                onError(error);
              },
            },
          );
        },
      });
      return;
    }

    const action =
      kindOfAction === 'delete'
        ? 'delete'
        : kindOfAction === 'restore'
          ? 'restore'
          : kindOfAction === 'approve'
            ? 'approveDraft'
            : 'addTag';

    bulk.mutate(
      { ids, action, payload: payload as Record<string, unknown> | undefined },
      {
        onSuccess: (result) => {
          const failed = result.failed ?? 0;
          const first = result.results?.find((item) => !item.ok);
          setSelected(new Set());
          if (failed) {
            toast(
              `${result.succeeded ?? 0} 条成功，${failed} 条失败${first?.message ? `：${first.message}` : ''}`,
              'error',
            );
          } else {
            toast(`${result.succeeded ?? 0} 条已处理`, 'success');
          }
        },
        onError: (error) => {
          setSelected(new Set());
          fail(error);
        },
      },
    );
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <>
      <PageHeader
        title="资产库"
        description="草稿箱、个人、小组、团队与回收站五个入口的统一资产库。可见性、共享审批与生命周期由后端判定，界面只少显示不该点的按钮。"
        actions={
          <>
            {canReadLegacy && (
              <Link
                to="/assets"
                className="text-[11.5px] font-medium text-ink-mute hover:text-primary"
              >
                旧版素材库
              </Link>
            )}
            <Button
              size="sm"
              variant="ghost"
              icon={<Download size={13} />}
              onClick={() => setDownloadsOpen(true)}
            >
              打包任务
            </Button>
            <Chip tone="ok">v2 · M5 观察期</Chip>
          </>
        }
      />

      <div className="mt-4 space-y-3">
        <LibraryToolbar
          view={view}
          facets={list.data?.facets}
          total={list.data?.total}
          kind={kind}
          keyword={keyword}
          sort={sort}
          tags={tags.data ?? []}
          tagIds={tagIds}
          canCreate={canCreate}
          pendingCount={pending.data?.length ?? 0}
          busy={list.isPending}
          onView={changeView}
          onKind={(next) => update({ kind: next === 'all' ? null : next, page: null })}
          onKeyword={(value) => update({ q: value || null, page: null })}
          onSort={(next) => update({ sort: next, page: null })}
          onTags={(values) => update({ tags: values.join(',') || null, page: null })}
          onUpload={() => setUploadOpen(true)}
          onPack={() => setPack({ open: true, preselected: [...selected] })}
          onRequests={() => setRequestsOpen(true)}
        />

        <div className="flex items-center justify-between gap-2">
          <span className="text-[11.5px] text-ink-mute">
            {canShare ? '' : '共享与删除操作需要权限'}
          </span>
          <LibrarySelectAll items={items} selected={selected} onToggleAll={toggleAll} />
        </div>

        <LibraryGrid
          view={view}
          items={list.data?.items}
          selected={selected}
          viewer={viewer}
          isPending={list.isPending}
          error={(list.error as Error | null) ?? null}
          hasFilters={hasFilters}
          onRetry={() => void list.refetch()}
          onToggle={toggle}
          onToggleAll={toggleAll}
          onAction={onAction}
        />

        {totalPages > 1 && (
          <div className="flex justify-center">
            <Pager
              page={page}
              totalPages={totalPages}
              busy={list.isPending}
              onChange={(next) => update({ page: String(next) })}
            />
          </div>
        )}

        <LibraryBulkBar
          view={view}
          count={selected.size}
          tags={tags.data ?? []}
          busy={bulk.isPending || createDownload.isPending}
          onClear={() => setSelected(new Set())}
          onAction={runBulk}
        />

        <p className="flex items-center gap-1.5 text-[11px] text-ink-mute">
          <PackageCheck size={12} />
          {meta.label}：{meta.hint}
        </p>
      </div>

      <LibraryDetail
        assetId={detailId}
        tags={tags.data ?? []}
        viewer={viewer}
        canUpdate={canUpdate}
        onClose={() => setDetailId(null)}
        onAction={(action, asset) => {
          if (action === 'open') setDetailId(asset.id);
          else onAction(action, asset);
        }}
        onOpenAsset={setDetailId}
      />

      <LibraryUploadModal open={uploadOpen} tags={tags.data ?? []} onClose={() => setUploadOpen(false)} />

      <LibraryPackModal
        open={pack.open}
        preselected={pack.preselected}
        onClose={() => setPack({ open: false, preselected: [] })}
        onCreated={(id) => {
          setSelected(new Set());
          setDetailId(id);
        }}
      />

      <LibraryShareModal
        asset={share?.asset ?? null}
        mode={share?.mode ?? 'share'}
        groups={groups.data ?? []}
        canDirect={canShareDirect}
        onClose={() => setShare(null)}
      />

      <ShareRequestsModal open={requestsOpen} onClose={() => setRequestsOpen(false)} />
      <LibraryDownloadsModal open={downloadsOpen} onClose={() => setDownloadsOpen(false)} />

      <LibraryConfirmModal
        open={!!confirm}
        title={confirm?.title ?? ''}
        description={confirm?.description}
        confirmLabel={confirm?.confirmLabel}
        danger={confirm?.danger}
        requireReason={confirm?.requireReason}
        reasonLabel={confirm?.reasonLabel}
        reasonPlaceholder={confirm?.reasonPlaceholder}
        busy={del.isPending || purge.isPending || decision.isPending || bulk.isPending}
        onClose={() => setConfirm(null)}
        onConfirm={(reason) => confirm?.onConfirm(reason)}
      />
    </>
  );
}
