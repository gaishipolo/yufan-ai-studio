/**
 * 来源标识 → 展示名。
 *
 * 来源标识由后端定义（当前是 `video` / `image`，将来接上音频、数字人还会多），
 * 这里是它们的中文名。
 *
 * 认不出来的原样显示：后端加了新来源而前端还没跟上时，页面上会出现一个生硬的
 * 英文 id —— 这比「什么都不显示」更容易被发现。
 */
const SOURCE_LABELS: Record<string, string> = {
  video: '视频',
  image: '生图',
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

/**
 * 状态词 → 展示名。
 *
 * 后端各来源的状态词表不一样（视频是 `completed`，生图是 `succeeded`），
 * 界面上要统一成中文。
 */
const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '进行中',
  pending: '进行中',
  succeeded: '已产出',
  completed: '已产出',
  failed: '失败',
  error: '失败',
  cancelled: '已取消',
  canceled: '已取消',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status?.trim().toLowerCase()] ?? status;
}

/**
 * 状态对应的底色。取不到就是中性灰。
 *
 * 「进行中」用**青绿**而不是琥珀：青绿在这个系统里是「在飞」的信号色
 * （卡片上的进度条、生成中的角标都是它），琥珀留给「需要注意但没失败」。
 * 同一个状态在卡片上是青的、在表里是黄的，等于两个信号。
 */
export function statusTone(status: string): 'ok' | 'brand' | 'bad' | 'mute' {
  const normalized = status?.trim().toLowerCase();
  if (normalized === 'succeeded' || normalized === 'completed') return 'ok';
  if (normalized === 'failed' || normalized === 'error') return 'bad';
  if (normalized === 'queued' || normalized === 'running' || normalized === 'pending') return 'brand';
  return 'mute';
}
