import { describe, expect, it } from 'vitest';
import {
  inFlightLibtvIds,
  isLibtvModelId,
  isQueued,
  queueLabel,
  upstreamTaskId,
  type LibTvQueueItem,
} from './types';

function item(patch: Partial<LibTvQueueItem> = {}): LibTvQueueItem {
  return {
    taskId: '20261117193603599744489',
    status: 0,
    percent: 9,
    queueReason: '',
    queuePosition: 0,
    queueTotal: 0,
    queueLimit: 0,
    power: 0,
    benefitTag: '',
    remainSeconds: 0,
    describe: '',
    ...patch,
  };
}

describe('upstreamTaskId', () => {
  it('从 `<归档工作区>:<上游 task_id>` 里只取后半段', () => {
    // 传整串给排队接口会得到空结果，看起来像「查不到排队」，其实是参数错了
    expect(upstreamTaskId('29dc66ae263043b8be054570b2873e7f:20261117193603599744489')).toBe(
      '20261117193603599744489',
    );
  });

  it('没有分隔符 / 空值一律返回 null，不拿半截 id 去问上游', () => {
    expect(upstreamTaskId(null)).toBeNull();
    expect(upstreamTaskId(undefined)).toBeNull();
    expect(upstreamTaskId('')).toBeNull();
    expect(upstreamTaskId('20261117193603599744489')).toBeNull();
    expect(upstreamTaskId('29dc66ae263043b8be054570b2873e7f:')).toBeNull();
  });
});

describe('queueLabel', () => {
  it('0 积分队列显示位次与名额', () => {
    const label = queueLabel(
      item({ queueReason: 'UNLIMITED_SERIAL', queuePosition: 3, queueLimit: 10 }),
    );
    expect(label).toBe('0 积分队列 3/10');
  });

  it('限流显示还要等多久', () => {
    expect(queueLabel(item({ queueReason: 'RATE_LIMIT', remainSeconds: 144 }))).toBe(
      '上游限流 · 约 144s',
    );
    expect(queueLabel(item({ queueReason: 'RATE_LIMIT' }))).toBe('上游限流中');
  });

  it('真在生成时不出字：百分比已经在旁边了，再写一句是重复信息', () => {
    expect(queueLabel(item({ queueReason: '', status: 1, percent: 48 }))).toBe('');
    expect(queueLabel(null)).toBe('');
  });
});

describe('isQueued', () => {
  it('只有「有排队原因且还没开始跑」才算排队', () => {
    expect(isQueued(item({ queueReason: 'UNLIMITED_SERIAL' }))).toBe(true);
    expect(isQueued(item({ queueReason: 'UNLIMITED_SERIAL', status: 1 }))).toBe(false);
    expect(isQueued(item())).toBe(false);
  });
});

describe('isLibtvModelId & inFlightLibtvIds', () => {
  it('覆盖全部 LibTV 模型与历史别名，过滤非 LibTV 模型', () => {
    expect(isLibtvModelId('libtv-wan3.0')).toBe(true);
    expect(isLibtvModelId('libtv-wan3-replacement')).toBe(true);
    expect(isLibtvModelId('libtv-seedance-2.5')).toBe(true);
    expect(isLibtvModelId('libtv-seedance-2.5-flash')).toBe(true);
    expect(isLibtvModelId('libtv-seedance-2.0')).toBe(true);
    expect(isLibtvModelId('bailian-wan3-replacement')).toBe(false);
    expect(isLibtvModelId(null)).toBe(false);

    expect(
      inFlightLibtvIds([
        { status: 'running', modelId: 'libtv-seedance-2.5', externalId: 'r1:t1' },
        { status: 'queued', modelId: 'libtv-seedance-2.5-flash', externalId: 'r2:t2' },
        { status: 'running', modelId: 'libtv-seedance-2.0', externalId: 'r3:t3' },
        { status: 'running', modelId: 'libtv-wan3.0', externalId: 'r4:t4' },
        { status: 'running', modelId: 'libtv-wan3-replacement', externalId: 'r5:t5' },
        { status: 'completed', modelId: 'libtv-seedance-2.5', externalId: 'r6:t6' },
        { status: 'running', modelId: 'bailian-wan3-replacement', externalId: 'r7:t7' },
      ]),
    ).toEqual(['t1', 't2', 't3', 't4', 't5']);
  });
});
