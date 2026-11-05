import { useQuery } from '@tanstack/react-query';
import type { AspectRatio } from '@/entities/task/model/types';
import { modelApi } from './model.api';
import type { Capability, ModelSpec } from '../model/types';

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

/** 按模型声明的 `durationSec` schema 生成时长选项（含兜底） */
export function durationOptionsFor(model: ModelSpec | undefined): number[] {
  const schema = model?.paramSchema?.durationSec;
  const min = Math.max(1, Math.round(schema?.min ?? 5));
  // 供应商把 max 配成 0 / 小于 min 时不能让 0 秒成为可选项：至少收敛到 min
  const max = Math.max(min, Math.round(schema?.max ?? 15));
  const step = Math.max(1, Math.round(schema?.step ?? 5));

  const options: number[] = [];
  for (let value = min; value <= max; value += step) options.push(value);
  // 上限本身也要能选到（例如 max=12、step=5 → 5/10/12）
  if (options[options.length - 1] !== max) options.push(max);
  return options;
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
