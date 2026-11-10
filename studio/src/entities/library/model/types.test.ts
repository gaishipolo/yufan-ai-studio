import { describe, expect, it } from 'vitest';
import {
  VIEWS,
  deleteOffer,
  isTrashExpired,
  parseView,
  trashDaysLeft,
  viewAllowsCreate,
  viewMeta,
  viewQuery,
} from './types';

const ME = 'u_001';

function asset(overrides: Partial<{ ownerId: string; visibility: string; state: string }> = {}) {
  return {
    ownerId: ME,
    visibility: 'personal',
    state: 'active',
    ...overrides,
  } as { ownerId: string; visibility: 'personal' | 'group' | 'team'; state: 'draft' | 'active' | 'trash' };
}

describe('视图与查询参数的映射', () => {
  it('入口顺序：总览在前，草稿箱与回收站挪到末尾', () => {
    expect(VIEWS.map((item) => item.label)).toEqual([
      '全部素材',
      '个人',
      '小组',
      '团队',
      '草稿箱',
      '回收站',
    ]);
    // 徽章取各自的分面字段，「全部素材」不能误用 personal 的数字
    expect(VIEWS.map((item) => item.facet)).toEqual([
      'all',
      'personal',
      'group',
      'team',
      'drafts',
      'trash',
    ]);
  });

  it('草稿箱与回收站只传 state，不传可见范围', () => {
    expect(viewQuery('drafts')).toEqual({ state: 'draft' });
    expect(viewQuery('trash')).toEqual({ state: 'trash' });
  });

  it('全部 / 个人 / 小组 / 团队是 state=active + library', () => {
    expect(viewQuery('all')).toEqual({ state: 'active', library: 'all' });
    expect(viewQuery('personal')).toEqual({ state: 'active', library: 'personal' });
    expect(viewQuery('group')).toEqual({ state: 'active', library: 'group' });
    expect(viewQuery('team')).toEqual({ state: 'active', library: 'team' });
  });

  it('非法 view 落回全部素材，而不是白屏', () => {
    expect(parseView(null)).toBe('all');
    expect(parseView('nope')).toBe('all');
    expect(parseView('personal')).toBe('personal');
    expect(viewMeta('nope' as never).label).toBe('全部素材');
  });

  it('总览与个人入口能上传、能建封装，其余入口不出现按钮', () => {
    expect(viewAllowsCreate('all')).toBe(true);
    expect(viewAllowsCreate('personal')).toBe(true);
    expect(viewAllowsCreate('group')).toBe(false);
    expect(viewAllowsCreate('team')).toBe(false);
    expect(viewAllowsCreate('drafts')).toBe(false);
    expect(viewAllowsCreate('trash')).toBe(false);
  });
});

describe('删除按钮的显示规则（§3.4）', () => {
  const viewer = { id: ME, canDeleteAny: false };
  const admin = { id: ME, canDeleteAny: true };

  it('本人个人库资产：移入回收站', () => {
    expect(deleteOffer(asset(), viewer)).toBe('trash');
  });

  it('本人已共享资产：只能取消共享，不能直接删', () => {
    expect(deleteOffer(asset({ visibility: 'group' }), viewer)).toBe('revoke');
    expect(deleteOffer(asset({ visibility: 'team' }), viewer)).toBe('revoke');
  });

  it('他人资产：普通用户没有删除入口', () => {
    expect(deleteOffer(asset({ ownerId: 'u_002' }), viewer)).toBe('none');
  });

  it('管理员对他人资产给「强制移入」，但仍不是直接删', () => {
    expect(deleteOffer(asset({ ownerId: 'u_002' }), admin)).toBe('force');
  });

  it('草稿与回收站不走这套按钮', () => {
    expect(deleteOffer(asset({ state: 'draft' }), admin)).toBe('none');
    expect(deleteOffer(asset({ state: 'trash' }), admin)).toBe('none');
  });

  it('团队库里的他人资产：管理员走强制移入，后端补自审撤销单', () => {
    expect(deleteOffer(asset({ ownerId: 'u_002', visibility: 'team' }), admin)).toBe('force');
  });
});

describe('回收站剩余天数', () => {
  const now = Date.parse('2026-11-09T00:00:00Z');

  it('按天向上取整，过期归零', () => {
    expect(trashDaysLeft('2026-11-12T00:00:00Z', now)).toBe(3);
    expect(trashDaysLeft('2026-11-09T06:00:00Z', now)).toBe(1);
    expect(trashDaysLeft('2026-11-08T00:00:00Z', now)).toBe(0);
  });

  it('没有截止时间就不显示徽章', () => {
    expect(trashDaysLeft(null, now)).toBeNull();
    expect(trashDaysLeft(undefined, now)).toBeNull();
  });

  it('过去时间算过期', () => {
    expect(isTrashExpired('2026-11-08T00:00:00Z', now)).toBe(true);
    expect(isTrashExpired('2026-11-10T00:00:00Z', now)).toBe(false);
  });
});
