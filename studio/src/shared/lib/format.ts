/** 秒 → 「11s」 / 「1m20s」 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m${s}s` : `${m}m`;
}

/** 经过的秒数 → 「45 秒」 / 「1 分 20 秒」 / 「1 小时 2 分」
 *
 * 与 `formatDuration` 分开：那个是镜头时长，习惯用 `1m20s`；这里是任务耗时，
 * 读的人关心「多久」，中文单位比缩写少一次换算。
 */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total} 秒`;
  if (total < 3600) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s ? `${m} 分 ${s} 秒` : `${m} 分`;
  }
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

/** ISO → 「11-05 18:03」 */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** ISO → 「3 分钟前」 */
export function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return '—';
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  return `${Math.floor(hour / 24)} 天前`;
}

/** 字节 → 可读体积 */
export function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** 千分位 */
export function formatNumber(n: number): string {
  return n.toLocaleString('zh-CN');
}

/** 文件名里的时间戳：`2026-11-14-17-27-19`（浏览器本地时区）。 */
export function fileStamp(iso?: string | null): string {
  const d = iso ? new Date(iso) : new Date();
  const at = Number.isNaN(d.getTime()) ? new Date() : d;
  const pad = (n: number) => String(n).padStart(2, '0');
  return [
    at.getFullYear(),
    pad(at.getMonth() + 1),
    pad(at.getDate()),
    pad(at.getHours()),
    pad(at.getMinutes()),
    pad(at.getSeconds()),
  ].join('-');
}

/**
 * 生成产物的下载文件名：`用户名-2026-11-14-17-27-19.mp4`。
 *
 * 与后端入库命名同一条规则（`core/naming.py`）：标题是给人读的一句话，同一句话
 * 可以生成十次，下载目录里就分不清哪个是新的。名字里放作者与时间。
 *
 * 昵称里可能有空格或 `/`（Windows 不允许），统一剥掉；剥空了退回「用户」。
 */
export function generatedFileName(
  author: string,
  iso: string | null | undefined,
  suffix: string,
): string {
  const who = (author || '').trim().replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 32);
  return `${who || '用户'}-${fileStamp(iso)}${suffix}`;
}
