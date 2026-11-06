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
  // 生图模型与真实后端 /models?capability=image 保持一致：
  // 两个模型的 count 上限不同（1 / 4），正好用来验证表单没有写死张数
  {
    id: 'agnes-image-2.5-flash',
    label: 'Agnes Image 2.5 Flash',
    capability: 'image',
    provider: 'agnes',
    description: '文生图，同步返回图片地址',
    paramSchema: {
      size: { type: 'enum', options: ['1024x1024', '1024x1536', '1536x1024'], default: '1024x1024' },
      count: { type: 'int', min: 1, max: 1, default: 1 },
    },
    isDefault: true,
  },
  {
    id: 'seedream-3',
    label: 'Seedream 3',
    capability: 'image',
    provider: 'echo',
    description: '',
    paramSchema: {
      size: { type: 'enum', options: ['1024x1024', '1024x1536', '1536x1024'], default: '1024x1024' },
      count: { type: 'int', min: 1, max: 4, default: 1 },
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
