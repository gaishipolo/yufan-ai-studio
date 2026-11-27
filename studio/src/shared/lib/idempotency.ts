/**
 * 创建类请求的幂等键。
 *
 * 键 = 一个「提交意图」随机串 + 当前 payload 的稳定指纹：
 * - 双击/超时重试：payload 未变 → 同一个键，后端只落一条；
 * - 用户改了表单：payload 变了 → 自动换键，不会撞「同键不同参数」；
 * - 成功后调用方换一个新的 intent，下一次提交就是新任务（可以并发）。
 *
 * 服务端仍会用完整 payload 自己算指纹；这里只是把「同一次提交」稳定地
 * 映射成一个符合契约形状的 key。
 */

const HEX = '0123456789abcdef';

export function newIdempotencyIntent(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) {
    return cryptoApi.randomUUID().replaceAll('-', '');
  }
  let out = '';
  for (let i = 0; i < 32; i += 1) {
    out += HEX[Math.floor(Math.random() * HEX.length)];
  }
  return out;
}

function stable(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .filter((key) => record[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${stable(record[key])}`)
    .join(',')}}`;
}

function fnv1a(text: string, seed: number): number {
  let hash = seed >>> 0;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash;
}

/** 49 字符：32 位 intent + `-` + 两个 8 位指纹，符合后端 8~64 位限制。 */
export function idempotencyKeyFor(intent: string, payload: object): string {
  const { idempotencyKey: _ignored, ...rest } = payload as Record<string, unknown>;
  const text = stable(rest);
  const left = fnv1a(text, 0x811c9dc5).toString(16).padStart(8, '0');
  const right = fnv1a(text, 0x9e3779b9).toString(16).padStart(8, '0');
  return `${intent}-${left}${right}`;
}