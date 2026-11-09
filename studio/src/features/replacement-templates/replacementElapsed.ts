import type { ReplacementRunOut } from '@/shared/api/schema';
import { formatElapsed } from '@/shared/lib/format';

type Timing = Pick<ReplacementRunOut, 'status' | 'startedAt' | 'finishedAt' | 'elapsedSec'>;

/** 还没进入提交环节的状态：说「尚未开始」比「未记录」准确。 */
const NOT_STARTED = new Set(['preparing', 'prepared', 'prepare_failed']);

/**
 * 一条替换任务的耗时文案。
 *
 * 三种情况必须分开说，否则用户会以为是同一个意思：
 * - 没提交 → 尚未开始；
 * - 提交了但没时间戳（上线前的档案、恢复路径没补上）→ 未记录；
 * - 在途 → 已用 X（刷新会变，不能写死成最终值）。
 */
export function replacementElapsedText(run: Timing): string {
  if (run.elapsedSec == null || run.elapsedSec < 0) {
    return NOT_STARTED.has(run.status) ? '尚未开始' : '未记录';
  }
  const value = formatElapsed(run.elapsedSec);
  return run.finishedAt ? value : `已用 ${value}`;
}
