import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useExecuteReplacement, usePrepareReplacement, useReplacementCatalog } from '@/entities/template/api/replacement.queries';
import { isStateChanged } from '@/shared/api/types';
import { useVideoTemplates } from '@/entities/video-template/api/videoTemplate.queries';
import { useLibraryDetail, useLibrarySelection, useUploadSourceVideo } from '@/entities/library/api/library.queries';
import type { LibraryAssetOut, ReplacementChannelOut, VideoTemplateOut } from '@/shared/api/schema';
import { hasForeignMustache } from '@/features/library/textRefs';
import { replacementKind, type ReplacementKind, type ReplacementRun, type ReplacementInput } from '@/entities/template/model/replacement.types';
import { toast } from '@/shared/lib/toast';
import { createId } from '@/shared/lib/id';
import { isReplacementPackAvailable } from '@/entities/template/model/replacementPack';
import { NEXT_ACTION, RETRY, TYPES } from './replacementOptions';
import { videoTemplateLabel, videoTemplateScopes, videoTemplatesForSource } from './videoTemplateFunctions';
import { SOURCE_VIDEO_ORIGINS } from '@/entities/library/model/types';
import { sourceVideoOfLibrary, type SourceVideo } from './sourceVideo';

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
    setExtraPackIds([]);
    setScopes([]);
    setNotes('');
    setTextRefs([]);
    patchParams({kind: value, packId: null, packVersion: null, libPack: null, videoTemplateId: null});
  }
  /* 原片选择只有一个来源：URL。选择、清空、深链带入都改同一个参数，
     否则刷新会复活旧选择，`linkedSource` 提示也可能指向另一条素材。 */
  const sourceId = params.get('sourceId');
  function setSourceId(id: string | null) {
    setExtraPackIds([]);
    setScopes([]);
    setTextRefs([]);
    patchParams({sourceId: id, videoTemplateId: null});
  }
  function setPackId(id: string) {
    updatePackId(id);
    // 模板素材包与资产库封装是**两个参考来源**：选一个必须清掉另一个，
    // 否则服务端会按「同时给了两路」直接 400（两套 id 形状一样，不允许谁优先）。
    patchParams({packId: id || null, packVersion: null, libPack: null});
  }
  /* 第二个参考来源：资产库 v2 封装（`libraryPackId`）。与模板素材包互斥，
     选择写进 URL 的 `libPack`，刷新与分享都还原同一个来源。 */
  const libraryPackId = params.get('libPack') ?? '';
  function setLibraryPackId(id: string) {
    updatePackId('');
    setExtraPackIds([]);
    setScopes([]);
    patchParams({libPack: id || null, packId: null, packVersion: null});
  }
  const libraryPackQuery = useLibraryDetail(libraryPackId || null);
  const libraryPack = libraryPackId ? libraryPackQuery.data : undefined;
  /* 只有能进提示词的那几张：非图片成员（音频/文本）与已不可用的成员不该出现在
     张数统计里——它们由服务端单独判，这里统计的是「用户以为会喂进去的图」。 */
  const libraryPackImages = (libraryPack?.packItems ?? []).filter(
    item => item.kind === 'image' && item.state === 'active',
  );
  /* 用途没声明的张数：服务端会拒（不按位置猜主图），但那是「下一步」之后才报，
     而准备是全模块最重的 IO 端点。同一条判据提前一次，避免一次注定失败的调用。 */
  const libraryPackMissingRoles = libraryPackImages.filter(item => !item.role).length;
  const libraryMode = !!libraryPackId;
  const [scopes, setScopes] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  /* 文本引用：占位符写在 scenePrompt / notes / replacementScope 里，
     提交时把绑定顺序的 id 放进 textLibraryIds（与参考图/素材包互不混用） */
  const [textRefs, setTextRefs] = useState<LibraryAssetOut[]>([]);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const detailId = params.get('run');
  const setDetailId = (id: string | null) => patchParams({run: id});
  const search = params.get('q') ?? '';
  /* 搜索要回到第 1 页：停在第 3 页上搜索，结果只有 1 页时列表会空，
     用户看到的是「没有找到相关模板」而不是「你在第 3 页」。与 setSourceScope 同口径。 */
  const setSearch = (value: string) => patchParams({q: value, sourcePage: '1'});
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
  /* 原片列表改读资产库 v2：`origin` 只要「切换后新传的」与「并存期导入的」两类
     ——按 `kind=video` 全取会把已生成的成片也列进来。入口用 `library`，与列表
     同一套判据：`public` 就是团队可见（公共原片池），不是另一个可见性档。 */
  const sourceSelection = useLibrarySelection({
    kind: 'video',
    origin: SOURCE_VIDEO_ORIGINS,
    library: sourceScope === 'public' ? 'team' : 'personal',
    keyword: search,
    page: sourcePage,
    pageSize: 24,
  });
  /* 深链（`?sourceId=`）带进来的原片可能不在当前页，单独取详情补齐卡片信息。 */
  const sourceDetail = useLibraryDetail(sourceId);
  const linkedSource: {data: SourceVideo | undefined; error: Error | null} = {
    data: sourceDetail.data ? sourceVideoOfLibrary(sourceDetail.data) : undefined,
    error: sourceDetail.error,
  };
  const catalog = useReplacementCatalog();
  const channels: ReplacementChannelOut[] = catalog.data?.channels?.length
    ? catalog.data.channels
    : [{id: 'libtv', label: 'LibTV', model: 'libtv-wan3-replacement', enabled: true, billing: 'libtv'}];
  const channelParam = params.get('channel');
  const channel: NonNullable<ReplacementInput['channel']> = channelParam === 'bailian' || channelParam === 'bailian-prime' ? channelParam : 'libtv';
  const currentChannel = channels.find(item => item.id === channel);
  const setChannel = (value: string) => patchParams({channel: value});
  const videoTemplates = useVideoTemplates();
  const prepare = usePrepareReplacement();
  const execute = useExecuteReplacement();
  const upload = useUploadSourceVideo();
  const template = TYPES.find(item => item.kind === kind) ?? TYPES[0];
  const videos: SourceVideo[] = (sourceSelection.data?.items ?? [])
    .filter(asset => asset.name.toLowerCase().includes(search.toLowerCase()))
    .map(sourceVideoOfLibrary);
  /* 组件仍按 `assets.isLoading` / `assets.data.items` 读，形状在这里对齐一次，
     免得每个卡片各判一遍「加载中」。 */
  const assets = {
    isLoading: sourceSelection.isPending,
    error: sourceSelection.error,
    refetch: sourceSelection.refetch,
    data: sourceSelection.data ? {items: videos, total: sourceSelection.data.total ?? 0} : undefined,
  };
  const currentDetail = catalog.data?.runs.find(run => run.id === detailId);
  const review = catalog.data?.runs.find(run => !run.deletedAt && run.id === reviewId);
  const busy = preparing || submitting || upload.isPending || batchPreparing;
  const packs = catalog.data?.packs?.filter(pack => pack.kind === kind) ?? [];
  const selectedPack = packs.find(pack => pack.id === packId);

  /* 视频模板卡片 = 原片 asset id + 绑定的素材包。封装后的模板和 0507 高端姐
     一样是「全部模板」里的普通卡片，区别只在选中时自动带出默认替换配置。
     提示词按用途取图，所以用户后续换成别的产品包也仍然套用同一条模板。
     仅认已绑定的原片 ID，避免把同名原片误认为已封装。 */
  function templateForSource(asset: {id: string; name: string} | undefined): VideoTemplateOut | undefined {
    return videoTemplatesForSource(videoTemplates.data?.items ?? [], asset)[0];
  }
  function templateKind(item: VideoTemplateOut): ReplacementKind {
    const raw = item.defaultKind
      ?? (typeof item.rules?.defaultKind === 'string' ? item.rules.defaultKind : 'product');
    return raw === 'character' || raw === 'scene' || raw === 'product' ? raw : 'product';
  }
  /* 选择卡片时在**同一次 URL 写入**里把替换类型与素材包一起带上，
     否则两次 patch 会互相覆盖（见文件头的注释）。 */
  function selectSource(id: string | null) {
    setExtraPackIds([]);
    setScopes([]);
    if (!id) { patchParams({sourceId: null, videoTemplateId: null}); return; }
    const asset = videos.find(video => video.id === id) ?? linkedSource.data ?? undefined;
    const binding = templateForSource(asset);
    if (!binding) { patchParams({sourceId: id, videoTemplateId: null}); return; }
    applyVideoTemplate(binding, id);
  }
  function applyVideoTemplate(binding: VideoTemplateOut, id = sourceId) {
    setExtraPackIds([]);
    setScopes([]);
    setTextRefs([]);
    updatePackId(binding.packId ?? '');
    setNotes('');
    setReviewId(null);
    patchParams({sourceId: id, kind: templateKind(binding), packId: binding.packId || null, packVersion: null, libPack: null, videoTemplateId: binding.id});
  }
  const boundTemplates = videoTemplatesForSource(videoTemplates.data?.items ?? [],
    sourceId ? videos.find(video => video.id === sourceId) ?? linkedSource.data ?? undefined : undefined,
  );
  const boundTemplate = boundTemplates.find(item => item.id === params.get('videoTemplateId'))
    ?? boundTemplates.find(item => templateKind(item) === kind) ?? boundTemplates[0];
  const isTextScene = kind === 'scene' && boundTemplate?.rules?.inputMode === 'video_scene_text';
  const defaultScenePrompt = typeof boundTemplate?.rules?.scenePromptDefault === 'string' ? boundTemplate.rules.scenePromptDefault : '';
  const [sceneDraft, setSceneDraft] = useState<{templateId: string; value: string} | null>(null);
  const scenePrompt = sceneDraft?.templateId === boundTemplate?.id ? sceneDraft?.value ?? defaultScenePrompt : defaultScenePrompt;
  function setScenePrompt(value: string) {setSceneDraft({templateId: boundTemplate?.id ?? '', value});}
  const functionOptions = boundTemplates.map(item => ({id: item.id,
    label: boundTemplates.filter(other => videoTemplateLabel(other) === videoTemplateLabel(item)).length > 1
      ? `${videoTemplateLabel(item)} · ${item.name}` : videoTemplateLabel(item),
    icon: TYPES.find(type => type.kind === templateKind(item))?.icon ?? TYPES[0].icon}));
  function selectFunction(id: string) {
    const binding = boundTemplates.find(item => item.id === id);
    if (binding) applyVideoTemplate(binding);
  }
  /* 只有配置确实还是模板那一套时才显示「固定提示词生效」：替换类型一致、
     且用户确实选了一个包。包 id 不锁死——后端按原片命中模板、按用途从当前包
     取图，换产品包时提示词仍是同一份，这正是提示词动态化的落点。 */
  const activeVideoTemplate = boundTemplate
    && (!!packId || libraryMode || isTextScene)
    && kind === templateKind(boundTemplate)
    ? boundTemplate : undefined;
  /* 模板声明了几个「正面身份图」槽位，就要求选几位人物：提示词里的
     `{{Mixed 2/3/4}}` 是三个人，而人物包一人一包、各一张正面图，
     一个包填不满三个槽位。槽位数来自封装时的 bindings，不在前端写死。 */
  const declaredBindings = boundTemplate?.rules?.referenceBindings;
  const characterSlots = kind === 'character'
    ? declaredBindings && typeof declaredBindings === 'object' && !Array.isArray(declaredBindings)
      ? Object.values(declaredBindings).filter(role => role === 'front').length
      : (boundTemplate?.mediaOrder ?? []).filter(item => item.type === 'image' && item.role === 'front' && item.inPrompt).length
    : 0;
  const slots = Math.max(1, characterSlots);
  const [extraPackIds, setExtraPackIds] = useState<string[]>([]);
  /* 换成槽位更少的模板/类型时丢掉多余的选人：带着第 3 个人去跑单人模板，
     后端会按槽位顺序多插一张图，编号就错位了。 */
  useEffect(() => {setExtraPackIds(previous => previous.slice(0, Math.max(0, slots - 1)));}, [slots]);
  const slotPacks = Array.from({length: slots}, (_, index) => (index === 0 ? packId : extraPackIds[index - 1] ?? ''));
  function toggleCharacterPack(id: string) {
    if (busy || kind !== 'character' || slots <= 1) return;
    const current = slotPacks.filter(Boolean);
    let next: string[];
    if (current.includes(id)) {
      next = current.filter(selected => selected !== id);
    } else {
      if (current.length >= slots || !isReplacementPackAvailable(packs.find(pack => pack.id === id))) return;
      next = [...current, id];
    }
    setExtraPackIds(next.slice(1));
    setPackId(next[0] ?? '');
  }
  const chosenPacks = slotPacks
    .map(id => packs.find(pack => pack.id === id))
    .filter((pack): pack is NonNullable<typeof pack> => !!pack);
  /* 深链进入时补一次默认配置。依赖写成**绑定签名**而不是 catalog / 模板对象：
     素材目录每 5 秒轮询会产出新引用，用对象做依赖会把用户手动改过的类型和包
     又改回去。最新一次渲染看到的值放 ref，签名没变就不重放 —— 这不是掩盖依赖，
     而是把真正的触发条件（原片 + 模板 + 类型）写成字符串。 */
  const bindingSignature = boundTemplate && catalog.data
    ? `${sourceId ?? ''}:${boundTemplate.id}:${templateKind(boundTemplate)}`
    : '';
  const applyBinding = useRef<() => void>(() => {});
  applyBinding.current = () => {
    const binding = boundTemplate;
    if (!binding || !catalog.data) return;
    if (params.get('videoTemplateId') === binding.id && kind === templateKind(binding)) return;
    const keepPack = kind === templateKind(binding) && !!params.get('packId');
    setExtraPackIds([]);
    updatePackId(keepPack ? params.get('packId')! : binding.packId ?? '');
    patchParams({kind: templateKind(binding), videoTemplateId: binding.id, libPack: null,
      ...(!keepPack ? {packId: binding.packId || null, packVersion: null} : {})});
  };
  useEffect(() => {
    if (bindingSignature) applyBinding.current();
  }, [bindingSignature]);
  /* 固定提示词的参数由封装定义；通用替换继续使用素材包审核范围。 */
  const scopeLabels = catalog.data?.packScopes ?? {};
  /* 资产库封装没有「运营审核过的替换范围」这一层元数据（那是模板封装的字段），
     所以给全量：范围仍必须由用户显式勾选，它进提示词变量。 */
  const availableScopes = boundTemplate ? videoTemplateScopes(boundTemplate)
    : libraryMode ? Object.values(scopeLabels)
    : (selectedPack?.supportedScopes ?? []).map(key => scopeLabels[key] ?? key);
  const showScope = boundTemplate ? availableScopes.length > 0 : true;
  /* 范围必须明确勾选。换包、换版本或目录刷新只剔除已不支持的位置，
     不能把可选范围当成用户选择，更不能自动扩大已经选定的范围。 */
  const scopeSignature = `${boundTemplate?.id ?? ''}:${libraryPackId}:${selectedPack?.id ?? ''}:${selectedPack?.version ?? 0}:${availableScopes.join('|')}`;
  const scopesToApply = useRef<string[]>([]);
  scopesToApply.current = availableScopes;
  useEffect(() => {
    setScopes(previous => previous.filter(scope => scopesToApply.current.includes(scope)));
  }, [scopeSignature]);
  /* 多人物：每个槽位贡献自己的参考图，顺序就是 `{{Mixed 2/3/4}}` 的顺序。
     单人 / 产品仍是「所选包的全部参考图」，行为一个字不变。 */
  /* 选了资产库封装时，参考图由**服务端**按成员展开（`libraryPackId`），前端不再
     传 `referenceIds`——两路同时给会被判成「两个参考来源」直接 400。这里的 id 清单
     只用于张数提示，不提交。 */
  const references = isTextScene ? [] : libraryMode
    ? libraryPackImages.map(item => item.assetId)
    : slots > 1
    ? [...new Set(chosenPacks.flatMap(pack => pack.referenceIds))]
    : selectedPack?.referenceIds ?? [];
  const target = isTextScene ? '文字换场景' : libraryMode
    ? libraryPack?.name ?? ''
    : slots > 1
    ? chosenPacks.map(pack => pack.name).join('、')
    : selectedPack?.name ?? '';
  /* v2 封装没有「服装模式」声明：保持原片服装是最保守的一档 */
  const wardrobeMode = libraryMode ? 'preserve' : selectedPack?.wardrobeMode ?? 'preserve';
  const requestedVersion = params.get('packId') === packId ? params.get('packVersion') : null;
  const everyoneChosen = chosenPacks.length === slots;
  const templatePackReady = isTextScene || everyoneChosen
    && chosenPacks.every(pack => isReplacementPackAvailable(pack))
    && (!requestedVersion || String(selectedPack?.version) === requestedVersion);
  /* 资产库封装：只要拉到了成员就算选好；用途缺失与张数超限单独提示（见 `missing`），
     不在这里混成一个「请选择素材包」。 */
  const packReady = libraryMode ? !!libraryPack && libraryPackImages.length > 0 : templatePackReady;
  /* 与后端 `require_channel` 同口径：LibTV 允许「执行期才注册 provider」的安装方式，
     目录里的 enabled=false 只用于在选择器里灰显，不能变成提交门禁。否则一个没注册
     LibTV provider 的部署（新机器、本地联调）整站都点不了「下一步」。 */
  const channelBlocked = channel !== 'libtv' && !currentChannel?.enabled;
  const missing = channelBlocked ? currentChannel?.unavailableReason || '所选模型渠道暂不可用'
    : isTextScene && !scenePrompt.trim() ? '请填写场景描述'
    /* 文字换场景允许 {{Text N}} 文本引用占位符，但其它手写模板括号仍拒绝 */
    : isTextScene && (scenePrompt.length > 4000 || hasForeignMustache(scenePrompt)) ? '场景描述最多 4000 字，只允许 {{Text N}} 文本引用占位符'
    : slots > 1 && !everyoneChosen ? `还需选择 ${slots - chosenPacks.length} 位人物`
    : libraryMode && libraryPackMissingRoles > 0
      ? `封装里还有 ${libraryPackMissingRoles} 张图没声明用途，替换时不知道该当主图还是细节图；请到资产库封装页补上`
    : references.length > 10 ? `所选人物参考图合计 ${references.length} 张，最多支持 10 张，请选择更精简的角色卡`
    : !packReady ? `请选择一个可用的${template.short}素材包`
    : showScope && (boundTemplate || kind === 'product') && !scopes.length ? '请选择至少一个产品替换位置'
    : videoTemplates.isPending || videoTemplates.error ? '请等待模板封装配置加载完成' : '';
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
      /* 额外人物包的版本只在「确实拿到了版本号」时才传：契约里它是可选的，
         旧行可能没带 version。以前用 `|| 1` 补一个假版本，服务端会拿 1 去查发布版本，
         查不到就报「额外人物包的所选发布版本不存在」——比省略更难排查。 */
      const extraIds = slots > 1 ? slotPacks.slice(1).filter(Boolean) : [];
      const extraVersions = extraIds
        .map(id => chosenPacks.find(pack => pack.id === id)?.version)
        .filter((value): value is number => typeof value === 'number');
      /* 两路参考来源只能给一路：选了资产库封装就**不带** `referenceIds` / `packId`，
         带了会被服务端判成「同时给了两个来源」直接 400（那边刻意不做谁优先）。 */
      const config: Omit<ReplacementInput, 'requestId'> = {kind, channel, target: target.trim(),
        sourceLibraryId: sourceId ?? '',
        referenceIds: libraryMode ? [] : references, wardrobeMode,
        ...(libraryMode ? {libraryPackId} : {}),
        packId: isTextScene || libraryMode ? null : packId || null,
        packVersion: isTextScene || libraryMode ? null : selectedPack?.version || 1, videoTemplateId: boundTemplate?.id,
        ...(isTextScene ? {scenePrompt} : {}),
        extraPackIds: extraIds,
        ...(extraIds.length && extraVersions.length === extraIds.length ? {extraPackVersions: extraVersions} : {}),
        replacementScope: showScope ? scopes.join('、') : '', notes: isTextScene ? '' : notes, aspectRatio: 'adaptive',
        ...(textRefs.length ? {textLibraryIds: textRefs.map(item => item.id)} : {})};
      const key = JSON.stringify(config);
      const previous = catalog.data?.runs.find(run => run.id === preparedIds.current.get(key));
      if (previous?.deletedAt) requestIds.current.delete(key);
      if (previous && !previous.deletedAt && !['preparing', 'prepare_failed'].includes(previous.status)) {
        setReviewId(previous.status === 'prepared' ? previous.id : null);
        report('success', previous.status === 'prepared' ? '输入已保存，请核对后确认生成。' : '该配置已有调用记录，请在任务记录中查看进度或结果。');
        return;
      }
      let requestId = requestIds.current.get(key);
      if (!requestId) {requestId = createId(); requestIds.current.set(key, requestId);}
      const result = await prepare.mutateAsync({...config, requestId});
      preparedIds.current.set(key, result.id);
      setReviewId(result.status === 'prepared' ? result.id : null);
      report('success', result.status === 'prepared' ? '输入已保存，请核对后确认生成。' : '该配置已有调用记录，请在任务记录中查看进度或结果。');
    } catch (reason) {
      report('error', isStateChanged(reason)
        ? '任务状态刚被另一处操作更新，已刷新到最新状态，请确认后重试。'
        : `准备失败：${reason instanceof Error ? reason.message : '输入准备未完成'}。可在当前配置下重试，不会重复创建任务。`);
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
      const updated = await execute.mutateAsync({id: run.id, billingMode: run.channel && run.channel !== 'libtv' ? undefined : billingMode});
      if (updated && ['failed', 'timeout', 'service_error', 'needs_login', 'submission_unknown'].includes(updated.status)) {
        report('error', updated.lastError || NEXT_ACTION[updated.status] || '任务未恢复，请查看调用档案中的失败原因。');
        return;
      }
      setReviewId(null);
      /* 反馈跟着档案的真实状态走：重复点确认时后端只查询不重提，说「已请求提交」
         会让用户以为又排了一条，说「已在生成中」才是他看到的事实。 */
      const live = updated && ['queued', 'uploading', 'uploaded', 'submitting', 'submitted', 'running', 'generated'].includes(updated.status);
      report('success', updated?.status === 'success'
        ? '这条任务已经生成完成，成片可在任务记录中播放或下载。'
        : live
        ? run.taskId && RETRY.includes(run.status)
          ? `已恢复原任务查询（进度 ${updated?.progress ?? 0}%），不会重复提交或扣费。`
          : `任务已在执行中（进度 ${updated?.progress ?? 0}%），状态已刷新；重复点击不会重复提交。`
        : run.channel && run.channel !== 'libtv'
        ? '已处理任务的执行请求，使用阿里云 API 计费，请在任务记录中查看最新状态。'
        : billingMode === 'credits'
        ? '已请求用积分加速：这条任务将按积分报价进入正常队列，实际扣费以任务记录为准。'
        : '已处理任务的执行请求，请在任务记录中查看最新状态；仅在估价为 0 积分时提交生成。');
    } catch (reason) {
      report('error', isStateChanged(reason)
        ? '任务状态刚被另一处操作更新，已刷新到最新状态；请核对后再点一次（不会重复提交或扣费）。'
        : `执行请求失败：${reason instanceof Error ? reason.message : '请求未完成'}`);
    } finally {operationLock.current = false; setSubmitting(false);}
  }
  function handleUpload(event: ChangeEvent<HTMLInputElement>) {
      const file = event.target.files?.[0]; if (!file || busy) return;
      upload.mutate({file}, {onSuccess: asset => {
        const duration = asset.durationSec ?? 0;
        if (!asset.hasAudio || duration < 1 || duration > 15) {
          report('success', `“${asset.name}”已保存，但不符合 1–15 秒且含音轨的要求，未加入选择。请换一条合规原片。`);
          return;
        }
        // 选择与来源切换必须一次写完：两次 patch 会互相覆盖。
        patchParams({sourceId: asset.id, sourceScope: 'mine', sourcePage: '1'});
        report('success', `“${asset.name}”已保存到素材库。请选择已封装的素材包后继续。`);
      }, onError: reason => report('error', `素材上传失败：${reason.message}。请检查文件后重新上传。`)});
      event.target.value = '';

  }
  return { boundTemplates, channel, setChannel, channels, currentChannel, isTextScene, scenePrompt, setScenePrompt, defaultScenePrompt, setBatchPreparing, sourceScope, setSourceScope, sourcePage, setSourcePage, linkedSource, params, kind, setKind, sourceId, setSourceId, selectSource, references, target, packId, setPackId, libraryPackId, setLibraryPackId, libraryPack, libraryPackImages, libraryPackMissingRoles, libraryMode, wardrobeMode, selectedPack, packs, scopes, setScopes, availableScopes, showScope, functionOptions, selectFunction, notes, setNotes, textRefs, setTextRefs, reviewId, setReviewId, detailId, setDetailId, search, setSearch, historyTrash, historyStatus, historySearch, setHistoryFilter, preparing, submitting, feedback, setFeedback, uploadInput, assets, catalog, upload, template, videos, currentDetail, review, busy, missing, toggle, pickUpload, prepareTask, submitRun, handleUpload, videoTemplates, templateForSource, boundTemplate, activeVideoTemplate, slots, slotPacks, toggleCharacterPack, chosenPacks };
}

export type ReplacementWorkbench = ReturnType<typeof useReplacementWorkbench>;
