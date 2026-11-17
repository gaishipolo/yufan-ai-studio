import { describe, expect, it } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { effectiveTagSelection, scopeAllowedTags } from './tagTree';

/**
 * 树是两级的：一级是**维度**（产品 / 角色），二级是**取值**（3030 / T40 / 菜菜）。
 *
 * 组合语义（与后端 `tag_filter_clause` 一致）：
 * 同维度多选是**或**，跨维度是**且**。
 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't12', name: 'T40', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
];

describe('effectiveTagSelection · 选中标签 → 真正参与筛选的标签', () => {
  it('跨维度多选原样保留（后端按「且」处理）', () => {
    expect(effectiveTagSelection(TAGS, ['t11', 't2'])).toEqual(['t11', 't2']);
    expect(effectiveTagSelection(TAGS, ['t1', 't21'])).toEqual(['t1', 't21']);
  });

  it('同一维度里选了维度又选了取值 → 只留取值（维度的范围含它的取值）', () => {
    expect(effectiveTagSelection(TAGS, ['t1', 't11'])).toEqual(['t11']);
    expect(effectiveTagSelection(TAGS, ['t1', 't11', 't12'])).toEqual(['t11', 't12']);
  });

  it('同一维度下的多个取值都要留着（两个产品都留着，后端按「或」处理）', () => {
    expect(effectiveTagSelection(TAGS, ['t11', 't12'])).toEqual(['t11', 't12']);
  });

  it('选了一个维度时，同维度的取值仍然只留取值那一档', () => {
    // 维度与取值是父子关系：只留取值，否则看到的是整个产品线
    expect(effectiveTagSelection(TAGS, ['t1', 't12'])).toEqual(['t12']);
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
    ]);
  });

  it('范围给了就只给它的子树（作者与使用者看到的必须是同一批素材）', () => {
    expect(scopeAllowedTags(TAGS, ['t1']).map((tag) => tag.id)).toEqual([
      't1',
      't11',
      't12',
    ]);
    expect(scopeAllowedTags(TAGS, ['t11']).map((tag) => tag.id)).toEqual(['t11']);
  });
});
