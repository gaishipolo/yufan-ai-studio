import { describe, expect, it } from 'vitest';
import { TASK_STATUS_LABEL, isTaskInFlight, type TaskStatus } from './types';

const STATUSES: TaskStatus[] = ['queued', 'running', 'completed', 'failed'];

describe('isTaskInFlight', () => {
  it('只有排队中和生成中需要继续轮询', () => {
    expect(isTaskInFlight({ status: 'queued' } as never)).toBe(true);
    expect(isTaskInFlight({ status: 'running' } as never)).toBe(true);
  });

  it('终态必须停止轮询，否则列表会永远每 2.5s 打一次接口', () => {
    expect(isTaskInFlight({ status: 'completed' } as never)).toBe(false);
    expect(isTaskInFlight({ status: 'failed' } as never)).toBe(false);
  });
});

describe('TASK_STATUS_LABEL', () => {
  it('四种状态都有中文文案，新增状态时这里会先挂', () => {
    for (const status of STATUSES) {
      expect(TASK_STATUS_LABEL[status]).toBeTruthy();
    }
  });
});
