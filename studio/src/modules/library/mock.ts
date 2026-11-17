import { HttpError, type MockContext, type MockRoute } from '@/mocks/kernel';
import type {
  LibraryAssetDetailOut,
  LibraryAssetOut,
  LibraryGroupOut,
  LibraryTagStatOut,
  ShareRequestOut,
} from '@/shared/api/schema';

/**
 * 资产库 v2 · 全端点 mock（M4）。
 *
 * 三条约束：
 * 1. 正则**精确锚定** `/library/...`：mock 抢错前缀的症状是页面拿到空数据，
 *    看起来像「数据丢了」（v1 的 `/assets` mock 已于 2026-11-13 删除）；
 * 2. 状态放模块级内存，写操作真的改它 —— 「删了还在」的 mock 会让人以为
 *    自己的代码没生效；
 * 3. 只保证**形状与语义**（可见性、状态机、权限结果），不模拟真实字节：
 *    取文件返回字符串，前端识别到不是 Blob 会提示「演示模式」。
 */

const DAY = 86_400_000;
const now = Date.now();
const iso = (offsetDays = 0) => new Date(now + offsetDays * DAY).toISOString();

/** 词表按添加时间排：t1 最早、t3 最后，与后端 `list_tags` 的排序一致。 */
/**
 * 词表：**两级**（一级维度 → 二级取值），与真实后端同一套层级口径。
 *
 * 一级是维度（产品 / 画面类型 / 角色 / 场景），二级是取值（3030 / 正面图 /
 * 菜菜）。演示环境要是只有平级的一级词，标签栏的第二行永远是空的，
 * 两级能力一行都看不到。
 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 0, createdAt: iso(-8) },
  { id: 't2', name: '画面类型', assetCount: 0, createdAt: iso(-6) },
  { id: 't3', name: '角色', assetCount: 0, createdAt: iso(-4) },
  { id: 't4', name: '3030', parentId: 't1', assetCount: 0, createdAt: iso(-5) },
  { id: 't5', name: 'T40', parentId: 't1', assetCount: 0, createdAt: iso(-3) },
  { id: 't6', name: '正面图', parentId: 't2', assetCount: 0, createdAt: iso(-2) },
  { id: 't7', name: '菜菜', parentId: 't3', assetCount: 0, createdAt: iso(-1) },
];

/** `isLeader` 现在只服务删除窄口（§3.4.1）；审批资格按权限点与目标范围判，
 *  和这个字段无关——mock 没有当前用户的权限模型，申请单列表也就不按组长筛。 */
const GROUPS: LibraryGroupOut[] = [
  { id: 'g1', name: '品牌组', isLeader: true },
  { id: 'g2', name: '短视频组', isLeader: false },
];

const ME = 'u_001';

let assetSeq = 100;

let ASSETS: LibraryAssetDetailOut[] = [];

function asset(partial: Partial<LibraryAssetDetailOut> & { name: string; kind: LibraryAssetDetailOut['kind'] }): LibraryAssetDetailOut {
  assetSeq += 1;
  const id = `a${assetSeq}`;
  return {
    id,
    name: partial.name,
    kind: partial.kind,
    purpose: partial.purpose ?? '',
    origin: partial.origin ?? 'upload',
    mimeType: partial.mimeType ?? 'image/png',
    sizeBytes: partial.sizeBytes ?? 1_240_000,
    durationSec: partial.durationSec ?? null,
    hasAudio: partial.hasAudio ?? false,
    ownerId: partial.ownerId ?? ME,
    ownerName: partial.ownerName ?? '老大',
    visibility: partial.visibility ?? 'personal',
    state: partial.state ?? 'active',
    stateVersion: 1,
    coverUrl: partial.coverUrl ?? `https://picsum.photos/seed/${id}/480/360`,
    groupIds: partial.groupIds ?? [],
    trashReason: partial.trashReason ?? null,
    trashExpiresAt: partial.trashExpiresAt ?? null,
    createdAt: partial.createdAt ?? iso(-3),
    updatedAt: null,
    /* 默认挂一个**二级取值**：一级是维度（分组），不是能打在素材上的词。 */
    tags: partial.tags ?? [TAGS[3]],
    groups: (partial.groupIds ?? []).map((gid) => ({
      id: gid,
      name: GROUPS.find((group) => group.id === gid)?.name ?? gid,
    })),
    generation: partial.generation ?? null,
    references: partial.references ?? [],
    shareHistory: partial.shareHistory ?? [],
    archive:
      partial.archive ?? {
        storageKey: `blobs/library/${id}.png`,
        sha256: `${id}`.padEnd(64, '0'),
        sourceKey: undefined,
        sizeBytes: partial.sizeBytes ?? 1_240_000,
        referenceCount: 0,
      },
    reviewedBy: null,
    reviewedAt: null,
    rejectReason: null,
    trashedFromState: partial.trashedFromState ?? null,
  };
}

function seed() {
  ASSETS = [
    asset({
      name: '超市抢购·主视觉 01',
      kind: 'image',
      /* 素材挂在**二级取值**上（产品/3030 + 画面类型/正面图）——
         一级是维度，只是分组；演示「跨维度取交集」要靠同一条挂两个维度的取值。 */
      tags: [TAGS[3], TAGS[5]],
      createdAt: iso(-1),
    }),
    asset({
      name: '产品开箱特写 · 白底',
      kind: 'image',
      tags: [TAGS[5]],
      createdAt: iso(-2),
    }),
    asset({
      name: '人物三视图 · v3',
      kind: 'image',
      visibility: 'group',
      groupIds: ['g1'],
      tags: [TAGS[4]],
      createdAt: iso(-4),
      shareHistory: [
        {
          id: 'h1',
          action: 'share',
          target: 'group',
          groupId: 'g1',
          groupName: '品牌组',
          status: 'approved',
          applicantId: ME,
          applicantName: '老大',
          reviewerId: 'u_002',
          reviewerName: '林设计',
          reason: '品牌组共用人物形象',
          createdAt: iso(-4),
          decidedAt: iso(-4),
        },
      ],
    }),
    asset({
      name: '夜跑城市街头 运镜 A',
      kind: 'video',
      /* 并存期从旧素材库导入的存量原片：模板工作台的「公共原片」靠它 */
      origin: 'imported',
      mimeType: 'video/mp4',
      visibility: 'team',
      durationSec: 12,
      hasAudio: true,
      sizeBytes: 84_000_000,
      tags: [TAGS[3]],
      ownerId: 'u_002',
      ownerName: '林设计',
      createdAt: iso(-6),
    }),
    asset({
      name: '咖啡店晨间氛围 空镜',
      kind: 'video',
      /* 模板库切换后新传的原片；无音轨，正好演示「不可选 + 原因」 */
      origin: 'template_source',
      mimeType: 'video/mp4',
      durationSec: 8,
      hasAudio: false,
      sizeBytes: 41_000_000,
      ownerId: 'u_002',
      ownerName: '林设计',
      createdAt: iso(-7),
    }),
    asset({
      name: '旁白配音 · 女声 01',
      kind: 'audio',
      mimeType: 'audio/mpeg',
      durationSec: 32,
      sizeBytes: 780_000,
      tags: [TAGS[6]],
      createdAt: iso(-8),
    }),
    asset({
      name: '生成结果 · 超市人流（待确认）',
      kind: 'image',
      origin: 'generated',
      state: 'draft',
      tags: [],
      createdAt: iso(-0.2),
      generation: {
        sourceModule: 'image',
        sourceTaskId: 'img_2041',
        model: 'Seedream 4.0',
        modelId: 'seedream-4',
        prompt: '超市抢购场景，手持镜头跟拍，人群涌动，暖色顶光，电影感浅景深',
        size: '2048x2048',
        count: 1,
        aspectRatio: '1:1',
        seed: '882144',
        credits: 6,
        generatedAt: iso(-0.2),
        promptSpec: { style: 'cinematic', lighting: 'warm' },
        configuration: { steps: 28, cfg: 7 },
      },
    }),
    asset({
      name: '生成结果 · 人物转身（待确认）',
      kind: 'image',
      origin: 'generated',
      state: 'draft',
      tags: [],
      createdAt: iso(-0.3),
      generation: {
        sourceModule: 'task',
        sourceTaskId: 'T20261109001',
        model: '可灵 2.1',
        prompt: '人物转身慢动作，影棚白底，120fps',
        durationSec: 5,
        aspectRatio: '9:16',
        referenceLibraryIds: ['a1'],
        generatedAt: iso(-0.3),
      },
    }),
    asset({
      name: '废弃版本 · 旧主视觉',
      kind: 'image',
      state: 'trash',
      trashReason: '换成 v3 重新上传',
      trashExpiresAt: iso(23),
      trashedFromState: 'active',
      createdAt: iso(-20),
    }),
    asset({
      name: '过期草稿 · 手部变形',
      kind: 'image',
      state: 'trash',
      trashReason: '手部变形，重新生成',
      trashExpiresAt: iso(-1),
      trashedFromState: 'draft',
      createdAt: iso(-32),
    }),
  ];
}
seed();

/* ---------------------------------------------------------------- 共享申请 */

let requests: ShareRequestOut[] = [
  {
    id: 'r1',
    assetId: 'a101',
    assetName: '超市抢购·主视觉 01',
    applicantId: ME,
    applicantName: '老大',
    action: 'share',
    target: 'group',
    groupId: 'g1',
    groupName: '品牌组',
    status: 'pending',
    reviewerId: null,
    note: '',
    reason: '品牌组做同款活动',
    decidedAt: null,
    createdAt: iso(-0.5),
  },
  {
    id: 'r2',
    assetId: 'a103',
    assetName: '人物三视图 · v3',
    applicantId: 'u_002',
    applicantName: '林设计',
    action: 'revoke',
    target: 'group',
    groupId: 'g1',
    groupName: '品牌组',
    status: 'pending',
    reviewerId: null,
    note: '',
    reason: '人物形象升级到 v4，旧版下架',
    decidedAt: null,
    createdAt: iso(-0.2),
  },
];

/* ---------------------------------------------------------------- 下载任务 */

interface MockJob {
  id: string;
  status: string;
  assetCount: number;
  sizeBytes: number;
  error: string;
  downloadUrl: string | null;
  expiresAt: string | null;
  createdAt: string;
  finishedAt: string | null;
  skipped: Array<{ id: string; name?: string; reason: string }>;
}

let jobs: MockJob[] = [];
let jobSeq = 0;

/* ---------------------------------------------------------------- 工具函数 */

function find(id: string): LibraryAssetDetailOut {
  const row = ASSETS.find((item) => item.id === id);
  if (!row) throw new HttpError(404, '资产不存在或不可见');
  return row;
}

/** 列表项 = 详情去掉六个只在详情页出现的区（与后端 `_to_out` 同形） */
function toOut(row: LibraryAssetDetailOut): LibraryAssetOut {
  return {
    id: row.id,
    name: row.name,
    kind: row.kind,
    purpose: row.purpose ?? '',
    origin: row.origin,
    mimeType: row.mimeType ?? null,
    sizeBytes: row.sizeBytes ?? 0,
    durationSec: row.durationSec ?? null,
    hasAudio: row.hasAudio ?? false,
    ownerId: row.ownerId,
    ownerName: row.ownerName ?? null,
    visibility: row.visibility,
    state: row.state,
    stateVersion: row.stateVersion ?? 1,
    coverUrl: row.coverUrl ?? null,
    groupIds: row.groupIds ?? [],
    trashReason: row.trashReason ?? null,
    trashExpiresAt: row.trashExpiresAt ?? null,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt ?? null,
  };
}

function inMyGroups(row: LibraryAssetDetailOut) {
  return (row.groupIds ?? []).some((gid) => GROUPS.some((group) => group.id === gid));
}

function visible(row: LibraryAssetDetailOut, library: string | undefined) {
  if (library === 'personal') return row.visibility === 'personal' && row.ownerId === ME;
  if (library === 'group') return row.visibility === 'group' && inMyGroups(row);
  if (library === 'team') return row.visibility === 'team';
  // 全部：省略 `library` 与显式 `library=all` 都走这一支（后端 LibraryTab.ALL
  // 是 `tab_clause` 的 fallback）。我的（未共享）+ 共享到我所在小组的 + 团队。
  // 不能写成 `ownerId === ME || visibility !== 'personal'`：那样别人共享到
  // **我不在的**小组的资产也会出现（后端 `active_visible_clause` 不是这么判的）。
  if (row.ownerId === ME && row.visibility === 'personal') return true;
  if (row.visibility === 'team') return true;
  return row.visibility === 'group' && inMyGroups(row);
}

/** 某个入口的可见条数。角标必须等于点进去的条数，所以按入口逐个算。 */
function facetOf(library?: string) {
  return ASSETS.filter((row) => row.state === 'active' && visible(row, library)).length;
}

function facets() {
  return {
    all: facetOf(undefined),
    personal: facetOf('personal'),
    group: facetOf('group'),
    team: facetOf('team'),
    drafts: ASSETS.filter((row) => row.state === 'draft').length,
    trash: ASSETS.filter((row) => row.state === 'trash').length,
  };
}

function listOf(ctx: MockContext, withFacets: boolean) {
  const params = ctx.params;
  const state = String(params.state ?? 'active');
  const library = params.library ? String(params.library) : undefined;
  const kind = params.kind ? String(params.kind) : undefined;
  const purpose = params.purpose ? String(params.purpose) : '';
  const keyword = String(params.keyword ?? '').toLowerCase();
  const tagIds = String(params.tagIds ?? '')
    .split(',')
    .filter(Boolean);

  let rows = ASSETS.filter((row) => row.state === state);
  if (state === 'active') {
    rows = rows.filter((row) => visible(row, library));
  }
  /* 用途与后端同口径：普通入口排除案例，案例池只留案例。 */
  rows = purpose
    ? rows.filter((row) => row.purpose === purpose)
    : rows.filter((row) => row.purpose !== 'case');
  if (kind) rows = rows.filter((row) => row.kind === kind);
  /* `origin` 是数组参数（`origin=a&origin=b`），axios 传进来的就是数组，
     统一按逗号拆——与 `tagIds` 同一套约定。模板工作台靠它只挑原片。 */
  const origins = String(params.origin ?? '').split(',').filter(Boolean);
  if (origins.length) rows = rows.filter((row) => origins.includes(row.origin ?? ''));
  if (keyword) rows = rows.filter((row) => row.name.toLowerCase().includes(keyword));
  if (tagIds.length) {
    /* 标签筛选：**组内 OR、组间 AND**（两级维度模型），与后端
       `tag_filter_clause` 同口径。
       选中的一级维度各成一组，组内的取值互相或，组与组之间且。 */
    const groups = tagGroupsByDimension(tagIds);
    rows = rows.filter((row) => {
      const owned = new Set((row.tags ?? []).map((tag) => tag.id));
      return groups.every((group) => {
        const expanded = new Set<string>();
        for (const id of group) {
          expanded.add(id);
          for (const descendant of tagDescendantIds(id)) expanded.add(descendant);
        }
        return [...owned].some((id) => expanded.has(id));
      });
    });
  }
  if (params.sort === 'name') rows = rows.sort((a, b) => a.name.localeCompare(b.name));
  else if (params.sort === '-sizeBytes') rows = rows.sort((a, b) => (b.sizeBytes ?? 0) - (a.sizeBytes ?? 0));
  else if (params.sort === 'createdAt') rows = rows.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  else rows = rows.sort((a, b) => b.createdAt.localeCompare(a.createdAt));

  const page = Math.max(1, Number(params.page ?? 1) || 1);
  const pageSize = Math.max(1, Number(params.pageSize ?? 24) || 24);
  const items = rows.slice((page - 1) * pageSize, page * pageSize).map(toOut);
  return { items, total: rows.length, page, pageSize, ...(withFacets ? { facets: facets() } : {}) };
}

/**
 * 选中的标签按**一级维度**分组：选中的一级自己成一组，
 * 二级取值归到它的一级祖先那组。组内 OR、组间 AND（与后端同口径）。
 */
function tagGroupsByDimension(tagIds: string[]): string[][] {
  const groups = new Map<string, string[]>();
  for (const id of tagIds) {
    const tag = TAGS.find((item) => item.id === id);
    if (!tag) continue;
    const key = tag.parentId ?? tag.id;
    groups.set(key, [...(groups.get(key) ?? []), id]);
  }
  return [...groups.values()];
}

/**
 * 后代 id（不含自身）：筛选时「选一级 = 展开到全部下级」，与后端
 * `expand_tag_ids` 同一口径。
 */
function tagDescendantIds(id: string): string[] {
  const result: string[] = [];
  const walk = (parentId: string) => {
    for (const child of TAGS.filter((item) => item.parentId === parentId)) {
      result.push(child.id);
      walk(child.id);
    }
  };
  walk(id);
  return result;
}

/** 祖先 id（不含自身，从近到远）：计数时一条挂了三级词的素材也要算进它的
 *  一级与二级祖先。 */
function tagAncestorIds(id: string): string[] {
  const result: string[] = [];
  let parentId = TAGS.find((item) => item.id === id)?.parentId ?? undefined;
  while (parentId) {
    result.push(parentId);
    const next = TAGS.find((item) => item.id === parentId)?.parentId ?? undefined;
    parentId = next;
  }
  return result;
}

/** 词表的派生字段：层级、整条父链、直属子级数（后端 `list_tags` 给的就是这些）。 */
function tagStatOf(tag: LibraryTagStatOut): LibraryTagStatOut {
  const chain: string[] = [];
  let parentId = tag.parentId ?? undefined;
  while (parentId) {
    const parent = TAGS.find((item) => item.id === parentId);
    if (!parent) break;
    chain.unshift(parent.name);
    parentId = parent.parentId ?? undefined;
  }
  return {
    ...tag,
    parentId: tag.parentId ?? null,
    parentName: chain.length ? chain.join(' / ') : null,
    depth: chain.length + 1,
    childCount: TAGS.filter((item) => item.parentId === tag.id).length,
  };
}

/** 标签栏计数：与 `listOf` 同一套入口口径。 */
function tagStats(ctx: MockContext): LibraryTagStatOut[] {
  const state = String(ctx.params.state ?? 'active');
  const library = ctx.params.library ? String(ctx.params.library) : undefined;
  let rows = ASSETS.filter((row) => row.state === state);
  if (state === 'active') {
    rows = rows.filter((row) => visible(row, library));
  }
  const purpose = ctx.params.purpose ? String(ctx.params.purpose) : '';
  rows = purpose
    ? rows.filter((row) => row.purpose === purpose)
    : rows.filter((row) => row.purpose !== 'case');
  /* 候选范围（模板库用）：只统计符合这组标签的素材。
     与后端 `GET /library/tags?tagIds=` 同口径 —— 组内 OR、组间 AND。
     模板作者圈了候选集之后，标签栏要显示的是「这批素材身上实际有的标签」，
     按全库算会列出一堆这批素材一个都没挂的标签。 */
  const scopeIds = String(ctx.params.tagIds ?? '')
    .split(',')
    .filter(Boolean);
  if (scopeIds.length) {
    const groups = tagGroupsByDimension(scopeIds);
    rows = rows.filter((row) => {
      const owned = new Set((row.tags ?? []).map((tag) => tag.id));
      return groups.every((group) => {
        const expanded = new Set<string>();
        for (const id of group) {
          expanded.add(id);
          for (const descendant of tagDescendantIds(id)) expanded.add(descendant);
        }
        return [...owned].some((id) => expanded.has(id));
      });
    });
  }
  /* 计数口径与后端一致：一个标签 = 自身 + 全部下级，按资产 id 去重。
     所以一条挂了三级词的素材，同时算进它的一级与二级祖先。 */
  const counts = new Map<string, number>();
  for (const row of rows) {
    const ids = new Set<string>();
    for (const tag of row.tags ?? []) {
      ids.add(tag.id);
      for (const ancestor of tagAncestorIds(tag.id)) ids.add(ancestor);
    }
    for (const id of ids) counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return TAGS.map((tag) => ({
    ...tagStatOf(tag),
    assetCount: counts.get(tag.id) ?? 0,
  })).sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
}

const nowIso = () => new Date().toISOString();

/* ---------------------------------------------------------------- 路由 */

export const routes: MockRoute[] = [
  { method: 'GET', pattern: /^\/library\/assets$/, handler: (ctx) => (listOf(ctx, true)) },
  { method: 'GET', pattern: /^\/library\/facets$/, handler: () => (facets()) },
  {
    method: 'GET',
    pattern: /^\/library\/selection$/,
    handler: (ctx) =>
      listOf({ ...ctx, params: { ...ctx.params, state: 'active' } }, false),
  },

  {
    method: 'POST',
    pattern: /^\/library\/assets\/upload$/,
    handler: (ctx) => {
      const filename = String(ctx.params.filename ?? '未命名');
      const rawKind = String(ctx.params.kind ?? 'image');
      /* 三种类别都要认：演示环境把 .txt 落成图片，文本资产在「文本」筛选里
         就找不到，等于骗人。 */
      const kind: LibraryAssetDetailOut['kind'] =
        rawKind === 'audio' || rawKind === 'text' || rawKind === 'video' ? rawKind : 'image';
      const mimeType =
        kind === 'text'
          ? 'text/plain'
          : kind === 'audio'
            ? 'audio/mpeg'
            : kind === 'video'
              ? 'video/mp4'
              : 'image/png';
      const row = asset({
        name: filename,
        kind,
        mimeType,
        state: 'active',
        tags: [],
        sizeBytes: 900_000 + (assetSeq % 5) * 120_000,
        createdAt: nowIso(),
      });
      ASSETS.unshift(row);
      return (toOut(row));
    },
  },
  {
    method: 'POST',
    pattern: /^\/library\/source-videos$/,
    handler: (ctx) => {
      const row = asset({
        name: String(ctx.params.filename ?? '参考原片'),
        kind: 'video',
        origin: 'template_source',
        state: 'active',
        tags: [],
        durationSec: 6,
        sizeBytes: 30_000_000,
        createdAt: nowIso(),
      });
      ASSETS.unshift(row);
      return (toOut(row));
    },
  },

  {
    method: 'POST',
    pattern: /^\/library\/assets\/bulk$/,
    handler: (ctx) => {
      const ids = (ctx.body.ids as string[]) ?? [];
      const action = String(ctx.body.action ?? '');
      const payload = (ctx.body.payload as Record<string, unknown>) ?? {};
      const results = ids.map((id) => {
        const row = ASSETS.find((item) => item.id === id);
        if (!row) return { id, ok: false, message: '资产不存在', errorCode: 40400 };
        if (action === 'delete') {
          if (row.state !== 'active') return { id, ok: false, message: '该资产不在库中' };
          row.state = 'trash';
          row.trashedFromState = 'active';
          row.trashExpiresAt = iso(30);
          return { id, ok: true };
        }
        if (action === 'restore') {
          if (row.state !== 'trash') return { id, ok: false, message: '该资产不在回收站' };
          row.state = (row.trashedFromState as 'draft' | 'active') ?? 'active';
          row.trashExpiresAt = null;
          return { id, ok: true };
        }
        if (action === 'approveDraft') {
          if (row.state !== 'draft') return { id, ok: false, message: '该草稿已被处理' };
          row.state = 'active';
          return { id, ok: true };
        }
        if (action === 'rejectDraft') {
          if (row.state !== 'draft') return { id, ok: false, message: '该草稿已被处理' };
          row.state = 'trash';
          row.trashedFromState = 'draft';
          row.rejectReason = String(payload.reason ?? '');
          row.trashExpiresAt = iso(30);
          return { id, ok: true };
        }
        if (action === 'addTag') {
          const ids2 = (payload.tagIds as string[]) ?? [];
          row.tags = [...(row.tags ?? []), ...TAGS.filter((tag) => ids2.includes(tag.id))].filter(
            (tag, index, all) => all.findIndex((item) => item.id === tag.id) === index,
          );
          return { id, ok: true };
        }
        return { id, ok: false, message: `不支持的动作 ${action}` };
      });
      const succeeded = results.filter((item) => item.ok).length;
      return ({ succeeded, failed: results.length - succeeded, results });
    },
  },
  {
    method: 'POST',
    pattern: /^\/library\/assets\/from-generation$/,
    handler: (ctx) => {
      const body = ctx.body as Record<string, unknown>;
      const existing = ASSETS.find((row) => row.archive?.sourceKey && row.archive.sourceKey === body.sourceKey);
      if (existing) return ({ assetId: existing.id, created: false });
      const row = asset({
        name: String(body.name ?? '生成结果'),
        kind: (body.kind as LibraryAssetDetailOut['kind']) ?? 'image',
        origin: 'generated',
        state: 'draft',
        tags: [],
        createdAt: nowIso(),
        generation: (body.generation as LibraryAssetDetailOut['generation']) ?? null,
      });
      ASSETS.unshift(row);
      return ({ assetId: row.id, created: true });
    },
  },

  {
    method: 'PATCH',
    pattern: /^\/library\/assets\/([^/]+)$/,
    handler: (ctx) => {
      const row = find(ctx.path.split('/').pop() ?? '');
      const body = ctx.body as { name?: string; tagIds?: string[] };
      if (body.name) row.name = body.name;
      if (Array.isArray(body.tagIds)) {
        row.tags = TAGS.filter((tag) => body.tagIds?.includes(tag.id));
      }
      row.stateVersion = (row.stateVersion ?? 1) + 1;
      return (row);
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/library\/assets\/([^/]+)$/,
    handler: (ctx) => {
      const row = find(ctx.path.split('/').pop() ?? '');
      if (row.state !== 'active') throw new HttpError(409, '该资产不在库中');
      if (row.visibility !== 'personal') {
        if (row.ownerId !== ME) throw new HttpError(409, '团队库资产不能直接删除，请先撤销团队共享');
        const canRevoke = String(ctx.params.revokeOwnGroupShare ?? 'false') === 'true';
        if (!canRevoke) {
          throw new HttpError(409, '该资产仍处于共享状态，请先撤销共享后再删除', {
            canRevokeAndDelete:
              (row.groupIds ?? []).length === 1 &&
              !!GROUPS.find((group) => group.id === row.groupIds?.[0])?.isLeader,
          });
        }
        if (!String(ctx.params.reason ?? '').trim()) throw new HttpError(400, '撤销共享必须填写理由');
        row.groupIds = [];
        row.groups = [];
        row.visibility = 'personal';
      }
      row.state = 'trash';
      row.trashedFromState = 'active';
      row.trashExpiresAt = iso(30);
      return (null);
    },
  },
  { method: 'GET', pattern: /^\/library\/assets\/([^/]+)$/, handler: (ctx) => (find(ctx.path.split('/').pop() ?? '')) },
  {
    method: 'GET',
    pattern: /^\/library\/assets\/([^/]+)\/(file|thumbnail)$/,
    handler: () => ('mock://binary'),
  },

  {
    method: 'POST',
    pattern: /^\/library\/drafts\/([^/]+)\/approve$/,
    handler: (ctx) => {
      const row = find(ctx.path.split('/')[3] ?? '');
      if (row.state !== 'draft') throw new HttpError(409, '该草稿已被处理，请刷新后重试');
      row.state = 'active';
      row.reviewedBy = ME;
      row.reviewedAt = nowIso();
      return (row);
    },
  },
  {
    method: 'POST',
    pattern: /^\/library\/drafts\/([^/]+)\/reject$/,
    handler: (ctx) => {
      const row = find(ctx.path.split('/')[3] ?? '');
      if (row.state !== 'draft') throw new HttpError(409, '该草稿已被处理，请刷新后重试');
      row.state = 'trash';
      row.trashedFromState = 'draft';
      row.rejectReason = String(ctx.body.reason ?? '');
      row.trashExpiresAt = iso(30);
      return (row);
    },
  },
  {
    method: 'POST',
    pattern: /^\/library\/trash\/([^/]+)\/restore$/,
    handler: (ctx) => {
      const row = find(ctx.path.split('/')[3] ?? '');
      if (row.state !== 'trash') throw new HttpError(409, '该资产不在回收站');
      row.state = (row.trashedFromState as 'draft' | 'active') ?? 'active';
      row.visibility = 'personal';
      row.groupIds = [];
      row.groups = [];
      row.trashExpiresAt = null;
      return (row);
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/library\/trash\/([^/]+)$/,
    handler: (ctx) => {
      const id = ctx.path.split('/').pop() ?? '';
      const row = find(id);
      if (row.state !== 'trash') throw new HttpError(409, '该资产不在回收站');
      if ((row.references ?? []).length) {
        throw new HttpError(409, '资产仍被引用，不能彻底删除', { references: row.references });
      }
      ASSETS = ASSETS.filter((item) => item.id !== id);
      return (null);
    },
  },

  {
    method: 'POST',
    pattern: /^\/library\/downloads$/,
    handler: (ctx) => {
      const ids = (ctx.body.ids as string[]) ?? [];
      const rows = ids.map((id) => ASSETS.find((item) => item.id === id)).filter(Boolean) as LibraryAssetDetailOut[];
      const usable = rows.filter((row) => row.state === 'active');
      if (!usable.length) throw new HttpError(400, '选中的条目都不在可下载范围内');
      jobSeq += 1;
      const job: MockJob = {
        id: `job${jobSeq}`,
        status: 'succeeded',
        assetCount: usable.length,
        sizeBytes: usable.reduce((sum, row) => sum + (row.sizeBytes ?? 0), 0),
        error: '',
        downloadUrl: `/library/downloads/job${jobSeq}/file`,
        expiresAt: iso(1),
        createdAt: nowIso(),
        finishedAt: nowIso(),
        skipped: rows
          .filter((row) => row.state !== 'active')
          .map((row) => ({ id: row.id, name: row.name, reason: row.state === 'draft' ? '草稿未入库' : '回收站内容不可下载' })),
      };
      jobs = [job, ...jobs].slice(0, 10);
      return (job);
    },
  },
  { method: 'GET', pattern: /^\/library\/downloads$/, handler: () => (jobs) },
  {
    method: 'GET',
    pattern: /^\/library\/downloads\/([^/]+)$/,
    handler: (ctx) => {
      const job = jobs.find((item) => item.id === ctx.path.split('/').pop());
      if (!job) throw new HttpError(404, '打包任务不存在');
      return (job);
    },
  },
  { method: 'GET', pattern: /^\/library\/downloads\/([^/]+)\/file$/, handler: () => ('mock://zip') },

  {
    method: 'POST',
    pattern: /^\/library\/share-requests$/,
    handler: (ctx) => {
      const body = ctx.body as Record<string, unknown>;
      const row = find(String(body.assetId ?? ''));
      const id = `r${requests.length + 10}`;
      const request: ShareRequestOut = {
        id,
        assetId: row.id,
        assetName: row.name,
        applicantId: ME,
        applicantName: '老大',
        action: (body.action as 'share' | 'revoke') ?? 'share',
        target: (body.target as 'group' | 'team') ?? 'team',
        groupId: body.groupId ? String(body.groupId) : undefined,
        groupName: body.groupId ? GROUPS.find((group) => group.id === body.groupId)?.name : null,
        /* 真实后端只有持 `library:share:direct` 才当场通过，否则是待审批：
           界面文案按 status 走，演示环境永远 approved 就看不到「申请已提交，
           等待审核」那条路。下面的 approve/reject 分支正好处理 pending。 */
        status: 'pending',
        reviewerId: undefined,
        note: '',
        reason: String(body.reason ?? ''),
        decidedAt: undefined,
        createdAt: nowIso(),
      };
      if (request.action === 'revoke' && !request.reason) throw new HttpError(400, '撤销共享必须填写理由');
      requests = [request, ...requests];
      return (request);
    },
  },
  {
    method: 'GET',
    pattern: /^\/library\/share-requests$/,
    handler: (ctx) => {
      const scope = String(ctx.params.scope ?? 'mine');
      return (scope === 'pending' ? requests.filter((item) => item.status === 'pending') : requests.filter((item) => item.applicantId === ME));
    },
  },
  {
    method: 'POST',
    pattern: /^\/library\/share-requests\/([^/]+)\/(approve|reject|cancel)$/,
    handler: (ctx) => {
      const [, , , id = '', action = ''] = ctx.path.split('/');
      const request = requests.find((item) => item.id === id);
      if (!request) throw new HttpError(404, '申请不存在');
      if (request.status !== 'pending') throw new HttpError(409, '该申请已被处理，请刷新列表');
      if (action === 'cancel') request.status = 'cancelled';
      else if (action === 'approve') {
        request.status = 'approved';
        request.decidedAt = nowIso();
        const row = ASSETS.find((item) => item.id === request.assetId);
        if (row) {
          if (request.action === 'share') {
            row.visibility = request.target === 'team' ? 'team' : 'group';
            if (request.groupId) row.groupIds = [request.groupId];
          } else if (request.target === 'team') {
            row.visibility = 'personal';
          } else {
            row.groupIds = (row.groupIds ?? []).filter((gid) => gid !== request.groupId);
            if (!row.groupIds.length) row.visibility = 'personal';
          }
        }
      } else {
        request.status = 'rejected';
        request.decidedAt = nowIso();
      }
      return (request);
    },
  },

  {
    method: 'POST',
    pattern: /^\/library\/assets\/([^/]+)\/purpose$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[3] ?? '';
      const row = ASSETS.find((item) => item.id === id);
      if (!row) throw new HttpError(404, '资产不存在或不可见');
      row.purpose = String(ctx.body.purpose ?? '');
      return (toOut(row));
    },
  },

  { method: 'GET', pattern: /^\/library\/groups$/, handler: () => (GROUPS) },
  { method: 'PUT', pattern: /^\/library\/groups\/([^/]+)\/leaders$/, handler: () => ([]) },

  { method: 'GET', pattern: /^\/library\/tags$/, handler: (ctx) => (tagStats(ctx)) },
  {
    method: 'POST',
    pattern: /^\/library\/tags$/,
    handler: (ctx) => {
      const name = String(ctx.body.name ?? '').trim();
      const parentId = ctx.body.parentId ? String(ctx.body.parentId) : undefined;
      /* 同名幂等的口径是「同一父下同名」：两个系列各自有「小红书」是合法的 */
      const existing = TAGS.find(
        (tag) => tag.name === name && (tag.parentId ?? undefined) === parentId,
      );
      if (existing) return (tagStatOf(existing));
      const tag: LibraryTagStatOut = {
        id: `t${TAGS.length + 1}`,
        name,
        parentId: parentId ?? null,
        assetCount: 0,
        createdAt: nowIso(),
      };
      TAGS.push(tag);
      return (tagStatOf(tag));
    },
  },
  {
    method: 'PATCH',
    pattern: /^\/library\/tags\/([^/]+)$/,
    handler: (ctx) => {
      const id = ctx.path.split('/').pop() ?? '';
      const tag = TAGS.find((item) => item.id === id);
      if (!tag) throw new HttpError(404, '标签不存在');
      tag.name = String(ctx.body.name ?? tag.name);
      return (tag);
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/library\/tags\/([^/]+)$/,
    handler: () => (null),
  },
];
