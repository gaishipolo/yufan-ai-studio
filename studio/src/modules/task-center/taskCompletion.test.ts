import { describe, expect, it } from 'vitest';
import {
  completedSince,
  completionMessage,
  toSnapshotMap,
  type TaskSnapshot,
} from './taskCompletion';

const task = (
  key: string,
  group: TaskSnapshot['group'],
  title = '芳华里',
): TaskSnapshot => ({ key, group, title });

describe('completedSince', () => {
  it('在跑 → 完成，命中一条', () => {
    const before = toSnapshotMap([task('video:a', 'running')]);
    const done = completedSince(before, [task('video:a', 'completed')]);

    expect(done).toEqual([{ key: 'video:a', group: 'completed', title: '芳华里' }]);
  });

  it('排队 → 失败也算落定', () => {
    const before = toSnapshotMap([task('video:a', 'queued')]);
    expect(completedSince(before, [task('video:a', 'failed')])).toHaveLength(1);
  });

  it('待处理（等额度/登录）之后落定，也要报', () => {
    const before = toSnapshotMap([task('video:a', 'attention')]);
    expect(completedSince(before, [task('video:a', 'completed')])).toHaveLength(1);
  });

  it('生图任务同样覆盖：只有 feed 里才有 image 来源', () => {
    const before = toSnapshotMap([task('image:x', 'running')]);
    expect(completedSince(before, [task('image:x', 'completed')])).toHaveLength(1);
  });

  it('image 与 video 的 id 相同也不会串：key 带来源前缀', () => {
    const before = toSnapshotMap([task('image:1', 'running')]);
    expect(completedSince(before, [task('video:1', 'completed')])).toHaveLength(0);
  });

  it('首次出现的新任务不弹：否则刚提交就报「已完成」', () => {
    const before = toSnapshotMap([]);
    expect(completedSince(before, [task('video:new', 'completed')])).toHaveLength(0);
  });

  it('上一轮就已经是终态的，不重复报', () => {
    const before = toSnapshotMap([task('video:a', 'completed')]);
    expect(completedSince(before, [task('video:a', 'completed')])).toHaveLength(0);
  });

  it('还在跑 / 仍排队的不算完成', () => {
    const before = toSnapshotMap([task('video:a', 'queued')]);
    expect(completedSince(before, [task('video:a', 'running')])).toHaveLength(0);
  });

  it('一轮里同时落定多条，按出现顺序返回', () => {
    const before = toSnapshotMap([task('video:a', 'running'), task('image:b', 'running')]);
    const done = completedSince(before, [
      task('video:a', 'failed'),
      task('image:b', 'completed'),
    ]);

    expect(done.map((item) => item.key)).toEqual(['video:a', 'image:b']);
  });
});

describe('completionMessage', () => {
  it('成功与失败用不同措辞', () => {
    expect(completionMessage({ key: 'a', group: 'completed', title: '芳华里' })).toBe(
      '「芳华里」已完成',
    );
    expect(completionMessage({ key: 'a', group: 'failed', title: '芳华里' })).toBe(
      '「芳华里」生成失败',
    );
  });

  it('长标题截断：toast 撑成两行会挡住内容', () => {
    const long = '一'.repeat(40);
    const message = completionMessage({ key: 'a', group: 'completed', title: long });

    expect(message).toContain('…');
    expect(message.length).toBeLessThan(24);
  });

  it('空标题退回通用叫法，不显示一对空引号', () => {
    expect(completionMessage({ key: 'a', group: 'completed', title: '  ' })).toBe(
      '「任务」已完成',
    );
  });
});
