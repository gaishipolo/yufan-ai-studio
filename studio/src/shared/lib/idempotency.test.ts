import {describe, expect, it} from 'vitest';
import {idempotencyKeyFor, newIdempotencyIntent} from './idempotency';

describe('请求幂等键', () => {
  it('payload 相同（键顺序不同）得到同一个键', () => {
    const intent = newIdempotencyIntent();
    expect(idempotencyKeyFor(intent, {prompt: '猫', params: {seed: 1, quality: 'x'}})).toBe(
      idempotencyKeyFor(intent, {params: {quality: 'x', seed: 1}, prompt: '猫'}),
    );
  });

  it('payload 变了就换键，避免“同键不同参数”', () => {
    const intent = newIdempotencyIntent();
    expect(idempotencyKeyFor(intent, {prompt: '猫'})).not.toBe(
      idempotencyKeyFor(intent, {prompt: '狗'}),
    );
  });

  it('键符合后端 8~64 位与字符集约束', () => {
    const key = idempotencyKeyFor(newIdempotencyIntent(), {prompt: '猫'});
    expect(key).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });
});