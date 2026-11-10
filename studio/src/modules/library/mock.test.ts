import { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { describe, expect, it } from 'vitest';
import { mockAdapter } from '@/mocks/adapter';
import type { ApiResponse } from '@/shared/api/types';
import type {
  DownloadJobOut,
  LibraryAssetDetailOut,
  LibraryAssetPage,
  LibraryTagStatOut,
  ShareRequestOut,
} from '@/shared/api/schema';

/**
 * 资产库 v2 的 mock 契约。
 *
 * 断言的是**语义**而不是种子数据本身：草稿箱里只有草稿、团队入口只有团队库、
 * 已共享资产的删除会被 409 拦下并带上窄口提示。种子文案改了不该让这些用例红。
 */

async function call<T>(
  method: string,
  url: string,
  options: { params?: Record<string, unknown>; data?: unknown } = {},
): Promise<{ status: number; body: ApiResponse<T> }> {
  const config = {
    url,
    method,
    params: options.params,
    data: options.data === undefined ? undefined : JSON.stringify(options.data),
    headers: new AxiosHeaders(),
  } as unknown as InternalAxiosRequestConfig;

  const response = await mockAdapter(config);
  return { status: response.status, body: response.data as ApiResponse<T> };
}

async function failure(
  method: string,
  url: string,
  options: { data?: unknown } = {},
): Promise<{ status: number; message: string; details: unknown }> {
  try {
    await call(method, url, options);
    throw new Error(`预期失败但成功了：${method} ${url}`);
  } catch (error) {
    const err = error as {
      response?: { status: number; data: { message: string; details?: unknown } };
      message: string;
    };
    return {
      status: err.response?.status ?? -1,
      message: err.response?.data?.message ?? err.message,
      details: err.response?.data?.details,
    };
  }
}

describe('资产库 mock · 列表与可见范围', () => {
  it('草稿箱只返回草稿', async () => {
    const { body } = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'draft' },
    });
    expect(body.data.items?.length).toBeGreaterThan(0);
    expect(body.data.items?.every((item) => item.state === 'draft')).toBe(true);
  });

  it('团队入口只返回团队库资产', async () => {
    const { body } = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active', library: 'team' },
    });
    expect(body.data.items?.every((item) => item.visibility === 'team')).toBe(true);
  });

  it('显式 library=all 与省略同口径，覆盖三种可见范围且角标等于条数', async () => {
    const all = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active', library: 'all', pageSize: 100 },
    });
    const omitted = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active', pageSize: 100 },
    });
    expect(all.body.data.total).toBe(omitted.body.data.total);
    expect(all.body.data.facets?.all).toBe(all.body.data.total);

    const items = all.body.data.items ?? [];
    expect(items.every((item) => item.state === 'active')).toBe(true);
    expect(new Set(items.map((item) => item.id)).size).toBe(items.length);
    const visibilities = new Set(items.map((item) => item.visibility));
    expect([...visibilities].sort()).toEqual(['group', 'personal', 'team']);
  });

  it('分面统计与列表同源，且各字段都是非负整数', async () => {
    const { body } = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active' },
    });
    const facets = body.data.facets ?? {};
    expect(Object.keys(facets).sort()).toEqual(['all', 'drafts', 'group', 'personal', 'team', 'trash']);
    expect(Object.values(facets).every((value) => Number.isInteger(value) && value >= 0)).toBe(true);
  });

  it('关键词只匹配名称', async () => {
    const { body } = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active', keyword: '人物' },
    });
    expect(body.data.items?.length).toBeGreaterThan(0);
    expect(body.data.items?.every((item) => item.name.includes('人物'))).toBe(true);
  });

  it('选择器里不出现草稿与回收站', async () => {
    const { body } = await call<LibraryAssetPage>('GET', '/library/selection', {
      params: { includePacks: false },
    });
    expect(body.data.items?.every((item) => item.state === 'active' && item.kind !== 'pack')).toBe(true);
  });
});

/**
 * 封装的口径：封完只剩一条，成员的入口是「封装」标签而不是类型筛选。
 *
 * mock 与后端必须同一套语义，否则只看 mock 开发时页面是对的、接上后端就多出
 * 三条重复素材。这里用运行时的 id 而不是写死 `a101`：种子会随用例改动而变号。
 */
describe('资产库 mock · 封装的口径', () => {
  const activeItems = async () => {
    const { body } = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active', pageSize: 50 },
    });
    return body.data.items ?? [];
  };

  it('封装后成员不再单列，封装自动带上「封装」标签', async () => {
    const members = (await activeItems()).filter((item) => item.kind !== 'pack').slice(0, 2);
    expect(members).toHaveLength(2);

    const created = await call<LibraryAssetDetailOut>('POST', '/library/packs', {
      data: { name: '口径用例封装', assetIds: members.map((item) => item.id) },
    });
    expect(created.status).toBe(200);
    expect(created.body.data.tags?.some((tag) => tag.name === '封装')).toBe(true);

    const ids = (await activeItems()).map((item) => item.id);
    expect(ids).toContain(created.body.data.id);
    expect(ids).not.toContain(members[0].id);
    expect(ids).not.toContain(members[1].id);
  });

  it('新建封装自动继承所有成员标签，与后端 sync_pack_tags 同口径', async () => {
    const first = (await call<LibraryAssetDetailOut>('GET', '/library/assets/a101')).body.data;
    const second = (await call<LibraryAssetDetailOut>('GET', '/library/assets/a102')).body.data;
    const created = await call<LibraryAssetDetailOut>('POST', '/library/packs', {
      data: { name: '继承标签封装', assetIds: [first.id, second.id] },
    });
    const names = (created.body.data.tags ?? []).map((tag) => tag.name);
    for (const member of [first, second]) {
      for (const tag of member.tags ?? []) expect(names).toContain(tag.name);
    }
    expect(names).toContain('封装');
    expect(new Set(names).size).toBe(names.length);
  });

  it('选择器仍能挑到封装里的成员', async () => {
    const packs = (await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'active', kind: 'pack' },
    })).body.data.items ?? [];
    expect(packs.length).toBeGreaterThan(0);

    const detail = await call<LibraryAssetDetailOut>('GET', `/library/packs/${packs[0].id}`);
    const memberId = detail.body.data.packItems?.[0]?.assetId;
    expect(memberId).toBeTruthy();

    const selection = await call<LibraryAssetPage>('GET', '/library/selection', {
      params: { includePacks: false, pageSize: 50 },
    });
    expect((selection.body.data.items ?? []).map((item) => item.id)).toContain(memberId);
  });
});

describe('资产库 mock · 写操作的语义', () => {
  it('撤销共享不填理由直接 400', async () => {
    const result = await failure('POST', '/library/share-requests', {
      data: { assetId: 'a103', action: 'revoke', target: 'group', groupId: 'g1' },
    });
    expect(result.status).toBe(400);
    expect(result.message).toContain('理由');
  });

  it('删除已共享给小组的资产：409 并提示可一步撤销', async () => {
    const result = await failure('DELETE', '/library/assets/a103');
    expect(result.status).toBe(409);
    expect((result.details as { canRevokeAndDelete?: boolean }).canRevokeAndDelete).toBe(true);
  });

  it('封装把成员原样带进详情', async () => {
    const { status, body } = await call<LibraryAssetDetailOut>('POST', '/library/packs', {
      data: { name: '用例封装', assetIds: ['a101', 'a102'] },
    });
    expect(status).toBe(200);
    expect(body.data.kind).toBe('pack');
    expect(body.data.packItems?.map((item) => item.assetId)).toEqual(['a101', 'a102']);
  });

  it('打包任务把草稿放进 skipped，而不是整单失败', async () => {
    const drafts = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { state: 'draft' },
    });
    const draftId = drafts.body.data.items?.[0]?.id ?? '';
    const { body } = await call<DownloadJobOut>('POST', '/library/downloads', {
      data: { ids: ['a101', draftId] },
    });
    expect(body.data.status).toBe('succeeded');
    expect(body.data.skipped?.map((item) => item.id)).toContain(draftId);
  });

  it('共享申请审批通过后，资产真的换到目标库', async () => {
    const created = await call<ShareRequestOut>('POST', '/library/share-requests', {
      data: { assetId: 'a101', action: 'share', target: 'group', groupId: 'g2', reason: '同款活动' },
    });
    expect(created.body.data.status).toBe('approved');
    const detail = await call<LibraryAssetDetailOut>('GET', '/library/assets/a101');
    expect(detail.body.data.visibility).toBe('group');
    expect(detail.body.data.groupIds).toContain('g2');
  });
});

describe('资产库 mock · 标签栏计数', () => {
  it('每个标签都带素材数与创建时间，按添加时间升序', async () => {
    const { body } = await call<LibraryTagStatOut[]>('GET', '/library/tags', {
      params: { state: 'active', library: 'personal' },
    });
    const tags = body.data;
    expect(tags.length).toBeGreaterThan(0);
    expect(tags.every((tag) => Number.isInteger(tag.assetCount ?? 0) && !!tag.createdAt)).toBe(true);
    const created = tags.map((tag) => tag.createdAt);
    expect(created).toEqual([...created].sort());
  });

  it('用 PATCH 去掉一个标签后，计数同步减少', async () => {
    const scope = { state: 'active', library: 'personal' };
    const before = (await call<LibraryTagStatOut[]>('GET', '/library/tags', { params: scope })).body.data;
    const tag = before.find((item) => (item.assetCount ?? 0) > 0);
    if (!tag) throw new Error('演示数据里应该有一个非空标签');

    const listed = await call<LibraryAssetPage>('GET', '/library/assets', {
      params: { ...scope, tagIds: [tag.id], pageSize: 100 },
    });
    const assetId = listed.body.data.items?.[0]?.id ?? '';
    expect(assetId).toBeTruthy();

    const detail = await call<LibraryAssetDetailOut>('GET', `/library/assets/${assetId}`);
    const remaining = (detail.body.data.tags ?? [])
      .filter((item) => item.id !== tag.id)
      .map((item) => item.id);
    await call('PATCH', `/library/assets/${assetId}`, { data: { tagIds: remaining } });

    const after = (await call<LibraryTagStatOut[]>('GET', '/library/tags', { params: scope })).body.data;
    expect(after.find((item) => item.id === tag.id)?.assetCount).toBe((tag.assetCount ?? 0) - 1);
  });
});
