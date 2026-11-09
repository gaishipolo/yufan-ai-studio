import type { ArchivedAsset } from '@/shared/api/schema';
import { db } from '../db';
import { HttpError, type MockRoute } from '../kernel';
import { categoryIdOf, mediaTypeOf, visibleInLibrary } from '../library-tree';

/* ---------------- 素材 / 模板 / 分镜 / 镜头 / 生图记录 ---------------- */

const assetPage = (params: Record<string, unknown>) => {
  const page = Math.max(1, Number(params.page ?? 1) || 1);
  const pageSize = Math.max(1, Number(params.pageSize ?? 24) || 24);
  return { page, pageSize, offset: (page - 1) * pageSize };
};

/**
 * 素材 mock 对齐的是**合并之后**的 `/assets`：它就是归档，没有第二套。
 *
 * 合并前这里 mock 的是旧六条接口（`counts` 分面、`refCount`、`DELETE` 直接删），
 * 而那六条后端已经删了——mock 能一直绿，是因为没人拿它跑真实链路。所以这里
 * 刻意照抄后端的三条规矩：隐藏已删、`expectedVersion` 冲突给 409、彻底删除前
 * 必须先在回收站。
 */
const findAsset = (id: string) => {
  const asset = db.assets.find((item) => item.id === id);
  if (!asset) throw new HttpError(404, '素材不存在');
  return asset;
};

const changeAsset = (id: string, patch: Partial<ArchivedAsset>, expectedVersion: unknown) => {
  const asset = findAsset(id);
  const version = Number(expectedVersion);
  if (Number.isFinite(version) && version !== (asset.stateVersion ?? 0)) {
    throw new HttpError(409, '素材已发生变化，请刷新后重试');
  }
  Object.assign(asset, patch, { stateVersion: (asset.stateVersion ?? 0) + 1 });
  return asset;
};

const MINE = 'u_001';

const visibleAssets = (params: Record<string, unknown>) => {
  const scope = String(params.scope ?? 'mine');
  const category = String(params.category ?? 'all');
  const categoryId = String(params.categoryId ?? '');
  const mediaType = String(params.mediaType ?? '');
  const keyword = String(params.keyword ?? '').trim().toLowerCase();
  const state = String(params.state ?? 'all');
  const teamId = String(params.teamId ?? '');
  const inTrash = (asset: ArchivedAsset) => asset.deletedAt != null;
  let list: ArchivedAsset[];
  if (scope === 'trash') list = db.assets.filter((asset) => asset.ownerId === MINE && inTrash(asset));
  else if (scope === 'public') list = visibleInLibrary('brand');
  else if (scope === 'team') {
    // 不带 teamId 是 400，与后端一致：回落成"全部团队"等于把跨团队隔离关掉
    if (!teamId) throw new HttpError(400, '请先选择团队库');
    list = visibleInLibrary(`team:${teamId}`);
  } else list = visibleInLibrary('personal');
  // 新分类树按 id；两个都给时以 id 为准（与后端一致）
  if (categoryId) list = list.filter((asset) => categoryIdOf(asset) === categoryId);
  else if (category !== 'all') list = list.filter((asset) => asset.category === category);
  if (mediaType) list = list.filter((asset) => mediaTypeOf(asset) === mediaType);
  if (keyword) list = list.filter((asset) => asset.name.toLowerCase().includes(keyword));
  if (scope === 'mine' && state !== 'all') {
    list = list.filter((asset) => (state === 'shared' ? asset.shared : !asset.shared));
  }
  return list;
};

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/assets$/,
    handler: (ctx) => {
      const list = visibleAssets(ctx.params);
      const { page, pageSize, offset } = assetPage(ctx.params);
      const usedBytes = db.assets.reduce((sum, asset) => sum + asset.sizeBytes, 0);
      return {
        items: list.slice(offset, offset + pageSize),
        total: list.length,
        usedBytes,
        // 配额不设限：`quotaBytes` 缺省表示「没有上限」，与后端 `0` 的语义一致
        ownerUsedBytes: usedBytes,
        page,
        pageSize,
      };
    },
  },
  {
    method: 'POST',
    pattern: /^\/assets\/upload$/,
    status: 201,
    handler: (ctx) => {
      const file = ctx.body as unknown as File;
      const name = String(ctx.params.filename ?? '未命名素材');
      const mimeType = String(file?.type || 'application/octet-stream');
      const type: ArchivedAsset['type'] = mimeType.startsWith('video/')
        ? 'video'
        : mimeType.startsWith('audio/')
          ? 'audio'
          : 'image';
      const requested = String(ctx.params.category ?? 'source');
      const category: ArchivedAsset['category'] =
        requested === 'character' || requested === 'product' || requested === 'scene' ||
        requested === 'audio' || requested === 'source'
          ? requested
          : 'source';
      const sha = Date.now().toString(16).padStart(64, '0');
      const asset: ArchivedAsset = {
        id: `A${Date.now()}`,
        name,
        category,
        type,
        mimeType,
        sizeBytes: typeof file?.size === 'number' ? file.size : 0,
        path: `materials/${sha.slice(0, 2)}/${sha}.dat`,
        sha256: sha,
        createdAt: new Date().toISOString(),
        width: 0,
        height: 0,
        duration: 0,
        hasAudio: type !== 'image',
        url: '',
        previewUrl: null,
        ownerId: MINE,
        shared: false,
        sharedAt: null,
        deletedAt: null,
        stateVersion: 0,
        editable: true,
        downloadAllowed: true,
      };
      db.assets.unshift(asset);
      return asset;
    },
  },
  {
    method: 'POST',
    pattern: /^\/assets\/import$/,
    status: 201,
    handler: (ctx) => {
      const relativePath = String(ctx.body.relativePath ?? '');
      if (!relativePath) throw new HttpError(400, '请填写归档盘内的相对路径');
      const name = relativePath.split(/[\\/]/).pop() ?? '导入素材';
      const sha = Date.now().toString(16).padStart(64, '0');
      const asset: ArchivedAsset = {
        id: `A${Date.now()}`,
        name,
        category: 'source',
        type: name.endsWith('.mp4') ? 'video' : name.match(/\.(mp3|wav)$/) ? 'audio' : 'image',
        mimeType: name.endsWith('.mp4') ? 'video/mp4' : 'image/jpeg',
        sizeBytes: 0,
        path: relativePath,
        sha256: sha,
        createdAt: new Date().toISOString(),
        hasAudio: false,
        url: '',
        previewUrl: null,
        ownerId: MINE,
        shared: false,
        sharedAt: null,
        deletedAt: null,
        stateVersion: 0,
        editable: true,
        downloadAllowed: true,
      };
      db.assets.unshift(asset);
      return asset;
    },
  },
  {
    method: 'GET',
    // `/assets/candidates`、`/assets/batch/...` 是**子资源集合**，不是一条素材。
    // 用负向断言排除，而不是靠 mock 文件的注册顺序（顺序由文件名字母序决定，
    // 改个文件名就会让详情接口抢走 candidates，症状是"候选列表 404 素材不存在"）。
    pattern: /^\/assets\/(?!candidates$|batch\/)[^/]+$/,
    handler: (ctx) => findAsset(ctx.path.split('/')[2]),
  },
  {
    method: 'PUT',
    pattern: /^\/assets\/[^/]+\/name$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const name = String(ctx.body.name ?? '').trim();
      if (!name) throw new HttpError(400, '名称不能为空');
      return changeAsset(id, { name }, ctx.body.expectedVersion);
    },
  },
  {
    method: 'PUT',
    pattern: /^\/assets\/[^/]+\/sharing$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const shared = Boolean(ctx.body.shared);
      return changeAsset(
        id,
        // 取消分享时把时间戳一并清掉：只翻布尔值会让「分享时间」留在一条私有素材上
        { shared, sharedAt: shared ? new Date().toISOString() : null },
        ctx.body.expectedVersion,
      );
    },
  },
  {
    method: 'PUT',
    pattern: /^\/assets\/[^/]+\/deleted$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const deleted = Boolean(ctx.body.deleted);
      const asset = findAsset(id);
      // 状态没变就不写：与后端一致，否则回收站按删除时间排序会跳动
      if ((asset.deletedAt != null) === deleted) {
        return asset;
      }
      return changeAsset(
        id,
        { deletedAt: deleted ? new Date().toISOString() : null },
        ctx.body.expectedVersion,
      );
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/assets\/[^/]+$/,
    status: 204,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const index = db.assets.findIndex((asset) => asset.id === id);
      if (index < 0) throw new HttpError(404, '素材不存在');
      // 与后端一致：彻底删除只对回收站里的记录生效，这是「删两次」的护栏
      if (db.assets[index].deletedAt == null) {
        throw new HttpError(409, '请先移入回收站再彻底删除');
      }
      db.assets.splice(index, 1);
      return null;
    },
  },
  {
    method: 'GET',
    pattern: /^\/storyboards$/,
    handler: () => ({ items: db.storyboards, total: db.storyboards.length }),
  },
  {
    method: 'GET',
    pattern: /^\/shots$/,
    handler: () => ({ items: db.shots, total: db.shots.length }),
  },
  {
    method: 'GET',
    pattern: /^\/images$/,
    handler: (ctx) => {
      // 每次拉取推进一次后台 worker，mock 模式也能看到 queued → succeeded
      db.tick();
      return {
        items: db.imageJobs,
        total: db.imageJobs.length,
        page: Number(ctx.params.page ?? 1),
        pageSize: Number(ctx.params.pageSize ?? 24),
      };
    },
  },
];
