import { useMemo } from 'react';
import type { UsageTemplateUserOut } from '../model/types';
import { cn } from '@/shared/lib/cn';

const TOP = 10;

interface Axis {
  key: string;
  label: string;
  total: number;
}

function topAxis(
  items: UsageTemplateUserOut[],
  keyOf: (item: UsageTemplateUserOut) => string,
  labelOf: (item: UsageTemplateUserOut) => string,
): Axis[] {
  const totals = new Map<string, Axis>();
  for (const item of items) {
    const key = keyOf(item);
    if (!key) continue;
    const current = totals.get(key) ?? { key, label: labelOf(item) || key, total: 0 };
    current.total += item.total;
    if (!current.label || current.label === key) {
      current.label = labelOf(item) || current.label;
    }
    totals.set(key, current);
  }
  return [...totals.values()].sort((a, b) => b.total - a.total).slice(0, TOP);
}

function cellKey(templateKey: string, ownerId: string): string {
  return `${templateKey}\u0000${ownerId}`;
}

/**
 * 模板 × 使用人热力表。
 *
 * 行 / 列都只取 Top 10：这是“关系”视图，不是全量报表；超过 10 个只会把
 * 格子压成看不清颜色的小方块。完整数字仍可在“按模板 / 按使用人”两个构成里看。
 */
export function TemplateUserMatrix({ items }: { items: UsageTemplateUserOut[] }) {
  const { templates, users, cells, max } = useMemo(() => {
    const templates = topAxis(items, item => item.templateKey, item => item.templateName);
    const users = topAxis(items, item => item.ownerId, item => item.ownerName);
    const cells = new Map<string, UsageTemplateUserOut>();
    let max = 0;
    for (const item of items) {
      cells.set(cellKey(item.templateKey, item.ownerId), item);
      max = Math.max(max, item.total);
    }
    return { templates, users, cells, max };
  }, [items]);

  if (!items.length || !templates.length || !users.length) {
    return <p className="py-6 text-center text-meta text-ink-mute">该区间没有模板使用记录</p>;
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="min-w-[560px] w-full border-separate border-spacing-1 text-meta">
          <caption className="sr-only">模板与使用人的使用次数</caption>
          <thead>
            <tr>
              <th className="sticky left-0 z-10 min-w-[8rem] bg-panel px-2 py-1.5 text-left font-medium text-ink-mute">
                模板 \ 使用人
              </th>
              {users.map(user => (
                <th
                  key={user.key}
                  className="min-w-[5rem] px-2 py-1.5 text-center font-medium text-ink-mute"
                  title={user.label}
                >
                  <span className="block max-w-[6rem] truncate">{user.label || user.key}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {templates.map(template => (
              <tr key={template.key}>
                <th
                  className="sticky left-0 z-10 min-w-[8rem] max-w-[11rem] truncate bg-panel px-2 py-1.5 text-left font-normal text-ink"
                  title={template.label || template.key}
                >
                  {template.label || template.key}
                </th>
                {users.map(user => {
                  const cell = cells.get(cellKey(template.key, user.key));
                  const value = cell?.total ?? 0;
                  const level = max > 0 && value > 0 ? Math.ceil((value / max) * 4) : 0;
                  const tone = [
                    'bg-panel-soft',
                    'bg-primary-soft',
                    'bg-primary/15',
                    'bg-primary/30',
                    'bg-primary/45',
                  ][level];
                  return (
                    <td
                      key={user.key}
                      className={cn(
                        'rounded-md px-2 py-1.5 text-center font-mono tabular-nums',
                        tone,
                        value ? 'text-ink' : 'text-ink-mute/50',
                      )}
                      title={
                        cell
                          ? `${template.label || template.key} · ${user.label || user.key}：${cell.total} 次（成功 ${cell.succeeded}）`
                          : '没有使用记录'
                      }
                    >
                      {value || '·'}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-micro text-ink-mute">
        颜色越深使用越多；只列使用次数最高的 10 个模板与 10 位使用人。
      </p>
    </div>
  );
}