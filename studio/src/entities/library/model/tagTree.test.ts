import { describe, expect, it } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  effectiveTagSelection,
  leafTagsForSelection,
  scopeFilterIds,
  secondLevelForSelection,
} from './tagTree';

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

describe('secondLevelForSelection · 二级行该列哪些取值', () => {
  it('没选任何东西时列全部取值', () => {
    expect(secondLevelForSelection(TAGS, []).map((tag) => tag.id)).toEqual([
      't11',
      't12',
      't21',
    ]);
  });

  it('选了一级维度 → 只列那个维度下的取值', () => {
    expect(secondLevelForSelection(TAGS, ['t1']).map((tag) => tag.id)).toEqual([
      't11',
      't12',
    ]);
  });

  it('选了多个维度 → 列这几条轴的取值之和', () => {
    expect(
      secondLevelForSelection(TAGS, ['t1', 't2']).map((tag) => tag.id),
    ).toEqual(['t11', 't12', 't21']);
  });

  it('选了一个二级取值 → 仍列它所属维度的**全部**取值（才能同维度多选）', () => {
    expect(secondLevelForSelection(TAGS, ['t11']).map((tag) => tag.id)).toEqual([
      't11',
      't12',
    ]);
  });

  it('选了取值又选了别的维度 → 两条轴都列', () => {
    expect(
      secondLevelForSelection(TAGS, ['t11', 't2']).map((tag) => tag.id),
    ).toEqual(['t11', 't12', 't21']);
  });
});

describe('leafTagsForSelection · 取值行列哪些能打的词', () => {
  it('没选任何东西时列全部叶子（有子标签的维度不出现）', () => {
    expect(leafTagsForSelection(TAGS, []).map((tag) => tag.id)).toEqual([
      't11',
      't12',
      't21',
    ]);
  });

  it('选了维度 → 只列那个维度下的叶子', () => {
    expect(leafTagsForSelection(TAGS, ['t1']).map((tag) => tag.id)).toEqual([
      't11',
      't12',
    ]);
  });

  it('选了取值 → 仍列它所属维度的全部叶子（才能同维度多选）', () => {
    expect(leafTagsForSelection(TAGS, ['t11']).map((tag) => tag.id)).toEqual([
      't11',
      't12',
    ]);
  });

  it('孤立的一级词也是叶子，也该列出来（按层级取会漏掉它）', () => {
    /* 「音乐」没有子标签 —— 它自己就能打在素材上，
       所以它属于「取值」而不是「一级维度」。
       顺序按添加时间：「音乐」建得最早，排最前。 */
    const withOrphan = [
      ...TAGS,
      {
        id: 't3',
        name: '音乐',
        assetCount: 2,
        childCount: 0,
        createdAt: '2026-11-09T10:03:00Z',
      },
    ];
    expect(leafTagsForSelection(withOrphan, []).map((tag) => tag.id)).toEqual([
      't3',
      't11',
      't12',
      't21',
    ]);
  });

  it('有子标签的一级维度不出现在取值行', () => {
    const ids = leafTagsForSelection(TAGS, []).map((tag) => tag.id);
    expect(ids).not.toContain('t1');
    expect(ids).not.toContain('t2');
  });
});

describe('scopeFilterIds · 候选集 + 使用者的挑选', () => {
  it('都没挑时就是候选集本身', () => {
    expect(scopeFilterIds(TAGS, ['t1', 't21'], [])).toEqual(['t1', 't21']);
  });

  it('候选集为空就只有使用者的挑', () => {
    expect(scopeFilterIds(TAGS, [], ['t11'])).toEqual(['t11']);
  });

  it('同一维度：使用者的挑**替换**候选集里那一维的标签（不是叠加）', () => {
    // 候选集「产品」，使用者挑「产品 / 3030」→ 只剩 3030。
    // 叠加成两个会变成「整个产品线 ∩ 3030」，而那正是产品线。
    expect(scopeFilterIds(TAGS, ['t1'], ['t11'])).toEqual(['t11']);
  });

  it('跨维度：使用者的挑加进来，候选集那一维原样保留', () => {
    // 候选集「产品」，使用者挑「角色 / 菜菜」→ 产品 ∩ 菜菜
    expect(scopeFilterIds(TAGS, ['t1'], ['t21'])).toEqual(['t1', 't21']);
  });

  it('候选集两个维度，使用者挑其中一个 → 那一个被替换，另一个保留', () => {
    // 候选集「产品 + 角色」，使用者挑「产品 / 3030」
    // → 3036 ∩ 角色，而不是「3030 ∩ 角色 ∩ 整个产品线」
    expect(scopeFilterIds(TAGS, ['t1', 't2'], ['t11'])).toEqual(['t11', 't2']);
  });

  it('同一维度挑了多个取值 → **全都留着**（后端组内是或）', () => {
    // 挑「3030 + T40」= 两个产品都要看。
    // 逐个覆盖会只剩最后一个 → 界面上三个胶囊都高亮，却只筛出最后一档。
    expect(scopeFilterIds(TAGS, ['t1'], ['t11', 't12'])).toEqual(['t11', 't12']);
  });

  it('同维度多个取值替换候选集那一维时，也是**整组替换**', () => {
    // 候选集「产品」，使用者挑「3030 + T40」→ 两个取值都替掉整个产品维度，
    // 但不能把另一个维度的候选集标签也吃掉。
    expect(scopeFilterIds(TAGS, ['t1', 't2'], ['t11', 't12'])).toEqual([
      't11',
      't12',
      't2',
    ]);
  });

  it('词表里没有的 id 被丢掉', () => {
    expect(scopeFilterIds(TAGS, ['nope'], [])).toEqual([]);
    expect(scopeFilterIds(TAGS, [], ['nope'])).toEqual([]);
  });
});
