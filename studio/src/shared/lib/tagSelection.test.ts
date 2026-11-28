import { describe, expect, it } from 'vitest';
import { tagRequestIds, tagSelection, toggleTagSelection } from './tagSelection';

const TAGS = [
  { id: 'd1' },
  { id: 'd2' },
  { id: 'v1', parentId: 'd1' },
  { id: 'v2', parentId: 'd1' },
  { id: 'v3', parentId: 'd2' },
];

describe('tagSelection', () => {
  it('选中二级时父维度保持高亮，第二行只列该维度', () => {
    const selection = tagSelection(TAGS, ['v1']);

    expect(selection.activeIds).toEqual(new Set(['d1', 'v1']));
    expect(selection.dimensions.map((tag) => tag.id)).toEqual(['d1', 'd2']);
    expect(selection.values.map((tag) => tag.id)).toEqual(['v1', 'v2']);
    expect(selection.requestIds).toEqual(['v1']);
  });

  it('父子同时出现时只发最具体的那一档', () => {
    expect(tagRequestIds(TAGS, ['d1', 'v1'])).toEqual(['v1']);
    expect(tagRequestIds(TAGS, ['d1', 'd2'])).toEqual(['d1', 'd2']);
  });

  it('未知 id 不参与高亮与请求', () => {
    const selection = tagSelection(TAGS, ['missing', 'v3']);

    expect(selection.activeIds).toEqual(new Set(['d2', 'v3']));
    expect(selection.requestIds).toEqual(['v3']);
  });
});

describe('toggleTagSelection', () => {
  it('单选点二级会把整条轴替换成父 + 子', () => {
    expect(toggleTagSelection(TAGS, ['v3'], TAGS[2])).toEqual(['d1', 'v1']);
  });

  it('点已选中的二级只去掉它，父维度保留', () => {
    expect(toggleTagSelection(TAGS, ['d1', 'v1'], TAGS[2])).toEqual(['d1']);
  });

  it('点已选中的父维度会连同它的下级一起清掉', () => {
    expect(toggleTagSelection(TAGS, ['d1', 'v1'], TAGS[0])).toEqual([]);
  });

  it('多选模式保留同维度的多个取值', () => {
    expect(toggleTagSelection(TAGS, ['v1'], TAGS[3], { single: false })).toEqual([
      'v1',
      'v2',
    ]);
  });
});