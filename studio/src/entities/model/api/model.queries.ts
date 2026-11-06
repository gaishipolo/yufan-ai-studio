import { useQuery } from '@tanstack/react-query';
import type { AspectRatio } from '@/entities/task/model/types';
import { modelApi } from './model.api';
import type { Capability, ModelSpec, ParamField } from '../model/types';

export const modelKeys = {
  all: ['models'] as const,
  list: (capability: Capability) => [...modelKeys.all, capability] as const,
};

/**
 * 离线 / mock 模式下的兜底清单。
 * 只在接口不可用时使用 —— 正常情况下模型列表完全由后端提供，
 * 新增模型不需要改前端（这正是 /models 接口存在的意义）。
 */
export const FALLBACK_MODELS: ModelSpec[] = [
  {
    id: 'seedance-2.5',
    label: 'Seedance 2.5',
    capability: 'video',
    provider: 'echo',
    description: '通用短视频生成，支持首尾帧参考',
    paramSchema: {
      durationSec: { type: 'int', min: 5, max: 12, default: 5 },
      aspectRatio: { type: 'enum', options: ['9:16', '16:9', '1:1'], default: '9:16' },
    },
    isDefault: true,
  },
  {
    id: 'kling-2.1',
    label: '可灵 2.1',
    capability: 'video',
    provider: 'echo',
    description: '写实风格运镜，适合商品与人物',
    paramSchema: {
      durationSec: { type: 'int', min: 5, max: 12, default: 5 },
      aspectRatio: { type: 'enum', options: ['9:16', '16:9', '1:1'], default: '9:16' },
    },
    isDefault: false,
  },
];

/** 可用模型列表：接口失败时退回内置清单，保证选择器永远有值 */
export function useModels(capability: Capability = 'video'): ModelSpec[] {
  const { data } = useQuery({
    queryKey: modelKeys.list(capability),
    queryFn: () => modelApi.list(capability),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const items = data?.items?.filter((item) => item.capability === capability);
  return items?.length ? items : FALLBACK_MODELS.filter((item) => item.capability === capability);
}

/**
 * 把 int 型 paramSchema 展开成可选档位。
 * 非法配置（0 / 负数 / max < min / step = 0 / NaN）一律收敛到至少一个合法值，
 * 保证 Select 不会渲染空列表，也不会让用户选到「0 张」这种必被后端拒的值。
 */
function intRange(
  field: ParamField | undefined,
  fallback: { min: number; max: number; step: number },
): number[] {
  const read = (value: unknown, defaultValue: number) => {
    const num = Number(value);
    return Number.isFinite(num) ? Math.round(num) : defaultValue;
  };
  const min = Math.max(1, read(field?.min ?? fallback.min, fallback.min));
  const max = Math.max(min, read(field?.max ?? fallback.max, fallback.max));
  const step = Math.max(1, read(field?.step ?? fallback.step, fallback.step));

  const options: number[] = [];
  for (let value = min; value <= max; value += step) options.push(value);
  // 上限本身也要能选到（例如 max=12、step=5 → 5/10/12）
  if (options[options.length - 1] !== max) options.push(max);
  return options;
}

/** 按模型声明的 `durationSec` schema 生成时长选项（含兜底） */
export function durationOptionsFor(model: ModelSpec | undefined): number[] {
  return intRange(model?.paramSchema?.durationSec, { min: 5, max: 15, step: 5 });
}

const RATIO_LABEL: Record<string, string> = {
  '9:16': '9:16 竖版',
  '16:9': '16:9 横版',
  '1:1': '1:1 方形',
};

/**
 * 按模型声明的 `aspectRatio.options` 生成比例选项。
 * 比例同样是「模型能力」的一部分：某些模型不支持方形，就不该让用户选。
 */
export function aspectOptionsFor(
  model: ModelSpec | undefined,
): Array<{ value: AspectRatio; label: string }> {
  const options = model?.paramSchema?.aspectRatio?.options;
  const values =
    Array.isArray(options) && options.length ? options.map(String) : ['9:16', '16:9', '1:1'];
  return values.map((value) => ({ value: value as AspectRatio, label: RATIO_LABEL[value] ?? value }));
}

/* ---------------- 生图表单：参数完全由模型 paramSchema 决定 ---------------- */

export interface ImageFormSchema {
  sizeOptions: string[];
  sizeDefault: string;
  countOptions: number[];
  countDefault: number;
}

/** 没有可用模型时给空档位：表单据此禁用提交，不编造尺寸 / 张数默认值 */
const EMPTY_IMAGE_FORM_SCHEMA: ImageFormSchema = {
  sizeOptions: [],
  sizeDefault: '',
  countOptions: [],
  countDefault: 0,
};

/**
 * 从 `GET /models?capability=image` 的 paramSchema 派生生图表单。
 *
 * 尺寸白名单与张数上限都按模型变（实测 agnes count.max=1、seedream count.max=4），
 * 写死常量会在「换模型 / 换供应商 / 后端收紧校验」三种情况下让用户选什么都被 400。
 */
export function imageFormSchemaFor(model: ModelSpec | undefined): ImageFormSchema {
  if (!model) return EMPTY_IMAGE_FORM_SCHEMA;

  const sizeField = model.paramSchema.size;
  const sizeOptions = (sizeField?.options ?? []).map(String).filter(Boolean);
  const declaredSize = sizeField?.default == null ? '' : String(sizeField.default);
  const sizeDefault = sizeOptions.includes(declaredSize) ? declaredSize : (sizeOptions[0] ?? '');

  // 生图张数是连续整数，步长默认 1；不复用视频时长的 5 秒一档
  const countOptions = intRange(model.paramSchema.count, { min: 1, max: 1, step: 1 });
  const declaredCount = Number(model.paramSchema.count?.default);
  const countDefault = countOptions.includes(declaredCount) ? declaredCount : countOptions[0];

  return { sizeOptions, sizeDefault, countOptions, countDefault };
}

/**
 * 生图模型目录。与 `useModels` 分开：不给 image 造 FALLBACK_MODELS，
 * 拿不到目录就明确显示「暂无可用模型」，避免拿一套假参数提交上去再被后端拒。
 * queryKey 与 `useModels('image')` 同源，同一页面重复订阅不会多发请求。
 */
export function useImageModels(): { models: ModelSpec[]; isLoading: boolean; isError: boolean } {
  const query = useQuery({
    queryKey: modelKeys.list('image'),
    queryFn: () => modelApi.list('image'),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  return {
    models: (query.data?.items ?? []).filter((item) => item.capability === 'image'),
    isLoading: query.isLoading,
    isError: query.isError,
  };
}
