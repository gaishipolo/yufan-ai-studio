/**
 * 可复现的随机源。
 *
 * ## 为什么不用 `Math.random` 直接写在计算里
 *
 * 模板库「随机」档要抽一段随机文案。界面是渲染出来的，`Math.random()`
 * 写在渲染路径里，每次重渲染都会重新抽一遍 —— 用户看到文案在两次点击
 * 之间跳变，像是页面在抖。种子把「这一批随机结果」固定下来：种子不变 →
 * 结果不变；换种子（用户主动再生成）→ 换一段。
 *
 * ## 为什么自己写而不用 `Math.sin` 之类的老写法
 *
 * `Math.sin(seed)` 那种伪随机的分布很差（小样本下明显聚集），而这里要的
 * 就是「随便抽一段」—— 分布歪会被当成「随机不准」。mulberry32 32 位状态、
 * 分布均匀，几行就够。
 */
export function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
