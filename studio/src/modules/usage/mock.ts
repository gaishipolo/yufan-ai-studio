import { db } from '@/mocks/db';
import type { MockContext, MockRoute } from '@/mocks/kernel';

/**
 * 用量看板的 mock。
 *
 * 口径与后端一致（`core/ports/usage_metrics.py` 的 `aggregate_rows`）：
 * - `total` 是尝试次数（所有行）；
 * - `quantity`（已产出数量）与 `durationSec`（已产出时长）**只计成功行**；
 * - 时间一律 UTC，桶边界按自然日 / 自然周（周一起点）。
 *
 * 数据源复用 `mocks/db.ts` 里的任务与生图作业，所以 mock 模式下看板和任务中心
 * 看到的是同一批记录 —— 改一条任务状态，两处一起变，不会出现「看板说 12 条、
 * 任务中心说 13 条」。
 */

/** mock 里「我」的账号 id（与 `mocks/db.ts` 里任务的作者一致）。 */
const ME = 'u_001';

type Bucket = 'succeeded' | 'failed' | 'pending';

const BUCKET_LABELS: Record<Bucket, string> = {
  succeeded: '已产出',
  failed: '失败',
  pending: '进行中',
};

interface MockRow {
  id: string;
  source: string;
  title: string;
  /** 原始状态词（视频 `completed`，生图 `succeeded`） */
  status: string;
  modelId: string;
  modelName: string;
  quantity: number;
  durationSec: number;
  createdAt: string;
  ownerId: string;
  ownerName: string;
  kind: string;
  detailUrl: string;
}

/** 状态词 → 三桶。与后端 `classify_status` 同义。 */
function classify(status: string): Bucket {
  const normalized = (status || '').trim().toLowerCase();
  if (['succeeded', 'completed', 'success', 'done'].includes(normalized)) return 'succeeded';
  if (['failed', 'error', 'cancelled', 'canceled'].includes(normalized)) return 'failed';
  return 'pending';
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

/** 视频 + 生图两路记录。 */
function buildRows(): MockRow[] {
  const video: MockRow[] = db.tasks.map((task) => ({
    id: task.id,
    source: 'video',
    title: task.title,
    status: task.status,
    modelId: slug(task.model),
    modelName: task.model,
    quantity: 1,
    durationSec: task.durationSec,
    createdAt: task.createdAt,
    ownerId: task.authorId,
    ownerName: task.authorName,
    // mock 的任务没有参考图，一律算文生视频
    kind: '文生视频',
    detailUrl: `/tasks?source=video&keyword=${task.id}`,
  }));

  const image: MockRow[] = db.imageJobs.map((job) => ({
    id: job.id,
    source: 'image',
    title: job.name,
    status: job.status,
    modelId: job.modelId ?? '',
    modelName: job.model ?? '',
    quantity: job.count ?? 1,
    durationSec: 0,
    createdAt: job.createdAt,
    ownerId: ME,
    ownerName: '',
    kind: '生图',
    detailUrl: `/image?job=${job.id}`,
  }));

  return [...video, ...image];
}

/** UTC 日期串。 */
function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function shiftDays(day: string, delta: number): string {
  const moment = new Date(`${day}T00:00:00Z`);
  return new Date(moment.getTime() + delta * 86_400_000).toISOString().slice(0, 10);
}

interface Range {
  start: string;
  end: string;
}

function resolveRange(ctx: MockContext): Range {
  const end = String(ctx.params.end ?? today()).slice(0, 10);
  const start = String(ctx.params.start ?? shiftDays(end, -29)).slice(0, 10);
  return { start, end };
}

/** 区间 + 数据范围过滤后的行。 */
function selectRows(ctx: MockContext, range: Range): MockRow[] {
  const scope = ctx.params.scope === 'all' ? 'all' : 'mine';
  return buildRows().filter((row) => {
    const key = dayKey(row.createdAt);
    if (key < range.start || key > range.end) return false;
    if (scope === 'mine' && row.ownerId !== ME) return false;
    return true;
  });
}

/** 区间内应有的全部桶（空的天也要出现，否则折线会「跳过」那天）。 */
function bucketKeys(range: Range, granularity: string): string[] {
  const keys: string[] = [];
  let cursor = range.start;
  if (granularity === 'week') {
    // 对齐到周一（ISO 周起点），与后端 `bucket_key` 一致
    const weekday = new Date(`${cursor}T00:00:00Z`).getUTCDay();
    cursor = shiftDays(cursor, weekday === 0 ? -6 : 1 - weekday);
  }
  const step = granularity === 'week' ? 7 : 1;
  while (cursor <= range.end) {
    keys.push(cursor);
    cursor = shiftDays(cursor, step);
  }
  return keys;
}

/** 计数块：来源汇总与趋势点共用同一组字段。 */
interface Counts {
  total: number;
  succeeded: number;
  failed: number;
  pending: number;
  quantity: number;
  durationSec: number;
}

function emptyCounts(): Counts {
  return { total: 0, succeeded: 0, failed: 0, pending: 0, quantity: 0, durationSec: 0 };
}

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/usage\/overview$/,
    handler: (ctx: MockContext) => {
      const range = resolveRange(ctx);
      const granularity = ctx.params.granularity === 'week' ? 'week' : 'day';
      const rows = selectRows(ctx, range);

      /* ---- 汇总 ---- */
      let total = 0;
      let succeeded = 0;
      let failed = 0;
      let pending = 0;
      let quantity = 0;
      let durationSec = 0;

      const bySource = new Map<string, Counts>();
      const trend = new Map<string, Counts>();
      const breakdown = new Map<string, { key: string; label: string; total: number; quantity: number; durationSec: number }>();

      const bump = (
        dimension: string,
        key: string,
        label: string,
        bucket: Bucket,
        row: MockRow,
      ) => {
        const slotKey = `${dimension}::${key}`;
        let item = breakdown.get(slotKey);
        if (!item) {
          item = { key, label, total: 0, quantity: 0, durationSec: 0 };
          breakdown.set(slotKey, item);
        }
        item.total += 1;
        if (bucket === 'succeeded') {
          item.quantity += row.quantity;
          item.durationSec += row.durationSec;
        }
      };

      rows.forEach((row) => {
        const bucket = classify(row.status);
        total += 1;
        if (bucket === 'succeeded') {
          succeeded += 1;
          quantity += row.quantity;
          durationSec += row.durationSec;
        } else if (bucket === 'failed') {
          failed += 1;
        } else {
          pending += 1;
        }

        const sourceKey = row.source;
        const sourcePoint = bySource.get(sourceKey) ?? emptyCounts();
        sourcePoint.total += 1;
        sourcePoint[bucket] += 1;
        if (bucket === 'succeeded') {
          sourcePoint.quantity += row.quantity;
          sourcePoint.durationSec += row.durationSec;
        }
        bySource.set(sourceKey, sourcePoint);

        const bucketDay =
          granularity === 'week'
            ? (() => {
                const weekday = new Date(row.createdAt).getUTCDay();
                return shiftDays(dayKey(row.createdAt), weekday === 0 ? -6 : 1 - weekday);
              })()
            : dayKey(row.createdAt);
        const point = trend.get(bucketDay) ?? emptyCounts();
        point.total += 1;
        point[bucket] += 1;
        if (bucket === 'succeeded') {
          point.quantity += row.quantity;
          point.durationSec += row.durationSec;
        }
        trend.set(bucketDay, point);

        const modelKey = row.modelId || row.modelName || 'unknown';
        bump('model', modelKey, row.modelName || row.modelId || '未知模型', bucket, row);
        bump('kind', row.kind || 'unknown', row.kind || '未知', bucket, row);
        bump('owner', row.ownerId || 'unknown', row.ownerName || row.ownerId || '未知', bucket, row);
        bump('status', bucket, BUCKET_LABELS[bucket], bucket, row);
      });

      const trendPoints = bucketKeys(range, granularity).map((key) => ({
        bucket: key,
        ...(trend.get(key) ?? emptyCounts()),
      }));

      const breakdownRows = [...breakdown.entries()]
        .map(([slotKey, item]) => {
          const dimension = slotKey.split('::')[0];
          return {
            dimension,
            key: item.key,
            label: item.label,
            total: item.total,
            quantity: item.quantity,
            durationSec: item.durationSec,
            share: total ? item.total / total : 0,
          };
        })
        .sort((a, b) => a.dimension.localeCompare(b.dimension) || b.total - a.total);

      return {
        summary: {
          start: `${range.start}T00:00:00Z`,
          end: `${range.end}T23:59:59Z`,
          granularity,
          scope: ctx.params.scope === 'all' ? 'all' : 'mine',
          total,
          succeeded,
          failed,
          pending,
          successRate: total ? succeeded / total : 0,
          quantity,
          durationSec,
          avgDurationSec: succeeded ? durationSec / succeeded : 0,
          sources: [...bySource.entries()].map(([source, point]) => ({
            source,
            total: point.total,
            succeeded: point.succeeded,
            failed: point.failed,
            pending: point.pending,
            quantity: point.quantity,
            durationSec: point.durationSec,
          })),
        },
        trend: trendPoints,
        breakdown: breakdownRows,
        templateUsers: [],
        sources: [...bySource.keys()],
      };
    },
  },
  {
    method: 'GET',
    pattern: /^\/usage\/records$/,
    handler: (ctx: MockContext) => {
      const range = resolveRange(ctx);
      const rows = selectRows(ctx, range).sort((a, b) =>
        a.createdAt === b.createdAt ? b.id.localeCompare(a.id) : a.createdAt < b.createdAt ? 1 : -1,
      );

      const page = Math.max(1, Number(ctx.params.page) || 1);
      const pageSize = Math.max(1, Math.min(100, Number(ctx.params.pageSize) || 24));
      const window = rows.slice((page - 1) * pageSize, page * pageSize);

      return {
        items: window.map((row) => ({
          id: row.id,
          source: row.source,
          title: row.title,
          status: row.status,
          modelId: row.modelId,
          modelName: row.modelName,
          quantity: row.quantity,
          durationSec: row.durationSec,
          createdAt: row.createdAt,
          ownerId: row.ownerId,
          ownerName: row.ownerName,
          detailUrl: row.detailUrl,
        })),
        total: rows.length,
        page,
        pageSize,
      };
    },
  },
];
