import { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { describe, expect, it } from 'vitest';
import { mockAdapter } from '../adapter';
import { db } from '../db';
import type { ApiResponse } from '@/shared/api/types';
import type {
  ArchivedAsset,
  ArchiveList,
  BatchAdoptResult,
  CandidateList,
  DeriveIntent,
  LineageOut,
} from '@/shared/api/schema';

/* ------------------------------------------------------------------
 * 候选 / 血缘 / 衍生的 mock 契约
 *
 * mock 存在的意义是**让前端在真后端之前就撞上真实规矩**。所以这里的每条
 * 断言都对应后端的一条硬约束，而不是"接口能通"：
 *   - 候选不进 `GET /assets`（进了会让前端的分类角标、配额全错）
 *   - 批量采纳全成或全不成（半成功无法在界面上解释）
 *   - 采纳幂等、弃用后不可再采纳
 *   - 上传件没有配方（血缘为空是事实，不是错误）
 * ------------------------------------------------------------------ */

interface MockResult<T> {
  status: number;
  body: ApiResponse<T>;
}

async function call<T>(
  method: string,
  url: string,
  options: {params?: Record<string, unknown>; data?: unknown} = {},
): Promise<MockResult<T>> {
  const config = {
    url,
    method,
    params: options.params,
    data: options.data === undefined ? undefined : JSON.stringify(options.data),
    headers: new AxiosHeaders(),
  } as unknown as InternalAxiosRequestConfig;
  const response = await mockAdapter(config);
  return {status: response.status, body: response.data as ApiResponse<T>};
}

async function failure(method: string, url: string, data?: unknown) {
  try {
    await call(method, url, {data});
    throw new Error(`预期失败但成功了：${method} ${url}`);
  } catch (error) {
    const err = error as {response?: {status: number; data: {message: string}}; message: string};
    return {status: err.response?.status ?? -1, message: err.response?.data?.message ?? err.message};
  }
}

const candidates = async () => (await call<CandidateList>('GET', '/assets/candidates')).body.data;
const listAssets = async () => (await call<ArchiveList>('GET', '/assets')).body.data;

describe('候选 mock · 三条"不许"', () => {
  it('候选不出现在素材列表里（它不进列表、不计配额）', async () => {
    const pending = await candidates();
    const ids = (pending?.groups ?? []).flatMap((group) => (group.items ?? []).map((item) => item.id));
    const listed = await listAssets();
    const listedIds = new Set((listed?.items ?? []).map((item) => item.id));
    for (const id of ids) expect(listedIds.has(id)).toBe(false);
  });

  it('同一 recipe_id 的候选挨在一组，过期时刻取组内最早', async () => {
    const pending = await candidates();
    for (const group of pending?.groups ?? []) {
      const recipeId = db.candidates.find((item) => item.recipeId === group.recipeId);
      expect(recipeId).toBeTruthy();
      const earliest = db.candidates
        .filter((item) => item.recipeId === group.recipeId)
        .map((item) => item.expiresAt)
        .sort()[0];
      expect(group.expiresAt).toBe(earliest);
      // 组内的每一张确实都属于这一批
      for (const item of group.items ?? []) expect(item.recipeId).toBe(group.recipeId);
    }
  });
});

describe('候选 mock · 采纳与弃用', () => {
  it('采纳之后：出现在素材列表、从候选区消失、落了未分类收件箱', async () => {
    const target = db.candidates[0];
    const {status, body} = await call<ArchivedAsset>('POST', `/assets/${target.asset.id}/adopt`, {
      data: {},
    });
    expect(status).toBe(200);
    expect(body.data.stage).toBe('adopted');
    // 没给分类就落收件箱：采纳是高频低摩擦动作，不逼用户先选分类
    expect(body.data.categoryId).toBe('cat-inbox');

    const listed = await listAssets();
    expect((listed?.items ?? []).some((item) => item.id === target.asset.id)).toBe(true);
    const pending = await candidates();
    const ids = (pending?.groups ?? []).flatMap((group) => (group.items ?? []).map((item) => item.id));
    expect(ids).not.toContain(target.asset.id);
  });

  it('采纳是幂等的：再点一次返回现状，而不是 409', async () => {
    const adopted = db.assets.find((item) => item.origin === 'generate' && item.stage === 'adopted');
    expect(adopted).toBeTruthy();
    const {status, body} = await call<ArchivedAsset>('POST', `/assets/${adopted?.id}/adopt`, {
      data: {},
    });
    expect(status).toBe(200);
    expect(body.data.id).toBe(adopted?.id);
  });

  it('弃用之后再采纳会 409（那是用户做过的决定，不能靠重试穿透）', async () => {
    const target = db.candidates[0];
    const discarded = await call<ArchivedAsset>('POST', `/assets/${target.asset.id}/discard`);
    expect(discarded.body.data.stage).toBe('discarded');
    const failed = await failure('POST', `/assets/${target.asset.id}/adopt`, {});
    expect(failed.status).toBe(409);
  });

  it('批量采纳全成或全不成：混进一个不存在的 id，一张都不写', async () => {
    const before = db.candidates.length;
    const ids = db.candidates.slice(0, 2).map((item) => item.asset.id);
    const failed = await failure('POST', '/assets/batch/adopt', {
      assetIds: [...ids, 'C-not-exist'],
    });
    expect(failed.status).toBe(404);
    // 关键点：前面两张也没有被采纳
    expect(db.candidates.length).toBe(before);
  });

  it('批量采纳返回 items 与 already 计数', async () => {
    const ids = db.candidates.slice(0, 2).map((item) => item.asset.id);
    if (!ids.length) return; // 前面的用例可能已经清空候选区
    const {body} = await call<BatchAdoptResult>('POST', '/assets/batch/adopt', {
      data: {assetIds: ids},
    });
    expect(body.data.items).toHaveLength(ids.length);
    expect(body.data.already).toBe(0);
  });
});

describe('血缘与衍生 mock', () => {
  it('上传件的血缘为空（recipe 为 null），而不是报错', async () => {
    const uploaded = db.assets.find((item) => item.origin === 'upload');
    const {status, body} = await call<LineageOut>('GET', `/assets/${uploaded?.id}/lineage`);
    expect(status).toBe(200);
    expect(body.data.recipe).toBeNull();
    expect(body.data.parents).toEqual([]);
  });

  it('生成件带配方全文、参考图与被引用关系', async () => {
    const generated = db.assets.find((item) => item.origin === 'generate' && item.recipeId === 'R100');
    const {body} = await call<LineageOut>('GET', `/assets/${generated?.id}/lineage`);
    expect(body.data.recipe?.prompt).toBeTruthy();
    expect(body.data.references?.map((ref) => ref.assetId)).toEqual(['A1002']);
    // R200 引用了 A1000/A1001，它们的产物是候选 → "谁在用它"里应当出现下游产物
    expect((body.data.usedBy ?? []).some((item) => item.kind === 'asset')).toBe(true);
  });

  it('衍生链上能看到父配方', async () => {
    const child = db.candidates.find((item) => item.recipeId === 'R201');
    if (!child) return; // 该批可能已被前面的用例采纳/弃用
    const {body} = await call<LineageOut>('GET', `/assets/${child.asset.id}/lineage`);
    expect(body.data.parents?.[0]?.recipe.id).toBe('R101');
  });

  it('上传件提不出提示词（400），图片位的衍生回填参考图', async () => {
    const uploaded = db.assets.find((item) => item.origin === 'upload' && item.type === 'image');
    const failed = await failure('POST', `/assets/${uploaded?.id}/derive`, {mode: 'prompt_extract'});
    expect(failed.status).toBe(400);

    const {body} = await call<DeriveIntent>('POST', `/assets/${uploaded?.id}/derive`, {
      data: {mode: 'image_ref'},
    });
    expect(body.data.referenceAssetIds).toEqual([uploaded?.id]);
    expect(body.data.referenceRole).toBe('reference');
  });

  it('视频不能当首帧：要求先截一帧（隐式抽帧是另一条写路径）', async () => {
    const video = db.assets.find((item) => item.type === 'video');
    const failed = await failure('POST', `/assets/${video?.id}/derive`, {mode: 'video_first_frame'});
    expect(failed.status).toBe(400);
  });
});

describe('单条标签', () => {
  it('读写同一份：PUT 之后再 GET 拿到的是新集合，空数组是清空', async () => {
    // A1001 / A1002 是演示账号自己的；A1000 属于别人（只能读，不能改）
    const id = 'A1001';
    await call('PUT', `/assets/${id}/tags`, {data: {tagIds: ['style-1', 'shot-2']}});
    const after = await call<{assetId: string; items: Array<{id: string}>}>('GET', `/assets/${id}/tags`);
    expect(after.body.data.items?.map((tag) => tag.id)).toEqual(['style-1', 'shot-2']);

    await call('PUT', `/assets/${id}/tags`, {data: {tagIds: []}});
    const cleared = await call<{items: Array<{id: string}>}>('GET', `/assets/${id}/tags`);
    expect(cleared.body.data.items).toEqual([]);
  });

  it('别人的素材可以读标签，但不能改', async () => {
    const read = await call<{assetId: string}>('GET', '/assets/A1000/tags');
    expect(read.status).toBe(200);
    const failed = await failure('PUT', '/assets/A1000/tags', {tagIds: ['style-1']});
    expect(failed.status).toBe(404);
  });

  it('词表外的 id 被拒绝，不静默丢弃', async () => {
    const failed = await failure('PUT', '/assets/A1002/tags', {tagIds: ['no-such-tag']});
    expect(failed.status).toBe(422);
  });
});
