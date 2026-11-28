import { useCallback, useRef, useState } from 'react';
import { idempotencyKeyFor, newIdempotencyIntent } from '@/shared/lib/idempotency';

/**
 * 一次生成提交的会话状态：幂等意图、pending、错误与成功回调。
 *
 * 幂等意图必须和 mutation 生命周期绑在一起：双击/超时重试复用同一个键，
 * 只有真正成功后才换新的；失败保留，下一次重试仍是同一提交意图。
 */
export interface GenerationSubmitOptions<T extends { idempotencyKey?: string | null }, R> {
  submit: (payload: T) => Promise<R>;
  onSuccess?: (result: R) => void | Promise<void>;
  onError?: (message: string) => void;
}

export function useGenerationSubmit<
  T extends { idempotencyKey?: string | null },
  R,
>({ submit, onSuccess, onError }: GenerationSubmitOptions<T, R>) {
  const intent = useRef(newIdempotencyIntent());
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  const run = useCallback(
    async (payload: T): Promise<R | undefined> => {
      setPending(true);
      setError('');
      try {
        const keyed = {
          ...payload,
          idempotencyKey: idempotencyKeyFor(intent.current, payload),
        } as T;
        const result = await submit(keyed);
        intent.current = newIdempotencyIntent();
        await onSuccess?.(result);
        return result;
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(message);
        onError?.(message);
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [submit, onSuccess, onError],
  );

  return { submit: run, pending, error };
}
