/**
 * 随机档：从候选池里挑素材。
 *
 * ## 为什么要「洗牌」而不是「每次都取前 N 个」
 *
 * 直接取前 N 个看起来像随机，实际上是**固定的那 N 个** —— 同一个模板
 * 连点十次随机，选中的素材一模一样，用户会立刻发现这个「随机」是假的。
 * 所以这里做一次**洗牌**（Fisher–Yates），再取前 N 个。
 *
 * ## 为什么洗牌要接受一个 `rand`
 *
 * 洗牌必须**可重现**：测试里要能断言「挑到哪几张」，线上出问题时也要能
 * 复现同一批选择。所以随机源从外面传进来（默认用 `Math.random`），
 * 而不是藏在函数里 —— 藏在里面的随机没法测、也没法复现。
 *
 * ## 挑不出 N 张时怎么办
 *
 * 候选池不够就**有多少拿多少**，不报错、不补位。槽位的 `minCount` 是 0
 * （可选槽位），少挑几张完全合法 —— 硬凑反而会挑进不该进这个槽位的素材。
 */

/**
 * 从 `pool` 里挑最多 `count` 张，返回**不重复**的 id 列表。
 *
 * @param pool 候选素材 id
 * @param count 要挑几张
 * @param rand 随机源（0~1 的小数）。测试传入固定序列以获得确定结果。
 */
export function randomPick(
  pool: string[],
  count: number,
  rand: () => number = Math.random,
): string[] {
  if (pool.length === 0 || count <= 0) return [];
  /* 洗牌：Fisher–Yates。倒着遍历，把每个元素换到它该在的位置上。
     用 `Math.floor(rand() * (i + 1))` 取下标 —— rand 返 1 时会越界，
     所以夹到 [0, 1) 再乘。 */
  const shuffled = [...pool];
  for (let i = shuffled.length - 1; i > 0; i -= 1) {
    const r = Math.min(Math.max(rand(), 0), 0.999999999);
    const j = Math.floor(r * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, Math.min(count, shuffled.length));
}

/**
 * 由一颗种子造一个**可复现**的随机源（mulberry32）。
 *
 * ## 为什么不用 `Math.random` 直接当随机源
 *
 * 界面上的素材是**渲染出来的**，`Math.random()` 写在渲染路径里，
 * 每次重渲染都会挑一遍不同的结果 —— 使用者看到素材在两次点击之间
 * 换来换去，像是页面在抖。种子把「这一批随机结果」固定下来：
 * 种子不变 → 结果不变；换种子（重新随机）→ 换一批。
 *
 * ## 为什么自己写而不用 `Math.sin` 之类的老写法
 *
 * `Math.sin(seed)` 那种伪随机的分布很差（小样本下明显聚集），
 * 而这里要的就是「随便挑几张看看效果」—— 分布歪一点会被使用者
 * 当成「随机不准」。mulberry32 32 位状态、分布均匀，几行就够。
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
