import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatRelative,
} from './format';

describe('formatDuration', () => {
  it('60 秒以内按秒展示', () => {
    expect(formatDuration(5)).toBe('5s');
    expect(formatDuration(59)).toBe('59s');
  });

  it('超过 60 秒换算成分秒，整除时不带秒', () => {
    expect(formatDuration(80)).toBe('1m20s');
    expect(formatDuration(120)).toBe('2m');
  });
});

describe('formatDateTime', () => {
  it('输出「月-日 时:分」并补零', () => {
    // 本地时区构造，避免用 UTC 字符串导致不同时区断言的时差
    expect(formatDateTime(new Date(2026, 10, 5, 9, 3).toISOString())).toBe('11-05 09:03');
  });

  it('非法时间回落为占位符而不是 Invalid Date', () => {
    expect(formatDateTime('not-a-date')).toBe('—');
  });
});

describe('formatBytes', () => {
  it('0 与小于 1KB 的边界', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
  });

  it('按 1024 进位并保留一位小数', () => {
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(16_800_000)).toBe('16.0 MB');
  });
});

describe('formatNumber', () => {
  it('千分位', () => {
    expect(formatNumber(1234)).toBe('1,234');
  });
});

describe('formatRelative', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('按分钟 / 小时 / 天三档收敛', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-05T12:00:00Z'));

    expect(formatRelative('2026-11-05T11:59:30Z')).toBe('刚刚');
    expect(formatRelative('2026-11-05T11:30:00Z')).toBe('30 分钟前');
    expect(formatRelative('2026-11-05T09:00:00Z')).toBe('3 小时前');
    expect(formatRelative('2026-11-03T12:00:00Z')).toBe('2 天前');
  });
});
