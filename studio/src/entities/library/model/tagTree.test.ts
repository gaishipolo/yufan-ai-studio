import { describe, expect, it } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { effectiveTagSelection, scopeAllowedTags } from './tagTree';

/** 树是三级的：产品 → 3030 → X100，产品 → T40，角色 → 菜菜。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 1, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't12', name: 'T40', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
  { id: 't31', name: 'X100', parentId: 't11', parentName: '产品 / 3030', depth: 3, assetCount: 1, childCount: 0, createdAt: '2026-11-09T10:07:00Z' },
];

describe('effectiveTagSelection · 选中标签 → 真正参与筛选的标签', () => {
  it('跨链多选原样保留（并集）', () => {
    expect(effectiveTagSelection(TAGS, ['t11', 't2'])).toEqual(['t11', 't2']);
    expect(effectiveTagSelection(TAGS, ['t1', 't21'])).toEqual(['t1', 't21']);
  });

  it('同一条链上只留最具体的一层（父的范围含子，留着会重复）', () => {
    expect(effectiveTagSelection(TAGS, ['t1', 't11'])).toEqual(['t11']);
    expect(effectiveTagSelection(TAGS, ['t1', 't11', 't31'])).toEqual(['t31']);
    expect(effectiveTagSelection(TAGS, ['t11', 't31'])).toEqual(['t31']);
  });

  it('同一层可以有多个（两个系列都留着）', () => {
    expect(effectiveTagSelection(TAGS, ['t11', 't12'])).toEqual(['t11', 't12']);
  });

  it('同一个一级下的兄弟分支都要留着（互不包含，丢一支就是静默筛掉一整批素材）', () => {
    expect(effectiveTagSelection(TAGS, ['t12', 't31'])).toEqual(['t12', 't31']);
    expect(effectiveTagSelection(TAGS, ['t1', 't12', 't31'])).toEqual(['t12', 't31']);
  });

  it('词表里没有的 id 被丢掉（URL 手改过也不该带进请求）', () => {
    expect(effectiveTagSelection(TAGS, ['t1', 'nope'])).toEqual(['t1']);
  });
});

describe('scopeAllowedTags · 候选集范围里能挑的标签', () => {
  it('范围为空 = 不限，所有标签都能挑', () => {
    expect(scopeAllowedTags(TAGS, []).map((tag) => tag.id)).toEqual([
      't1',
      't2',
      't11',
      't12',
      't21',
      't31',
    ]);
  });

  it('范围给了就只给它的子树（作者与使用者看到的必须是同一批素材）', () => {
    expect(scopeAllowedTags(TAGS, ['t1']).map((tag) => tag.id)).toEqual([
      't1',
      't11',
      't12',
      't31',
    ]);
    expect(scopeAllowedTags(TAGS, ['t11']).map((tag) => tag.id)).toEqual(['t11', 't31']);
  });
});
