import type {
  ManifestDraftIn,
  ManifestFunctionIn,
  ManifestOut,
  ManifestPackIn,
} from '@/shared/api/schema';
import { baseRole, rolesFor } from '@/entities/template/model/packRoles';

/** 封装编辑器的本地状态。与契约的差别只有「复用/新建」与「引用方式」两个开关。 */
export type PackKind = 'product' | 'character' | 'scene';
export type AspectRatio = '9:16' | '16:9' | '1:1';

export interface DraftRef {
  assetId: string;
  role: string;
}

export interface DraftPack {
  key: string;
  mode: 'new' | 'reuse';
  name: string;
  kind: PackKind;
  /** `mode = reuse` 时指向已发布的**运行素材包**（`/replacement-packs/available`）。 */
  packId: string;
  refs: DraftRef[];
  scopes: string[];
}

export interface DraftFunction {
  key: string;
  name: string;
  packKey: string;
  prompt: string;
  /** true = 按用途绑定（生成 referenceBindings）；false = 按素材包原序（mediaOrder）。 */
  useRoles: boolean;
  aspectRatio: AspectRatio;
  duration: string;
  /** 非空即「文字换场景」：不选包、不选范围，只有原片 + 可编辑描述。 */
  scenePrompt: string;
}

export interface Draft {
  key: string;
  name: string;
  description: string;
  videoAssetId: string;
  packs: DraftPack[];
  functions: DraftFunction[];
}

export const KIND_LABELS: Record<PackKind, string> = {
  product: '产品',
  character: '人物',
  scene: '场景',
};

export function newKey(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

export function blankPack(kind: PackKind = 'product'): DraftPack {
  return {
    key: newKey('pack'),
    mode: 'new',
    name: '',
    kind,
    packId: '',
    refs: [],
    scopes: kind === 'product' ? ['worn'] : [],
  };
}

export function blankFunction(): DraftFunction {
  return {
    key: newKey('fn'),
    name: '',
    packKey: '',
    prompt: '',
    useRoles: true,
    aspectRatio: '9:16',
    duration: '',
    scenePrompt: '',
  };
}

export function blankDraft(): Draft {
  return {
    key: newKey('tpl'),
    name: '',
    description: '',
    videoAssetId: '',
    packs: [blankPack()],
    functions: [blankFunction()],
  };
}

/** 按用途绑定：`{{Mixed 1}}` 永远是原片，其余按包里参考图的顺序落到用途上。 */
export function bindingsFor(pack: DraftPack): Record<string, string> {
  const mapping: Record<string, string> = {'{{Mixed 1}}': 'source_video'};
  pack.refs.forEach((ref, index) => {
    mapping[`{{Mixed ${index + 2}}}`] = ref.role || baseRole(pack.kind);
  });
  return mapping;
}

/** 已发布配置 → 草稿。字段逐项对应，未声明的取默认值。 */
export function fromManifest(out: ManifestOut): Draft {
  return {
    key: out.key,
    name: out.name,
    description: out.description ?? '',
    videoAssetId: out.video?.assetId ?? '',
    packs: (out.packs ?? []).map(pack => ({
      key: pack.key,
      mode: pack.packId ? 'reuse' : 'new',
      name: pack.name ?? '',
      kind: pack.kind,
      packId: pack.packId ?? '',
      refs: (pack.references ?? []).map(ref => ({assetId: ref.assetId, role: ref.role})),
      scopes: pack.supportedScopes ?? [],
    })),
    functions: (out.functions ?? []).map(fn => ({
      key: fn.key,
      name: fn.name,
      packKey: fn.pack ?? '',
      prompt: fn.prompt,
      useRoles: Boolean(fn.bindings),
      aspectRatio: fn.aspectRatio ?? '9:16',
      duration: fn.duration ? String(fn.duration) : '',
      scenePrompt: fn.scenePromptDefault ?? '',
    })),
  };
}

/** 草稿 → 预检/保存入参。空字段按「未提供」处理，不要发空串。 */
export function toPayload(draft: Draft): ManifestDraftIn {
  const packs: ManifestPackIn[] = draft.packs.map(pack => {
    const base: ManifestPackIn = {key: pack.key, kind: pack.kind};
    if (pack.mode === 'reuse') {
      return {...base, packId: pack.packId};
    }
    return {
      ...base,
      name: pack.name,
      references: pack.refs.map((ref, index) => ({
        key: `ref${index + 1}`,
        assetId: ref.assetId,
        role: ref.role || baseRole(pack.kind),
      })),
      supportedScopes: pack.scopes,
    };
  });

  const functions: ManifestFunctionIn[] = draft.functions.map(fn => {
    const scene = fn.scenePrompt.trim();
    if (scene) {
      return {
        key: fn.key,
        name: fn.name,
        kind: 'scene',
        scenePromptDefault: scene,
        prompt: fn.prompt,
        aspectRatio: fn.aspectRatio,
      };
    }
    const pack = draft.packs.find(item => item.key === fn.packKey);
    return {
      key: fn.key,
      name: fn.name,
      pack: fn.packKey,
      prompt: fn.prompt,
      ...(fn.useRoles && pack ? {bindings: bindingsFor(pack)} : {}),
      aspectRatio: fn.aspectRatio,
      ...(fn.duration ? {duration: Number(fn.duration)} : {}),
      scopeOptions: [],
    };
  });

  return {
    key: draft.key,
    name: draft.name,
    description: draft.description,
    video: {assetId: draft.videoAssetId},
    packs,
    functions,
  };
}

/** 功能可选的用途清单（给只读预览用）：按当前包的类型取词表。 */
export function roleOptions(kind: PackKind): {value: string; label: string}[] {
  return Object.entries(rolesFor(kind)).map(([value, label]) => ({value, label}));
}
