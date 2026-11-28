import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useGenerationSubmit } from './useGenerationSubmit';

type TestPayload = { prompt: string; idempotencyKey?: string | null };

describe('useGenerationSubmit', () => {
  it('给每次提交补幂等键，成功后换新键', async () => {
    const submit = vi.fn(async (_payload: TestPayload) => ({ id: 'ok' }));
    const { result } = renderHook(() =>
      useGenerationSubmit<TestPayload, { id: string }>({
        submit,
      }),
    );

    await act(async () => {
      await result.current.submit({ prompt: '第一张' });
    });
    await act(async () => {
      await result.current.submit({ prompt: '第二张' });
    });

    const firstKey = submit.mock.calls[0][0].idempotencyKey;
    const secondKey = submit.mock.calls[1][0].idempotencyKey;
    expect(firstKey).toMatch(/^[a-f0-9]{32}-[a-f0-9]{16}$/);
    expect(secondKey).toMatch(/^[a-f0-9]{32}-[a-f0-9]{16}$/);
    expect(secondKey).not.toBe(firstKey);
  });

  it('失败保留同一意图，重试同 payload 复用键', async () => {
    const submit = vi
      .fn<(_payload: TestPayload) => Promise<{ id: string }>>()
      .mockRejectedValueOnce(new Error('网络断开'))
      .mockResolvedValueOnce({ id: 'ok' });
    const onError = vi.fn();
    const { result } = renderHook(() =>
      useGenerationSubmit<TestPayload, { id: string }>({
        submit,
        onError,
      }),
    );

    await act(async () => {
      await result.current.submit({ prompt: '同一张' });
    });
    await waitFor(() => expect(result.current.error).toBe('网络断开'));
    expect(onError).toHaveBeenCalledWith('网络断开');

    await act(async () => {
      await result.current.submit({ prompt: '同一张' });
    });

    expect(submit.mock.calls[0][0].idempotencyKey).toBe(
      submit.mock.calls[1][0].idempotencyKey,
    );
  });

  it('成功回调拿到 mutation 结果，pending 覆盖整个提交', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const onSuccess = vi.fn();
    const { result } = renderHook(() =>
      useGenerationSubmit<TestPayload, { id: string }>({
        submit: async () => {
          await gate;
          return { id: 'done' };
        },
        onSuccess,
      }),
    );

    let running!: Promise<unknown>;
    act(() => {
      running = result.current.submit({ prompt: '慢请求' });
    });
    await waitFor(() => expect(result.current.pending).toBe(true));
    await act(async () => {
      release();
      await running;
    });

    expect(result.current.pending).toBe(false);
    expect(onSuccess).toHaveBeenCalledWith({ id: 'done' });
  });
});
