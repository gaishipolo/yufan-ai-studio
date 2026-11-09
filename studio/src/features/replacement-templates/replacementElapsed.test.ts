import { describe, expect, it } from 'vitest';
import { replacementElapsedText } from './replacementElapsed';

const base = { status: 'success', startedAt: '2026-11-09T08:00:00Z' };

describe('replacementElapsedText', () => {
  it('结束的一轮给最终耗时', () => {
    expect(
      replacementElapsedText({ ...base, finishedAt: '2026-11-09T08:02:05Z', elapsedSec: 125 }),
    ).toBe('2 分 5 秒');
  });

  it('在途任务标成「已用」，不冒充最终值', () => {
    expect(replacementElapsedText({ ...base, status: 'running', finishedAt: null, elapsedSec: 40 }))
      .toBe('已用 40 秒');
  });

  it('没进入提交环节说「尚未开始」', () => {
    for (const status of ['preparing', 'prepared', 'prepare_failed']) {
      expect(replacementElapsedText({ status, startedAt: null, finishedAt: null, elapsedSec: null }))
        .toBe('尚未开始');
    }
  });

  it('提交过却没有时间戳（历史档案）说「未记录」', () => {
    expect(
      replacementElapsedText({ status: 'success', startedAt: null, finishedAt: null, elapsedSec: null }),
    ).toBe('未记录');
  });
});
