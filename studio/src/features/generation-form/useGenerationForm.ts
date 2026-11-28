import { useMemo, useState } from 'react';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { toast } from '@/shared/lib/toast';
import type { LibraryAssetDrag } from '@/shared/lib/libraryDrag';
import {
  REFERENCE_KIND_META,
  type ReferenceItem,
} from '@/shared/ui';
import {
  referenceKindsFor,
  referenceLimitsFor,
} from '@/entities/model/api/model.queries';
import {
  isEmptyParamValue,
  paramControlsFor,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import type { ModelSpec } from '@/entities/model/model/types';
import type { GenerateImagePayload } from '@/entities/image/model/types';
import type { CreateTaskPayload } from '@/entities/task/model/types';
import { useUploadAsset } from '@/entities/library/api/library.queries';
import {
  toSubmitPayload,
  type SubmitKind,
} from './taskPayload';
import { useGenerationSubmit } from './useGenerationSubmit';

export type GenerationPayload = CreateTaskPayload | GenerateImagePayload;

export interface UseGenerationFormOptions<R> {
  /** 直接页面的能力：决定参数默认值与错误文案，不决定提交接口（payload 自己带形状）。 */
  kind: SubmitKind;
  models: ModelSpec[];
  /** 模型列表加载失败时，页面仍拿得到空数组；这里只负责表单。 */
  promptLimit?: number;
  /** Studio 的素材库入口受 `library:read` 控制；Image 传 true。 */
  canReadLibrary?: boolean;
  /** 页面级权限（Image 的 `image:create`）；false 时禁用提交。 */
  canSubmit?: boolean;
  /** Studio 用提示词前 40 字当任务名；Image 不传 name。 */
  titleFromPrompt?: (prompt: string) => string | undefined;
  /** 直接生图页：用户没显式选模型时不把默认模型发出去。 */
  omitDefaultModel?: boolean;
  /** 生图页成功后清空提示词；视频页成功后跳任务中心，保留输入。 */
  clearPromptOnSuccess?: boolean;
  submit: (payload: GenerationPayload) => Promise<R>;
  onSuccess: (result: R) => void;
  onError?: (message: string) => void;
  /** 表单前置校验（空提示词 / 无模型）：Image 页用 toast，Studio 页用行内文案。 */
  onValidationError?: (message: string) => void;
}

/**
 * 直接生成页的共享表单：模型/参数/参考素材/prompt/换模型确认与提交。
 *
 * 页面负责布局、权限和成功后的导航/清页；这里只收起那些“两个页面写得一模一样、
 * 却很容易漂移”的规则。
 */
export function useGenerationForm<R>({
  kind,
  models,
  promptLimit,
  canReadLibrary = true,
  canSubmit = true,
  titleFromPrompt,
  omitDefaultModel = false,
  clearPromptOnSuccess = false,
  submit,
  onSuccess,
  onError,
  onValidationError,
}: UseGenerationFormOptions<R>) {
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  const [overrides, setOverrides] = useState<Record<string, ParamValue>>({});
  const [references, setReferences] = useState<ReferenceItem[]>([]);
  const [textRefs, setTextRefs] = useState<LibraryAssetOut[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [pendingModelId, setPendingModelId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const upload = useUploadAsset();

  const currentModel = useMemo(
    () => models.find((item) => item.id === modelId) ?? models.find((m) => m.isDefault) ?? models[0],
    [models, modelId],
  );
  const modelOptions = useMemo(
    () => models.map((item) => ({ value: item.id, label: item.label })),
    [models],
  );
  const paramControls = useMemo(() => paramControlsFor(currentModel), [currentModel]);
  const paramValues = useMemo(
    () => resolveParamValues(paramControls, overrides),
    [paramControls, overrides],
  );
  const referenceKinds = useMemo(() => referenceKindsFor(currentModel), [currentModel]);
  const referenceLimits = useMemo(() => referenceLimitsFor(currentModel), [currentModel]);
  const maxReferences = currentModel?.maxReferences ?? 0;

  const submission = useGenerationSubmit<GenerationPayload, R>({
    submit,
    onSuccess: result => {
      // 参考素材跟这次请求走了；留着会让下一次生成莫名其妙沿用旧图。
      setReferences([]);
      setTextRefs([]);
      setLibraryOpen(false);
      if (clearPromptOnSuccess) setPrompt('');
      onSuccess(result);
    },
    onError: message => {
      setError(message);
      onError?.(message);
    },
  });

  const countOfKind = (kindName: string) =>
    references.filter((one) => (one.kind ?? 'image') === kindName).length;

  const uploadKindFor = (file: File): 'image' | 'audio' | null => {
    if (file.type.startsWith('audio/')) {
      return referenceKinds.includes('audio') ? 'audio' : null;
    }
    return 'image';
  };

  const rejectMessage = (kindName: string, limit: number): string => {
    const meta = REFERENCE_KIND_META[kindName];
    return meta
      ? `最多 ${limit} ${meta.unit}${meta.label}参考素材，先移除一个再添加`
      : `最多 ${limit} 个 ${kindName} 参考素材，先移除一个再添加`;
  };

  const addReferences = async (files: FileList | null) => {
    const picked = Array.from(files ?? []);
    if (!picked.length) return;
    /* `setReferences` 是异步的：一批里连着处理几张时读 `references` 还是旧值，
       会一次塞进超额的量、到提交才被拒。 */
    const used: Record<string, number> = {};
    for (const item of references) {
      const itemKind = item.kind ?? 'image';
      used[itemKind] = (used[itemKind] ?? 0) + 1;
    }
    let total = references.length;

    for (const file of picked) {
      const fileKind = uploadKindFor(file);
      if (!fileKind) {
        toast('这个模型不收音频参考素材', 'error');
        continue;
      }
      if (total >= maxReferences) {
        toast(`最多 ${maxReferences} 个参考素材，先移除一个再添加`, 'error');
        break;
      }
      const limit = referenceLimits[fileKind];
      if (limit !== undefined && (used[fileKind] ?? 0) >= limit) {
        toast(rejectMessage(fileKind, limit), 'error');
        continue;
      }
      try {
        const asset = await upload.mutateAsync({ file, kind: fileKind });
        setReferences((prev) => [
          ...prev,
          {
            id: asset.id,
            name: asset.name,
            url: asset.previewUrl ?? asset.url ?? '',
            kind: fileKind,
          },
        ]);
        used[fileKind] = (used[fileKind] ?? 0) + 1;
        total += 1;
      } catch (reason) {
        toast(reason instanceof Error ? reason.message : '参考素材上传失败', 'error');
      }
    }
  };

  const addLibraryAsset = (asset: LibraryAssetDrag) => {
    const assetKind = asset.kind ?? 'image';
    if (references.some((item) => item.id === asset.id)) return;
    if (!referenceKinds.includes(assetKind)) {
      const meta = REFERENCE_KIND_META[assetKind];
      toast(`这个模型不收${meta ? meta.label : assetKind}参考素材`, 'error');
      return;
    }
    const limit = referenceLimits[assetKind];
    if (limit !== undefined && countOfKind(assetKind) >= limit) {
      toast(rejectMessage(assetKind, limit), 'error');
      return;
    }
    if (references.length >= maxReferences) {
      toast(`最多 ${maxReferences} 个参考素材，先移除一个再添加`, 'error');
      return;
    }
    setReferences((prev) => [
      ...prev,
      { id: asset.id, name: asset.name, url: asset.url, kind: assetKind },
    ]);
  };

  const removeReference = (assetId: string) => {
    setReferences((prev) => prev.filter((item) => item.id !== assetId));
  };

  const applyModel = (nextId: string) => {
    setModelId(nextId);
    setReferences([]);
    setLibraryOpen(false);
    setOverrides({});
  };

  const changeModel = (nextId: string) => {
    if (nextId === (currentModel?.id ?? '')) return;
    if (references.length > 0) {
      setPendingModelId(nextId);
      return;
    }
    applyModel(nextId);
  };

  const confirmModelChange = () => {
    if (pendingModelId) applyModel(pendingModelId);
    setPendingModelId(null);
  };

  const cancelModelChange = () => setPendingModelId(null);

  const submitForm = async () => {
    if (!prompt.trim()) {
      const message = kind === 'video' ? '请先填写画面描述' : '请先填写提示词';
      setError(message);
      onValidationError?.(message);
      return;
    }
    if (!currentModel) {
      const message =
        kind === 'video' ? '暂无可用模型，请稍后重试' : '暂无可用生图模型，请稍后重试';
      setError(message);
      onValidationError?.(message);
      return;
    }
    setError('');

    const compiled = {
      version: 0,
      model: currentModel.id,
      prompt: prompt.trim(),
      params: Object.fromEntries(
        Object.entries(paramValues).filter(([, value]) => !isEmptyParamValue(value)),
      ),
      referenceLibraryIds: references.map((item) => item.id),
      textLibraryIds: textRefs.map((item) => item.id),
      slots: [],
    };
    const defaults = {
      durationSec:
        Number(currentModel.paramSchema?.durationSec?.default ?? 0) || undefined,
      aspectRatio:
        (currentModel.paramSchema?.aspectRatio?.default as string | undefined) ?? undefined,
      size: (currentModel.paramSchema?.size?.default as string | undefined) ?? undefined,
      count: Number(currentModel.paramSchema?.count?.default ?? 0) || undefined,
    };
    const plan = toSubmitPayload(
      compiled,
      titleFromPrompt?.(prompt.trim()) ?? '',
      kind,
      defaults,
      currentModel,
    );
    if (!plan.ok) {
      setError(plan.message);
      return;
    }

    let payload: GenerationPayload = plan.payload;
    if (plan.kind === 'image') {
      // 直接生图页以前不发空数组：没有参考/文本时字段直接不存在。
      const image = { ...plan.payload };
      if (!image.referenceLibraryIds?.length) delete image.referenceLibraryIds;
      if (!image.textLibraryIds?.length) delete image.textLibraryIds;
      if (omitDefaultModel && currentModel.isDefault) delete image.model;
      payload = image;
    }
    await submission.submit(payload);
  };

  return {
    prompt,
    setPrompt,
    promptLimit,
    currentModel,
    modelOptions,
    changeModel,
    pendingModelId,
    pendingModel: models.find((item) => item.id === pendingModelId),
    confirmModelChange,
    cancelModelChange,
    paramControls,
    paramValues,
    setParam: (key: string, value: ParamValue) =>
      setOverrides((prev) => ({ ...prev, [key]: value })),
    references,
    referenceKinds,
    referenceLimits,
    maxReferences,
    addReferences: (files: FileList | null) => void addReferences(files),
    addLibraryAsset,
    removeReference,
    textRefs,
    setTextRefs,
    libraryOpen,
    setLibraryOpen,
    canReadLibrary,
    uploading: upload.isPending,
    error,
    setError,
    submit: submitForm,
    pending: submission.pending,
    canSubmit: Boolean(currentModel) && canSubmit,
  };
}
