import { describe, expect, it } from 'vitest';
import type { ReplacementRun } from './replacement.types';
import { replacementTaskFullName, replacementTaskName } from './replacementTaskIdentity';

function run(name?: string, id = '12345678abcdefabcdefabcdefabcdef'): ReplacementRun {
  return {id, kind: 'character', target: '目标人物', status: 'prepared', archivePath: '/archive',
    createdAt: '2026-11-06T00:00:00Z', prompt: '',
    inputSummary: name === undefined ? null : {assets: [{id: 'source', name, role: 'source_video', type: 'video', url: '/video'}]},
  };
}

describe('替换任务视频优先命名', () => {
  it('短视频名移除扩展名，拼接完整任务信息', () => {
    expect(replacementTaskName(run('火车站原片.MP4'))).toBe('火车站原片 · 人物替换 · 目标人物 · #12345678');
  });
  it('长名称仅截取视频名前缀，完整名称仍可获取', () => {
    const task = run('123456789012345678后续内容.mp4');
    expect(replacementTaskName(task)).toBe('123456789012345678… · 人物替换 · 目标人物 · #12345678');
    expect(replacementTaskFullName(task)).toBe('123456789012345678后续内容 · 人物替换 · 目标人物 · #12345678');
    const emoji = run('😀'.repeat(19) + '.mp4');
    expect(replacementTaskName(emoji).startsWith('😀'.repeat(18) + '… · ')).toBe(true);
  });
  it('相同视频与人物保留不同任务短码，历史缺失原片时有明确回退', () => {
    expect(replacementTaskName(run('同一原片.mp4'))).not.toBe(replacementTaskName(run('同一原片.mp4', '87654321abcdefabcdefabcdefabcdef')));
    expect(replacementTaskName(run())).toContain('历史档案未记录原片 · 人物替换');
  });
});
