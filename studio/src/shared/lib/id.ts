/**
 * 浏览器端的 UUID v4。
 *
 * 不要把 `crypto.randomUUID()` 直接写在业务里：它只在**安全上下文**（HTTPS 或 localhost）
 * 才存在。局域网用 `http://192.168.x.x` 打开时它是 undefined，一调就抛
 * `crypto.randomUUID is not a function`，整个操作链断在生成标识这一步。
 * `crypto.getRandomValues` 没有这个限制，用它自己拼一个 v4 即可。
 */

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));

/** 同一条警告只打一次：批量上传一次会调很多次，刷屏反而没人看。 */
let warned = false;
function warnOnce(message: string): void {
  if (warned) return;
  warned = true;
  console.warn(`[id] ${message}`);
}

export function createId(): string {
  const api = globalThis.crypto;
  if (typeof api?.randomUUID === 'function') {
    try {
      return api.randomUUID();
    } catch {
      /* 少数浏览器实现了接口，但在非安全上下文里调用仍会抛错，落到下面。 */
    }
  }
  if (typeof api?.getRandomValues === 'function') {
    const bytes = api.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; /* version 4 */
    bytes[8] = (bytes[8] & 0x3f) | 0x80; /* variant 10xx */
    let out = '';
    for (let i = 0; i < 16; i += 1) {
      if (i === 4 || i === 6 || i === 8 || i === 10) out += '-';
      out += HEX[bytes[i]];
    }
    return out;
  }
  /* 老浏览器兜底：只要不撞车就够用，这个值不会被当作安全凭据。
     但它会作为后端的幂等键（`requestId`），撞一次就把两条不同配置判成同一个请求，
     所以这种降级只该出现在「连 getRandomValues 都没有」的老环境里——留一条警告，
     让问题在控制台可见，而不是静默用弱随机数跑完全流程。 */
  warnOnce('当前环境没有 crypto.getRandomValues，标识改用弱随机数生成，请升级浏览器');
  const chunk = (len: number) =>
    Array.from({ length: len }, () => Math.floor(Math.random() * 16).toString(16)).join('');
  return `${chunk(8)}-${chunk(4)}-4${chunk(3)}-a${chunk(3)}-${chunk(12)}`;
}
