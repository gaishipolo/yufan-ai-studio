import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useArchiveAssets, useArchiveUpload, useArchiveSelection } from '@/entities/asset/api/archive.queries';
import { useExecuteReplacement, usePrepareReplacement, useReplacementCatalog } from '@/entities/template/api/replacement.queries';
import { replacementKind, type ReplacementKind, type ReplacementRun, type ReplacementInput } from '@/entities/template/model/replacement.types';
import { toast } from '@/shared/lib/toast';
import { isReplacementPackAvailable } from '@/entities/template/model/replacementPack';
import { TYPES } from './replacementOptions';

/** 编排页面动作；服务端记录由 Query 保存，仅持有选择和反馈等 UI 状态。 */
export function useReplacementWorkbench() {
  const [params, setParams] = useSearchParams();
  /* 所有 URL 写入走同一处 patch：一次事件里连调两次 setParams 时，
     第二次会基于同一个旧快照计算，后写的会把前一次改动整条覆盖。 */
  function patchParams(patch: Record<string, string | null>) {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value); else next.delete(key);
      }
      return next;
    }, {replace: true});
  }
  const kind = replacementKind(params.get('kind'));
  const [packId, updatePackId] = useState(params.get('packId') ?? '');
  function setKind(value: ReplacementKind) {
    // 只改 state，不调 setPackId：那是另一次 URL 写入，会和下面的 patch 互相覆盖。
    updatePackId('');
    setNotes('');
    patchParams({kind: value, packId: null, packVersion: null});
  }
  /* 原片选择只有一个来源：URL。选择、清空、深链带入都改同一个参数，
     否则刷新会复活旧选择，`linkedSource` 提示也可能指向另一条素材。 */
  const sourceId = params.get('sourceId');
  function setSourceId(id: string | null) {
    patchParams({sourceId: id});
  }
  function setPackId(id: string) {
    updatePackId(id);
    patchParams({packId: id || null, packVersion: null});
  }
  const [scopes, setScopes] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [reviewId, setReviewId] = useState<string | null>(null);
  const detailId = params.get('run');
  const setDetailId = (id: string | null) => patchParams({run: id});
  const search = params.get('q') ?? '';
  const setSearch = (value: string) => patchParams({q: value});
  /* 历史区的筛选同样进 URL：同页其它筛选都在 URL，刷新后只剩它们会丢，语义也对不上。 */
  const historyTrash = params.get('trash') === '1';
  const historyStatus = params.get('taskStatus') ?? '';
  const historySearch = params.get('taskQ') ?? '';
  function setHistoryFilter(patch: {trash?: boolean; status?: string; keyword?: string}) {
    patchParams({
      ...(patch.trash === undefined ? {} : {trash: patch.trash ? '1' : null}),
      ...(patch.status === undefined ? {} : {taskStatus: patch.status || null}),
      ...(patch.keyword === undefined ? {} : {taskQ: patch.keyword || null}),
    });
  }
  const [preparing, setPreparing] = useState(false);
  const [batchPreparing, setBatchPreparing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{type: 'success' | 'error'; message: string} | null>(null);
  const requestIds = useRef(new Map<string, string>());
  const preparedIds = useRef(new Map<string, string>());
  const operationLock = useRef(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const sourceScope = params.get('sourceScope') === 'public' ? 'public' : 'mine';
  const sourcePage = Math.max(1, Number(params.get('sourcePage')) || 1);
  const setSourceScope = (scope: string) => patchParams({sourceScope: scope, sourcePage: '1'});
  const setSourcePage = (page: number) => patchParams({sourcePage: String(page)});
  const assets = useArchiveAssets('source', {scope: sourceScope, keyword: search, page: sourcePage, pageSize: 24});
  const linkedSource = useArchiveSelection(sourceId);
  const catalog = useReplacementCatalog();
  const prepare = usePrepareReplacement();
  const execute = useExecuteReplacement();
  const upload = useArchiveUpload();
  const template = TYPES.find(item => item.kind === kind) ?? TYPES[0];
  const videos = assets.data?.items.filter(asset => asset.category === 'source' && asset.name.toLowerCase().includes(search.toLowerCase())) ?? [];
  const currentDetail = catalog.data?.runs.find(run => run.id === detailId);
  const review = catalog.data?.runs.find(run => !run.deletedAt && run.id === reviewId);
  const busy = preparing || submitting || upload.isPending || batchPreparing;
  const packs = catalog.data?.packs?.filter(pack => pack.kind === kind) ?? [];
  const selectedPack = packs.find(pack => pack.id === packId);
  /* 替换范围由素材包审核结果决定：换包就重置为它支持的全部位置，
     不让上一包的选择残留成越权提交。 */
  const scopeLabels = catalog.data?.packScopes ?? {};
  const availableScopes = (selectedPack?.supportedScopes ?? []).map(key => scopeLabels[key] ?? key);
  /* 只在「换包 / 换版本 / 该包范围变了」时重置。用目录对象做依赖会让后台轮询
     每 5 秒把用户手动取消的范围又勾回来。 */
  const scopeSignature = `${selectedPack?.id ?? ''}:${selectedPack?.version ?? 0}:${(selectedPack?.supportedScopes ?? []).join('|')}`;
  useEffect(() => {
    setScopes(availableScopes);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 依赖签名而非目录引用，避免轮询覆盖手动勾选
  }, [scopeSignature]);
  const references = selectedPack?.referenceIds ?? [];
  const target = selectedPack?.name ?? '';
  const wardrobeMode = selectedPack?.wardrobeMode ?? 'preserve';
  const requestedVersion = params.get('packId') === packId ? params.get('packVersion') : null;
  const packReady = isReplacementPackAvailable(selectedPack, assets.data?.items ?? []) && (!requestedVersion || String(selectedPack?.version) === requestedVersion);
  const missing = !packReady ? `请选择一个可用的${template.short}素材包` : kind === 'product' && !scopes.length ? '请选择至少一个产品替换位置' : '';
  function report(type: 'success' | 'error', message: string) {
    setFeedback({type, message});
    toast(message, type);
  }
  function toggle(values: string[], id: string) {
    return values.includes(id) ? values.filter(value => value !== id) : [...values, id];
  }
  function pickUpload() {
    if (uploadInput.current) {
      uploadInput.current.accept = '.mp4';
      uploadInput.current.click();
    }
  }
  async function prepareTask() {
    if (busy || operationLock.current) return;
    if (!sourceId || missing || assets.error || catalog.error || linkedSource.error) {report('error', missing || '请先选择原片并恢复素材加载'); return;}
    operationLock.current = true;
    setFeedback(null);
    setPreparing(true);
    try {
      const config: Omit<ReplacementInput, 'requestId'> = {kind, target: target.trim(), sourceId, referenceIds: references, wardrobeMode,
        packId: packId || null, packVersion: selectedPack?.version || 1, replacementScope: scopes.join('、'), notes, aspectRatio: 'adaptive'};
      const key = JSON.stringify(config);
      const previous = catalog.data?.runs.find(run => run.id === preparedIds.current.get(key));
      if (previous?.deletedAt) requestIds.current.delete(key);
      if (previous && !previous.deletedAt && !['preparing', 'prepare_failed'].includes(previous.status)) {
        setReviewId(previous.status === 'prepared' ? previous.id : null);
        report('success', previous.status === 'prepared' ? '输入已保存，请核对后确认生成。' : '该配置已有调用记录，请在任务记录中查看进度或结果。');
        return;
      }
      let requestId = requestIds.current.get(key);
      if (!requestId) {requestId = crypto.randomUUID(); requestIds.current.set(key, requestId);}
      const result = await prepare.mutateAsync({...config, requestId});
      preparedIds.current.set(key, result.id);
      setReviewId(result.status === 'prepared' ? result.id : null);
      report('success', result.status === 'prepared' ? '输入已保存，请核对后确认生成。' : '该配置已有调用记录，请在任务记录中查看进度或结果。');
    } catch (reason) {
      report('error', `准备失败：${reason instanceof Error ? reason.message : '输入准备未完成'}。可在当前配置下重试，不会重复创建任务。`);
    } finally {operationLock.current = false; setPreparing(false);}
  }
  /* billingMode 不传 = 沿用档案里的计费模式：「继续查询已有任务」走这条路，
     不该顺带把一条已经加速的任务改回免费队列。 */
  async function submitRun(run: ReplacementRun, billingMode?: 'free' | 'credits') {
    if (busy || operationLock.current) return;
    operationLock.current = true;
    setSubmitting(true);
    setFeedback(null);
    try {
      const updated = await execute.mutateAsync({id: run.id, billingMode});
      setReviewId(null);
      /* 反馈跟着档案的真实状态走：重复点确认时后端只查询不重提，说「已请求提交」
         会让用户以为又排了一条，说「已在生成中」才是他看到的事实。 */
      const live = updated && ['queued', 'uploading', 'uploaded', 'submitting', 'submitted', 'running', 'generated'].includes(updated.status);
      report('success', updated?.status === 'success'
        ? '这条任务已经生成完成，成片可在任务记录中播放或下载。'
        : live
        ? `任务已在执行中（进度 ${updated?.progress ?? 0}%），状态已刷新；重复点击不会重复提交。`
        : billingMode === 'credits'
        ? '已请求用积分加速：这条任务将按积分报价进入正常队列，实际扣费以任务记录为准。'
        : '已处理任务的执行请求，请在任务记录中查看最新状态；仅在估价为 0 积分时提交生成。');
    } catch (reason) {
      report('error', `执行请求失败：${reason instanceof Error ? reason.message : '请求未完成'}`);
    } finally {operationLock.current = false; setSubmitting(false);}
  }
  function handleUpload(event: ChangeEvent<HTMLInputElement>) {
      const file = event.target.files?.[0]; if (!file || busy) return;
      upload.mutate({file, category: 'source'}, {onSuccess: asset => {
        if (!asset.hasAudio || (asset.duration ?? 0) < 1 || (asset.duration ?? 0) > 15) {
          report('success', `“${asset.name}”已保存，但不符合 1–15 秒且含音轨的要求，未加入选择。请换一条合规原片。`);
          return;
        }
        // 选择与来源切换必须一次写完：两次 patch 会互相覆盖。
        patchParams({sourceId: asset.id, sourceScope: 'mine', sourcePage: '1'});
        report('success', `“${asset.name}”已保存到素材库。请选择已封装的素材包后继续。`);
      }, onError: reason => report('error', `素材上传失败：${reason.message}。请检查文件后重新上传。`)});
      event.target.value = '';

  }
  return { setBatchPreparing, sourceScope, setSourceScope, sourcePage, setSourcePage, linkedSource, params, kind, setKind, sourceId, setSourceId, references, target, packId, setPackId, wardrobeMode, selectedPack, packs, scopes, setScopes, availableScopes, notes, setNotes, reviewId, setReviewId, detailId, setDetailId, search, setSearch, historyTrash, historyStatus, historySearch, setHistoryFilter, preparing, submitting, feedback, setFeedback, uploadInput, assets, catalog, upload, template, videos, currentDetail, review, busy, missing, toggle, pickUpload, prepareTask, submitRun, handleUpload };
}

export type ReplacementWorkbench = ReturnType<typeof useReplacementWorkbench>;
