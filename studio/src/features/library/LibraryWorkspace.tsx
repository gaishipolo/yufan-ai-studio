import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Download } from 'lucide-react';
import { Button, PageHeader, Pager } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useCan, useSubject } from '@/shared/access/use-access';
import { libraryApi } from '@/entities/library/api/library.api';
import { ApiError } from '@/shared/api/types';
import type { BulkAction, LibraryAssetOut } from '@/shared/api/schema';
import {
  KIND_OPTIONS,
  deleteOffer,
  displayName,
  parseView,
  viewQuery,
  type KindFilter,
  type LibrarySortKey,
  type LibraryView,
} from '@/entities/library/model/types';
import { effectiveTagSelection } from '@/entities/library/model/tagTree';
import {
  useBulkAction,
  useCreateDownload,
  useDeleteAsset,
  useDraftDecision,
  useLibraryList,
  useLibraryTags,
  useMyGroups,
  usePatchAsset,
  usePurgeAsset,
  useRestoreAsset,
  useSaveAssetFile,
  useShareRequests,
} from '@/entities/library/api/library.queries';
import { LibraryBatchShareModal } from './LibraryBatchShareModal';
import { LibraryBulkBar, type BulkKind } from './LibraryBulkBar';
import { LibraryConfirmModal } from './LibraryConfirmModal';
import { LibraryDetail } from './LibraryDetail';
import { LibraryDownloadsModal } from './LibraryDownloadsModal';
import { LibraryGrid, LibrarySelectAll } from './LibraryGrid';
import { LibraryShareModal } from './LibraryShareModal';
import { LibraryTagBar } from './LibraryTagBar';
import { LibraryTagManager } from './LibraryTagManager';
import { LibraryTagPicker } from './LibraryTagPicker';
import { LibraryToolbar } from './LibraryToolbar';
import { LibraryUploadModal } from './LibraryUploadModal';
import { PackCreateModal, type EditingPack } from './PackCreateModal';
import { TextAssetModal } from './TextAssetModal';
import { ShareRequestsModal } from './ShareRequestsModal';
import type { LibraryCardAction } from './LibraryCard';

const PAGE_SIZE = 24;
/* URL 里的 `kind` 白名单。**从 `KIND_OPTIONS` 派生，不手抄**。
   手抄漏过三次（`text`、`pack`、再是 `pack` 回来那次），表现每次都一样：
   `?kind=…` 写进 URL 又被判回 `all`，用户看到的是「点了筛选没反应」。
   派生之后「加一档忘了同步这里」不可能再发生 —— 这条白名单本来就没有
   独立于筛选控件的语义。 */
const KINDS = new Set<string>(KIND_OPTIONS.map((item) => item.value));

/**
 * 删封装时要说清「成员不会被删，会回到资产库」。
 *
 * 删包做的事只是解除归属：成员仍是自己的资产行。不说的话，用户点完「移入
 * 回收站」会看到资产库突然多出几张图 —— 而界面上没有任何地方提过这件事，
 * 读起来更像「回收站把包拆开吐了一地」。反过来，以为图跟着没了的人会去
 * 回收站里找，也找不到。
 */
function packMemberNote(assets: LibraryAssetOut[]): string {
  const packs = assets.filter((asset) => asset.kind === 'pack');
  if (!packs.length) return '';
  const total = packs.reduce((sum, asset) => sum + (asset.memberCount ?? 0), 0);
  return packs.length === 1
    ? `里面的 ${total} 张素材不会被删除，会回到资产库单独显示。`
    : `这 ${packs.length} 个封装里的 ${total} 张素材不会被删除，会回到资产库单独显示。`;
}
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
  const canManageTags = useCan('library:tag:manage');
  const canDeleteAny = useCan('library:delete:any');
  const canShareDirect = useCan('library:share:direct');
  const canShare = useCan('library:share');
  const viewer = useMemo(
    () => ({ id: subject?.id ?? '', canDeleteAny }),
    [subject?.id, canDeleteAny],
  );

  const tagScope = useMemo(() => viewQuery(view), [view]);
  const tags = useLibraryTags(tagScope);
  /*
   * 发给后端的是**收敛后**的标签：跨链多选是并集，但同一条链上只按最具体的那
   * 一层筛选（父的范围本来就含子，两个都留着范围会重复 —— 用户点「产品 /
   * 3030」要的是 3030 那一档，不是整个产品线）。URL 里存的仍是用户点的全部，
   * 所以标签栏的胶囊全都高亮，用户看得见自己圈了哪几档。
   */
  const effectiveTagIds = useMemo(
    () => effectiveTagSelection(tags.data ?? [], tagIds),
    [tags.data, tagIds],
  );
  const list = useLibraryList({
    view,
    kind: kind === 'all' ? undefined : kind,
    tagIds: effectiveTagIds.length ? effectiveTagIds : undefined,
    keyword: debouncedKeyword || undefined,
    sort,
    page,
    pageSize: PAGE_SIZE,
  });
  const patch = usePatchAsset();
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
  /* 建封装弹窗。`preset` 不单独存：选中项已经被裁到当前页（见上面的 effect），
     所以 `items` 里一定拿得到它们的完整对象，用派生值比再存一份同步状态稳。 */
  const [packOpen, setPackOpen] = useState(false);
  /* 编辑哪一个封装（空 = 新建）。`PackCreateModal` 用 `pack` 是否存在区分两种模式，
     所以和「新建」共用一个实例：同一张表单，两套入口。 */
  const [editingPack, setEditingPack] = useState<EditingPack | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [textModalOpen, setTextModalOpen] = useState(false);
  const [share, setShare] = useState<{ asset: LibraryAssetOut; mode: 'share' | 'revoke' } | null>(
    null,
  );
  /** 批量共享打开时把选中项快照下来：弹窗期间列表刷新/选中变化不该改这批的目标 */
  const [batchShare, setBatchShare] = useState<LibraryAssetOut[] | null>(null);
  const [requestsOpen, setRequestsOpen] = useState(false);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const [tagManagerOpen, setTagManagerOpen] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  /** 卡片标签行 `+` 打开的选择器；只存 asset，词表与权限在渲染时取当前值 */
  const [tagPicker, setTagPicker] = useState<LibraryAssetOut | null>(null);

  const items = useMemo(() => list.data?.items ?? [], [list.data?.items]);
  const total = list.data?.total ?? 0;
  const hasFilters = kind !== 'all' || tagIds.length > 0 || !!debouncedKeyword;
  /* 建封装的初始成员 = 当前选中的素材。从批量条进来时这就是「刚勾的那几条」；
     从工具栏进来时也带上（勾完再点新建封装，期望显然是包含它们）—— 想减掉
     哪张，在弹窗里删就是了。 */
  const packPreset = useMemo(
    () => items.filter((item) => selected.has(item.id)),
    [items, selected],
  );

  /* 列表数据一变（换类型/关键词/标签、翻页、删掉一条）就把选中项裁到当前结果里。
     批量条只写「已选 N 项」，不列是哪几条——留着看不见的 id，用户点批量删除时
     删掉的是自己都看不到的素材（其中还可能有已被删掉的 id）。 */
  useEffect(() => {
    const visible = new Set(items.map((item) => item.id));
    setSelected((previous) => {
      const next = new Set([...previous].filter((id) => visible.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [items]);

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
    /* 切入口时清掉筛选与选择：留着 kind 进草稿箱只会看到空列表 */
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

  /** 标签栏交回**完整**的选中数组（三个级别可跨链多选），容器只写 URL。
      URL 里只存 tag id、不另存「当前一级」，刷新或分享后三行仍然对得上。 */
  function changeTags(next: string[]) {
    update({ tags: next.join(',') || null, page: null });
  }

  /** 卡片标签上的 ×：用整体替换去掉一个标签，不让「去标」变成新接口。 */
  function removeTag(asset: LibraryAssetOut, tagId: string) {
    const remaining = (asset.tags ?? [])
      .filter((tag) => tag.id !== tagId)
      .map((tag) => tag.id);
    patch.mutate(
      { id: asset.id, body: { tagIds: remaining } },
      {
        onSuccess: () => toast('已移除标签'),
        onError: fail,
      },
    );
  }

  /** 卡片标签上的 +：选择器交回的是完整 tagIds，与 × 走同一个 PATCH。 */
  function addTags(asset: LibraryAssetOut, tagIds: string[]) {
    patch.mutate(
      { id: asset.id, body: { tagIds } },
      {
        onSuccess: () => {
          setTagPicker(null);
          toast('已添加标签');
        },
        onError: fail,
      },
    );
  }

  function askDelete(asset: LibraryAssetOut) {
    const force = deleteOffer(asset, viewer) === 'force';
    setConfirm({
      title: force ? '强制移入回收站' : '移入回收站',
      description: force
        ? `「${displayName(asset.name)}」属于 ${asset.ownerName || asset.ownerId}。强制删除会同时补一张自审的撤销共享单，理由会进审计。`
        : asset.kind === 'pack'
          ? `「${displayName(asset.name)}」会进入回收站，30 天内可以恢复。${packMemberNote([asset])}`
          : `「${displayName(asset.name)}」会进入回收站，30 天内可以恢复；被任务引用时不能彻底删除。`,
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
              /* 从详情里删的：顺手把详情关掉。留着它会去拉一条刚被删的资产，
                 弹窗停在那显示「资产不存在」—— 看起来像删除失败了。 */
              setDetailId(null);
              toast(
                asset.kind === 'pack'
                  ? `已把「${displayName(asset.name)}」移入回收站，${asset.memberCount ?? 0} 张素材已回到资产库`
                  : '已移入回收站',
              );
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
      description: `「${displayName(asset.name)}」只共享给你负责的小组，且你是组长：可以一步撤销共享并移入回收站。仍会写一张自审申请单，理由进审计。`,
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
      description:
        asset.kind === 'pack'
          ? `「${displayName(asset.name)}」会被永久删除，不可恢复。${packMemberNote([asset])}`
          : `「${displayName(asset.name)}」的字节会被永久删除，**不可恢复**。被模板 / 任务引用时后端会拒绝并列出引用方。`,
      confirmLabel: '彻底删除',
      danger: true,
      onConfirm: () => {
        purge.mutate(asset.id, {
          onSuccess: () => {
            setConfirm(null);
            setDetailId(null);
            toast(
              asset.kind === 'pack'
                ? `已删除「${displayName(asset.name)}」，${asset.memberCount ?? 0} 张素材已回到资产库`
                : '已彻底删除',
            );
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
      description: `「${displayName(asset.name)}」会被移入回收站，恢复时回到草稿箱。驳回原因会记在资产上，生成方能看到。`,
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
        /* 封装没有自己的字节，但它能进**打包任务**：后端把成员展开再压 zip。
           走单文件那条会得到「此资产没有原文件」——同一个按钮两套链路，
           而用户看到的结果差别是「下了」和「报错」。 */
        if (asset.kind === 'pack') {
          createDownload.mutate(
            { ids: [asset.id] },
            {
              onSuccess: () => {
                setDownloadsOpen(true);
                toast('已创建打包任务');
              },
              onError: fail,
            },
          );
          return;
        }
        saveFile.mutate(
          { id: asset.id, filename: asset.name },
          { onSuccess: () => toast('已开始下载'), onError: fail },
        );
        return;
      case 'editPack':
        /* 卡片上只有 `memberCount`，编辑器要 **成员行**（名字、kind、标签）。
           先拉详情再开弹窗：那一次请求省不掉，但用户不该为此先跳进详情页。
           直接返回 promise 而不是走 mutation —— 这次请求的「结果」只是打开一个
           弹窗，没有需要留在缓存里的东西，也没有要表达的重试语义。 */
        libraryApi
          .detail(asset.id)
          .then((row) => {
            setEditingPack({
              id: row.id,
              name: row.name,
              members: row.packItems ?? [],
            });
            setPackOpen(true);
          })
          .catch(fail);
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
    if (kindOfAction === 'share') {
      /* 与单条共享共用同一套目标口径；这里只快照选中项，目标由弹窗选。
         弹窗取消时选中项还在，用户还能对这批素材做别的操作。 */
      const picked = items.filter((asset) => selected.has(asset.id));
      if (!picked.length) return;
      setBatchShare(picked);
      return;
    }
    if (kindOfAction === 'pack') {
      /* 不在这儿调接口：建封装要先让用户确认成员和起个名字。选中项不急着清，
         弹窗取消时用户还能继续对这批素材做别的操作。 */
      setPackOpen(true);
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

    if (kindOfAction === 'delete') {
      /* 批量删除与单条删除走**同一套确认**：不可逆的动作不留「手滑」的口子。
         共享出去的素材（小组/团队）后端必然拒——那类素材单独剔掉，不让整批
         陪葬；卡片层在小组/团队入口本来就不给删除按钮，这里是同一口径。 */
      const picked = items.filter((asset) => selected.has(asset.id));
      const deletable = picked.filter((asset) => {
        const offer = deleteOffer(asset, viewer);
        return offer === 'trash' || offer === 'force';
      });
      const skipped = picked.length - deletable.length;
      /* 选中项与当前列表对不上（列表正在刷新）：什么都不做，等列表回来再说 */
      if (!picked.length) return;
      if (!deletable.length) {
        toast('选中的素材共享给小组或团队，不能直接移入回收站', 'error');
        return;
      }
      const force = deletable.some((asset) => deleteOffer(asset, viewer) === 'force');
      setConfirm({
        title: '批量移入回收站',
        description: `选中的 ${deletable.length} 条素材会进入回收站，30 天内可以恢复。${
          skipped ? `另外 ${skipped} 条共享给小组或团队，不能直接删除，已跳过。` : ''
        }${packMemberNote(deletable)}`,
        confirmLabel: '批量移入回收站',
        danger: true,
        /* 管理员强制删除仍要填理由：与单条删除复用同一套理由字段 */
        requireReason: force,
        reasonLabel: '强制删除理由',
        reasonPlaceholder: '例：上传错误版本，已与作者确认',
        onConfirm: (reason) => {
          applyBulk(
            deletable.map((asset) => asset.id),
            'delete',
            { reason },
          );
        },
      });
      return;
    }

    const action =
      kindOfAction === 'restore'
        ? 'restore'
        : kindOfAction === 'approve'
          ? 'approveDraft'
          : 'addTag';

    applyBulk(ids, action, payload as Record<string, unknown> | undefined);
  }

  /** 批量写：逐条判定、逐条提交；成败混在一起时把失败明细带出来 */
  function applyBulk(
    writeIds: string[],
    action: BulkAction,
    writePayload?: Record<string, unknown>,
    expectedVersions?: Record<string, number>,
  ) {
    bulk.mutate(
      { ids: writeIds, action, payload: writePayload, expectedVersions },
      {
        onSuccess: (result) => {
          const failed = result.failed ?? 0;
          const first = result.results?.find((item) => !item.ok);
          setConfirm(null);
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
          setConfirm(null);
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
        actions={
          <Button
            size="sm"
            variant="ghost"
            icon={<Download size={13} />}
            onClick={() => setDownloadsOpen(true)}
          >
            打包下载
          </Button>
        }
      />

      <div className="mt-4 space-y-3">
        <LibraryToolbar
          view={view}
          facets={list.data?.facets}
          kind={kind}
          keyword={keyword}
          sort={sort}
          canCreate={canCreate}
          pendingCount={pending.data?.length ?? 0}
          busy={list.isPending}
          onView={changeView}
          onKind={(next) => update({ kind: next === 'all' ? null : next, page: null })}
          onKeyword={(value) => update({ q: value || null, page: null })}
          onSort={(next) => update({ sort: next, page: null })}
          onUpload={() => setUploadOpen(true)}
          onNewText={() => setTextModalOpen(true)}
          onNewPack={() => setPackOpen(true)}
          onRequests={() => setRequestsOpen(true)}
        />

        {/* 标签筛选条与「共 N 条」收进同一张卡：原来这里只有一句入口说明和
            一个全选按钮，白占一行；现在这一屏就能看到全部标签与各自的数量。 */}
        <div className="rounded-card border border-line-soft bg-panel px-3 py-2">
          <LibraryTagBar
            tags={tags.data ?? []}
            selected={tagIds}
            busy={tags.isPending}
            onChange={changeTags}
            onClear={() => update({ tags: null, page: null })}
            onManage={() => setTagManagerOpen(true)}
            canManage={canManageTags}
          />
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-line-soft pt-2">
            <span className="min-w-0 truncate text-body text-ink-mute">
              共 {total} 条
            </span>
            <LibrarySelectAll items={items} selected={selected} onToggleAll={toggleAll} />
          </div>
        </div>

        <LibraryGrid
          view={view}
          items={list.data?.items}
          selected={selected}
          viewer={viewer}
          canUpdate={canUpdate}
          canShare={canShare}
          canEditPack={canCreate}
          tagBusy={patch.isPending}
          isPending={list.isPending}
          error={(list.error as Error | null) ?? null}
          hasFilters={hasFilters}
          onRetry={() => void list.refetch()}
          onToggle={toggle}
          onAction={onAction}
          onRemoveTag={removeTag}
          onAddTag={(asset) => setTagPicker(asset)}
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
          canShare={canShare}
          busy={bulk.isPending || createDownload.isPending}
          onClear={() => setSelected(new Set())}
          onAction={runBulk}
        />
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
      />

      <PackCreateModal
        open={packOpen}
        pack={editingPack ?? undefined}
        preset={packPreset}
        onClose={() => {
          setPackOpen(false);
          setEditingPack(null);
        }}
        onCreated={() => setSelected(new Set())}
        /* 改完要重拉列表：名字、成员数、共同标签都会变（标签是后端按新成员重算的）。 */
        onUpdated={() => {
          setPackOpen(false);
          setEditingPack(null);
          void list.refetch();
        }}
      />

      <LibraryUploadModal open={uploadOpen} tags={tags.data ?? []} onClose={() => setUploadOpen(false)} />

      {textModalOpen && (
        <TextAssetModal open mode="create" onClose={() => setTextModalOpen(false)} />
      )}

      {tagPicker && (
        <LibraryTagPicker
          key={tagPicker.id}
          asset={tagPicker}
          tags={tags.data ?? []}
          busy={patch.isPending}
          onSubmit={addTags}
          onClose={() => setTagPicker(null)}
        />
      )}

      {tagManagerOpen && canManageTags && (
        <LibraryTagManager
          tags={tags.data ?? []}
          onClose={() => setTagManagerOpen(false)}
        />
      )}

      <LibraryShareModal
        asset={share?.asset ?? null}
        mode={share?.mode ?? 'share'}
        groups={groups.data ?? []}
        canDirect={canShareDirect}
        isOwner={!!share && share.asset.ownerId === viewer.id}
        onClose={() => setShare(null)}
      />

      {batchShare && (
        <LibraryBatchShareModal
          count={batchShare.length}
          groups={groups.data ?? []}
          canDirect={canShareDirect}
          allOwned={batchShare.every((asset) => asset.ownerId === viewer.id)}
          busy={bulk.isPending}
          onClose={() => setBatchShare(null)}
          onSubmit={({ target, groupId, reason }) => {
            const expectedVersions = Object.fromEntries(
              batchShare.map((asset) => [asset.id, asset.stateVersion ?? 0]),
            );
            setBatchShare(null);
            applyBulk(
              batchShare.map((asset) => asset.id),
              'requestShare',
              { target, groupId: groupId ?? '', reason },
              expectedVersions,
            );
          }}
        />
      )}

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
