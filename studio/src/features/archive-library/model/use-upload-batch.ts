import { create } from 'zustand';
import { archiveApi } from '@/entities/asset/api/archive.api';
import { archiveKeys } from '@/entities/asset/api/archive.queries';
import type { ArchiveCategory } from '@/entities/asset/model/archive.types';
import { useSessionStore } from '@/shared/access/session';
import { queryClient } from '@/shared/api/query-client';
import {
  batchReducer, classifyUploadError, isBatchLocked, messageOf, nextQueued,
  type BatchEvent, type UploadBatch,
} from './upload-batch';

/**
 * 批次状态放在模块级 store，不放组件 state。
 *
 * 用户中途去别的页面看一眼再回来，如果状态在组件里，「已归档 3 个、失败 1 个」会全部消失，
 * 而他无法判断哪些已经写进归档盘。这里只存内存、不持久化：刷新页面丢失可以接受，
 * 因为服务端事实还在，列表能查到。
 */

interface UploadBatchStore {
  batch: UploadBatch | null;
  ownerId: string | null;
  dispatch: (event: BatchEvent) => void;
  start: (files: File[], category: ArchiveCategory) => void;
  retry: (key: string) => void;
  requeue: (key: string) => void;
  skip: (key: string) => void;
  confirm: (key: string, assetId: string) => void;
  dismiss: () => void;
}

/** 串行 runner 不能重入：两个并发循环会把同一批文件各传一遍。 */
let running = false;

export const useUploadBatchStore = create<UploadBatchStore>((set, get) => ({
  batch: null,
  ownerId: null,
  dispatch: event => set(state => ({batch: batchReducer(state.batch, event)})),
  start: (files, category) => {
    if (isBatchLocked(get().batch)) return;
    const batch = batchReducer(null, {
      type: 'create', id: crypto.randomUUID(), category, files, startedAt: Date.now(),
    });
    if (!batch) return;
    set({batch, ownerId: useSessionStore.getState().subject.id});
    void run();
  },
  retry: key => { get().dispatch({type: 'retry', key}); void run(); },
  requeue: key => { get().dispatch({type: 'requeue', key}); void run(); },
  skip: key => { get().dispatch({type: 'skip', key}); void run(); },
  /* 确认也要续跑：确认「未知」项之后，后面可能还有未处理的文件在等这个批次解锁。 */
  confirm: (key, assetId) => { get().dispatch({type: 'confirm', key, assetId}); void run(); },
  dismiss: () => {
    /* 还有请求在途时不能清空：结果回来会写进一个已经不存在的批次。 */
    if (get().batch?.items.some(item => item.status === 'uploading')) return;
    get().dispatch({type: 'dismiss'});
  },
}));

/** 统一刷新一次。逐条 invalidate 会让 5 个文件触发 5 次全量列表重取。 */
async function refreshList(owner: string) {
  await queryClient.invalidateQueries({queryKey: archiveKeys.all(owner)});
}

async function run() {
  if (running) return;
  const owner = useUploadBatchStore.getState().ownerId;
  const batchId = useUploadBatchStore.getState().batch?.id;
  if (!owner || !batchId || useSessionStore.getState().subject.id !== owner) return;
  running = true;
  try {
    for (;;) {
      const batch = useUploadBatchStore.getState().batch;
      if (!batch || batch.phase !== 'running' || batch.id !== batchId
        || useUploadBatchStore.getState().ownerId !== owner
        || useSessionStore.getState().subject.id !== owner) break;
      const item = nextQueued(batch);
      if (!item) break;
      const {dispatch} = useUploadBatchStore.getState();
      dispatch({type: 'begin', key: item.key});
      try {
        const asset = await archiveApi.upload({file: item.file, category: batch.category});
        if (useUploadBatchStore.getState().batch?.id !== batchId) break;
        dispatch({type: 'succeed', key: item.key, assetId: asset.id});
      } catch (error) {
        if (useUploadBatchStore.getState().batch?.id !== batchId) break;
        /* 首错暂停：可能是明确失败（可重发），也可能是结果未知（禁止重发）。
           判定交给 UI，runner 不在这里猜。 */
        dispatch({type: 'reject', key: item.key, message: messageOf(error), status: classifyUploadError(error)});
        break;
      }
    }
  } finally {
    running = false;
    /* 暂停或结束都刷新：暂停时更要刷新，用户核对「未知」项需要看到最新列表。 */
    await refreshList(owner);
    if (useUploadBatchStore.getState().batch?.phase === 'running') void run();
  }
}

// 登出或切换账号后，旧批次不得暴露文件名或借用新账号令牌上传后续文件。
useSessionStore.subscribe((state, previous) => {
  if (state.subject.id !== previous.subject.id) useUploadBatchStore.setState({batch: null, ownerId: null});
});
