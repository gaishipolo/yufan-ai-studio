import type { MockRoute } from '../kernel';

/** 与后端 echo 供应商声明保持一致，便于 mock / 真实后端切换时无差异 */
const MOCK_MODELS = [
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

function listModels(capability: unknown) {
  const value = capability ? String(capability) : '';
  const items = value ? MOCK_MODELS.filter((m) => m.capability === value) : MOCK_MODELS;
  return { items, total: items.length };
}

export const routes: MockRoute[] = [
  { method: 'GET', pattern: /^\/models$/, handler: (ctx) => listModels(ctx.params.capability) },
];
