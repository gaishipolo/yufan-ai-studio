/**
 * 跨命名空间共享的缓存前缀。
 *
 * 生图与任务中心读的是**同一批生成记录**：`/images` 与 `/tasks/feed` 会把同一条
 * 记录各投影一次。两边的键各自在 `entities/<模块>/api` 下的 queries 里定义，谁也不知道
 * 对方的前缀，于是「一边写入、另一边不失效」：
 *
 * - 生图工作台提交成功 → 任务中心那一屏还是旧的（要手动刷新才出现）；
 * - 在任务中心删掉生图记录 → 生图历史页仍列着它，点重试 404。
 *
 * 这里只放**前缀**，具体的列表键/详情键仍归各模块（它们带参数，只有自己知道形状）。
 * 失效时两边都带着前缀失效一次即可，见 `useGenerateImage` / `useDeleteImages`。
 */
export const CACHE_PREFIX = {
  tasks: ['tasks'] as const,
  images: ['images'] as const,
} as const;
