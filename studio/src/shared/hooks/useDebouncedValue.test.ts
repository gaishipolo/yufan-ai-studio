import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDebouncedValue } from './useDebouncedValue';

describe('useDebouncedValue', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('初始值立即返回，不等待', () => {
    const { result } = renderHook(() => useDebouncedValue('初始', 300));
    expect(result.current).toBe('初始');
  });

  it('延迟未到之前保持旧值，到点才更新', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useDebouncedValue(value, 300),
      { initialProps: { value: '' } },
    );

    rerender({ value: 'a' });
    act(() => vi.advanceTimersByTime(299));
    expect(result.current).toBe('');

    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe('a');
  });

  it('连续输入只在最后一次停手后更新（避免每键一次请求）', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useDebouncedValue(value, 300),
      { initialProps: { value: '' } },
    );

    rerender({ value: '超' });
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: '超市' });
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: '超市抢' });

    // 累计 400ms 但每次都重置了定时器，此时仍应停在初始值
    expect(result.current).toBe('');

    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toBe('超市抢');
  });

  it('卸载后不再 setState（清理定时器）', () => {
    const { rerender, unmount } = renderHook(
      ({ value }: { value: string }) => useDebouncedValue(value, 300),
      { initialProps: { value: '' } },
    );

    rerender({ value: 'x' });
    unmount();

    // 若定时器没清掉，这里会报 "state update on unmounted component"
    expect(() => act(() => vi.advanceTimersByTime(300))).not.toThrow();
  });
});
