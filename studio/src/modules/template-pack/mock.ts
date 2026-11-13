import { HttpError, ok, type MockContext, type MockRoute } from '@/mocks/kernel';
import type {
  TemplatePackCandidateListOut,
  TemplatePackOut,
  TemplatePackSlotIn,
} from '@/shared/api/schema';

/**
 * 封装模板 · 全端点 mock。
 *
 * **筛选语义必须真的生效**：mock 若直接返回全部素材，前端开发时会以为
 * 「筛选条件没起作用」，于是把一个真 bug 当成 mock 的怪癖放过。所以这里
 * 按后端同一套口径筛（类别精确匹配 + 标签命中），包括「一级标签展开成
 * 自身 + 直属二级」这条 —— 那是最容易漏的一条。
 */

interface MockSlot {
  position: number;
  token: string;
  slotKind: 'fixed' | 'filtered';
  label: string;
  assetId: string | null;
  mediaType: string;
  assetName: string;
  filterKind: string;
  filterTagIds: string[];
  filterTagMatch: string;
  required: boolean;
}

interface MockPack {
  id: string;
  templateKey: string;
  name: string;
  description: string;
  prompt: string;
  promptSha256: string;
  sourceVideoAssetId: string;
  sourceDurationSec: number;
  aspectRatio: string;
  coverUrl: string;
  status: 'draft' | 'published' | 'disabled';
  ownerId: string;
  slots: MockSlot[];
  publishedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

const now = new Date().toISOString();

/** 演示用素材池。tagId 对应下面的 TAGS。 */
const ASSETS = [
  {id: 'a1', name: '产品正面.png', kind: 'image', tagIds: ['t2'], coverUrl: ''},
  {id: 'a2', name: '产品侧面.png', kind: 'image', tagIds: ['t2', 't5'], coverUrl: ''},
  {id: 'a3', name: '产品细节.png', kind: 'image', tagIds: ['t5'], coverUrl: ''},
  {id: 'a4', name: '人物照.png', kind: 'image', tagIds: ['t1'], coverUrl: ''},
  {id: 'a5', name: '开场视频.mp4', kind: 'video', tagIds: ['t1'], coverUrl: ''},
];

/** 标签词表。t2 是一级，t5 是 t2 的二级。 */
const TAGS = [
  {id: 't1', name: '人物', parentId: ''},
  {id: 't2', name: '产品', parentId: ''},
  {id: 't5', name: '白底', parentId: 't2'},
];

const PACKS: MockPack[] = [
  {
    id: 'tp1',
    templateKey: 'tp-demo-1',
    name: '产品主图模板',
    description: '演示用：第 2 槽只能选「产品」标签的图',
    prompt: '按 {{Mixed 1}} 的节奏，用 {{Mixed 2}} 做主图',
    promptSha256: 'demo',
    sourceVideoAssetId: 'a5',
    sourceDurationSec: 5,
    aspectRatio: '9:16',
    coverUrl: '',
    status: 'published',
    ownerId: 'me',
    slots: [
      {
        position: 1,
        token: '{{Mixed 1}}',
        slotKind: 'fixed',
        label: '参考视频',
        assetId: 'a5',
        mediaType: 'video',
        assetName: '开场视频.mp4',
        filterKind: '',
        filterTagIds: [],
        filterTagMatch: 'any',
        required: true,
      },
      {
        position: 2,
        token: '{{Mixed 2}}',
        slotKind: 'filtered',
        label: '产品主图',
        assetId: null,
        mediaType: 'image',
        assetName: '',
        filterKind: 'image',
        filterTagIds: ['t2'],
        filterTagMatch: 'any',
        required: true,
      },
    ],
    publishedAt: now,
    createdAt: now,
    updatedAt: now,
  },
  {
    id: 'tp2',
    templateKey: 'tp-demo-2',
    name: '我的草稿',
    description: '演示用：草稿只有作者可见',
    prompt: '用 {{Mixed 2}} 做主图',
    promptSha256: 'demo',
    sourceVideoAssetId: 'a5',
    sourceDurationSec: 5,
    aspectRatio: '9:16',
    coverUrl: '',
    status: 'draft',
    ownerId: 'me',
    slots: [
      {
        position: 1,
        token: '{{Mixed 1}}',
        slotKind: 'fixed',
        label: '参考视频',
        assetId: 'a5',
        mediaType: 'video',
        assetName: '开场视频.mp4',
        filterKind: '',
        filterTagIds: [],
        filterTagMatch: 'any',
        required: true,
      },
    ],
    publishedAt: null,
    createdAt: now,
    updatedAt: now,
  },
];

/**
 * 选中一级标签时展开成「自身 + 直属二级」—— 与资产库筛选同一口径。
 *
 * 少了这一步，「选了产品却选不到挂在白底下的图」，看起来像 bug。
 */
function expandedTagIds(tagIds: string[]): Set<string> {
  const out = new Set<string>();
  for (const id of tagIds) {
    out.add(id);
    for (const tag of TAGS) {
      if (tag.parentId === id) out.add(tag.id);
    }
  }
  return out;
}

/** 类别精确匹配 + 标签命中（任一即可）。 */
function matches(assetId: string, filterKind: string, tagIds: string[]): boolean {
  const asset = ASSETS.find(item => item.id === assetId);
  if (!asset) return false;
  if (asset.kind !== filterKind) return false;
  if (!tagIds.length) return true;
  const allowed = expandedTagIds(tagIds);
  return asset.tagIds.some(tagId => allowed.has(tagId));
}

function findPack(id: string): MockPack {
  const pack = PACKS.find(item => item.id === id);
  if (!pack) throw new HttpError(404, '封装模板不存在');
  return pack;
}

function toOut(pack: MockPack): TemplatePackOut {
  return {
    id: pack.id,
    templateKey: pack.templateKey,
    name: pack.name,
    description: pack.description,
    prompt: pack.prompt,
    promptSha256: pack.promptSha256,
    sourceVideoAssetId: pack.sourceVideoAssetId,
    sourceDurationSec: pack.sourceDurationSec,
    aspectRatio: pack.aspectRatio,
    coverUrl: pack.coverUrl,
    status: pack.status,
    ownerId: pack.ownerId,
    slots: pack.slots,
    publishedAt: pack.publishedAt,
    createdAt: pack.createdAt,
    updatedAt: pack.updatedAt,
  };
}

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/template-packs$/,
    handler: (ctx: MockContext) => {
      const status = String(ctx.params.status ?? '');
      const items = PACKS.filter(pack => (status ? pack.status === status : true)).map(toOut);
      return ok({items, total: items.length});
    },
  },
  {
    method: 'POST',
    pattern: /^\/template-packs$/,
    status: 201,
    handler: (ctx: MockContext) => {
      const body = ctx.body as Record<string, unknown>;
      const slots = ((body.slots as TemplatePackSlotIn[]) ?? []).map((slot, index) => ({
        position: index + 1,
        token: slot.token,
        slotKind: slot.slotKind,
        label: slot.label ?? '',
        assetId: slot.assetId ?? null,
        mediaType: slot.mediaType ?? 'image',
        assetName: '',
        filterKind: slot.filterKind ?? '',
        filterTagIds: slot.filterTagIds ?? [],
        filterTagMatch: slot.filterTagMatch ?? 'any',
        required: slot.required ?? true,
      }));
      const pack: MockPack = {
        id: `tp${PACKS.length + 1}`,
        templateKey: String(body.templateKey ?? `tp-${PACKS.length + 1}`),
        name: String(body.name ?? '未命名模板'),
        description: String(body.description ?? ''),
        prompt: String(body.prompt ?? ''),
        promptSha256: 'demo',
        sourceVideoAssetId: String(body.sourceVideoAssetId ?? ''),
        sourceDurationSec: Number(body.sourceDurationSec ?? 0),
        aspectRatio: String(body.aspectRatio ?? '9:16'),
        coverUrl: String(body.coverUrl ?? ''),
        status: 'draft',
        ownerId: 'me',
        slots,
        publishedAt: null,
        createdAt: now,
        updatedAt: now,
      };
      PACKS.push(pack);
      return ok(toOut(pack));
    },
  },
  {
    method: 'GET',
    pattern: /^\/template-packs\/([^/]+)\/candidates$/,
    handler: (ctx: MockContext) => {
      const pack = findPack(ctx.path.split('/')[2]);
      const token = String(ctx.params.token ?? '');
      const slot = pack.slots.find(item => item.token === token);
      if (!slot) throw new HttpError(400, `槽位 ${token} 不存在`);
      if (slot.slotKind !== 'filtered') {
        throw new HttpError(400, '该槽位是固定素材，没有可选范围');
      }
      const items = ASSETS.filter(asset =>
        matches(asset.id, slot.filterKind, slot.filterTagIds),
      ).map(asset => ({
        assetId: asset.id,
        name: asset.name,
        kind: asset.kind,
        coverUrl: asset.coverUrl,
        sizeBytes: 0,
        width: 0,
        height: 0,
      }));
      const result: TemplatePackCandidateListOut = {
        token: slot.token,
        label: slot.label,
        filterKind: slot.filterKind,
        filterTagIds: slot.filterTagIds,
        items,
        total: items.length,
      };
      return ok(result);
    },
  },
  {
    method: 'POST',
    pattern: /^\/template-packs\/([^/]+)\/verify$/,
    handler: (ctx: MockContext) => {
      const pack = findPack(ctx.path.split('/')[2]);
      const picks = (ctx.body as {picks?: Record<string, string>}).picks ?? {};
      const checked: Record<string, string> = {};
      for (const [token, assetId] of Object.entries(picks)) {
        const slot = pack.slots.find(item => item.token === token);
        if (!slot) throw new HttpError(400, `模板里没有这些槽位：${token}`);
        if (slot.slotKind !== 'filtered') continue;
        if (!assetId) {
          if (slot.required) {
            throw new HttpError(400, `槽位 ${token}「${slot.label || token}」必须选择素材`);
          }
          continue;
        }
        if (!matches(assetId, slot.filterKind, slot.filterTagIds)) {
          throw new HttpError(
            400,
            `所选素材不符合槽位「${slot.label || token}」的要求，请重新选择`,
          );
        }
        checked[token] = assetId;
      }
      return ok({picks: checked});
    },
  },
  {
    method: 'POST',
    pattern: /^\/template-packs\/([^/]+)\/publish$/,
    handler: (ctx: MockContext) => {
      const pack = findPack(ctx.path.split('/')[2]);
      pack.status = 'published';
      pack.publishedAt = now;
      return ok(toOut(pack));
    },
  },
  {
    method: 'POST',
    pattern: /^\/template-packs\/([^/]+)\/disable$/,
    handler: (ctx: MockContext) => {
      const pack = findPack(ctx.path.split('/')[2]);
      pack.status = 'disabled';
      return ok(toOut(pack));
    },
  },
  {
    method: 'PATCH',
    pattern: /^\/template-packs\/([^/]+)\/prompt$/,
    handler: (ctx: MockContext) => {
      const pack = findPack(ctx.path.split('/')[2]);
      pack.prompt = String((ctx.body as {prompt?: string}).prompt ?? pack.prompt);
      return ok(toOut(pack));
    },
  },
  {
    method: 'GET',
    pattern: /^\/template-packs\/([^/]+)$/,
    handler: (ctx: MockContext) => ok(toOut(findPack(ctx.path.split('/')[2]))),
  },
];
