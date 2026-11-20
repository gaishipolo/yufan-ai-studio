import { useMemo, useState } from 'react';
import { cn } from '@/shared/lib/cn';
import { EmptyState, SearchInput } from '@/shared/ui';
import { SourceChips } from './SourceChips';
import type { PermissionGroup, PermissionOverview, PermissionRow } from './use-overview';

type Filter = 'all' | 'denied' | 'gated';

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'denied', label: '仅例外' },
  { value: 'gated', label: '被页面关掉' },
];

export interface PermissionOverviewProps {
  overview: PermissionOverview;
  /** 正在写的码：行内转圈，不让用户以为没点上 */
  pendingCode?: string | null;
  /** 当前是谁在看（超管整页只读，理由在页头那条） */
  readOnly?: boolean;
  onDeny: (row: PermissionRow) => void;
  onAllow: (row: PermissionRow) => void;
  onRestore: (row: PermissionRow) => void;
}

function actionOf(row: PermissionRow): 'restore' | 'deny' | 'remove' | 'none' {
  if (row.status === 'denied') return 'restore';
  if (row.status === 'gated') return 'none';
  // 来源只有个人 = 这一项本来就是「只给他一个人的」，直接移除即可，
  // 不需要留一条否决（那会成为一条谁也不解释的例外）
  const onlyPersonal =
    row.origins.length > 0 && row.origins.every((origin) => origin.kind === 'user');
  return onlyPersonal ? 'remove' : 'deny';
}

const ACTION_LABEL = {
  restore: '恢复',
  deny: '仅对此人关闭',
  remove: '移除',
} as const;

function Row({
  row,
  readOnly,
  pending,
  pageOpen,
  onDeny,
  onAllow,
  onRestore,
}: {
  row: PermissionRow;
  readOnly: boolean;
  pending: boolean;
  pageOpen: boolean;
  onDeny: (row: PermissionRow) => void;
  onAllow: (row: PermissionRow) => void;
  onRestore: (row: PermissionRow) => void;
}) {
  const action = pageOpen ? actionOf(row) : 'none';
  const closed = row.status === 'denied';

  const trigger = () => {
    if (action === 'restore') onRestore(row);
    else if (action === 'remove') onAllow(row);
    else if (action === 'deny') onDeny(row);
  };

  return (
    <div
      data-permission-row={row.code}
      data-status={row.status}
      className={cn(
        'grid grid-cols-[minmax(0,1fr)_10.5rem_6rem] items-start gap-2.5 border-b border-line-soft px-3 py-1.5 last:border-b-0',
        !pageOpen && 'opacity-55',
      )}
    >
      <div className="min-w-0">
        <div className={cn('truncate text-xs', closed ? 'text-ink-mute line-through' : 'text-ink')}>
          {row.name}
        </div>
        <div
          className={cn(
            'truncate font-mono text-micro text-ink-mute',
            closed && 'line-through',
          )}
        >
          {row.code}
        </div>
      </div>

      <div className="min-w-0 pt-[2px]">
        {closed ? (
          <span className="rounded-full border border-dashed border-line-strong px-1.5 py-[1px] text-micro text-ink-mute">
            已对他关闭
          </span>
        ) : (
          <SourceChips origins={row.origins} />
        )}
        {row.shared && pageOpen && !closed ? (
          <span
            className="mt-0.5 block text-micro text-ink-mute"
            title="这个接口同时归多个页面，关掉其中一页它仍然有效"
          >
            多页共用
          </span>
        ) : null}
      </div>

      <div className="flex justify-end pt-[1px]">
        {action === 'none' ? (
          <span className="text-micro text-ink-mute" title="这一页没开，先开页面">
            —
          </span>
        ) : (
          <button
            type="button"
            disabled={readOnly || pending}
            onClick={trigger}
            className={cn(
              'rounded-md border border-transparent px-1.5 py-[2px] text-micro text-ink-soft',
              'hover:border-line hover:bg-panel-soft hover:text-ink',
              (readOnly || pending) && 'cursor-not-allowed opacity-40 hover:bg-transparent',
            )}
          >
            {pending ? '处理中…' : ACTION_LABEL[action]}
          </button>
        )}
      </div>
    </div>
  );
}

function GroupBlock({
  group,
  readOnly,
  pendingCode,
  onDeny,
  onAllow,
  onRestore,
}: {
  group: PermissionGroup;
  readOnly: boolean;
  pendingCode: string | null;
  onDeny: (row: PermissionRow) => void;
  onAllow: (row: PermissionRow) => void;
  onRestore: (row: PermissionRow) => void;
}) {
  return (
    <section data-page-group={group.code} className="overflow-hidden rounded-lg border border-line-soft">
      <header
        className={cn(
          'flex flex-wrap items-center gap-x-2 gap-y-1 border-b px-3 py-1.5',
          group.open
            ? 'border-line bg-line-soft'
            : 'border-danger-line bg-danger-soft',
        )}
      >
        <h3 className={cn('text-xs font-semibold', group.open ? 'text-ink' : 'text-danger')}>
          {group.name}
        </h3>
        <span className="font-mono text-micro text-ink-mute">{group.route}</span>
        <span className="ml-auto flex items-center gap-1.5">
          {group.open ? (
            <SourceChips origins={group.origins} />
          ) : (
            <span className="text-micro text-danger">页面没开</span>
          )}
        </span>
      </header>
      <div>
        {group.rows.map((row) => (
          <Row
            key={row.code}
            row={row}
            readOnly={readOnly}
            pending={pendingCode === row.code}
            pageOpen={group.open}
            onDeny={onDeny}
            onAllow={onAllow}
            onRestore={onRestore}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * 权限总览：按页面分组，每行一个权限码 + 它的来源 + 一个动作。
 *
 * 为什么不是「按层列四段」：那种布局要回答「他为什么能删任务」得在四个列表里
 * 对一遍；按码列，问题在那一行上就答完了。
 */
export function PermissionOverview({
  overview,
  pendingCode = null,
  readOnly = false,
  onDeny,
  onAllow,
  onRestore,
}: PermissionOverviewProps) {
  const [keyword, setKeyword] = useState('');
  const [filter, setFilter] = useState<Filter>('all');

  // 筛选与搜索一次算完：拆成三份会让「匹配规则」散落在三个地方，
  // 而它们的语义必须完全一致（同一行不能在一处被筛掉、另一处留着）
  const { groups, background, exceptions } = useMemo(() => {
    const text = keyword.trim().toLowerCase();
    const matches = (row: PermissionRow) => {
      if (filter === 'denied' && row.status !== 'denied') return false;
      if (filter === 'gated' && row.status !== 'gated') return false;
      if (!text) return true;
      return row.name.toLowerCase().includes(text) || row.code.toLowerCase().includes(text);
    };
    return {
      groups: overview.groups
        .map((group) => ({ ...group, rows: group.rows.filter(matches) }))
        .filter((group) => group.rows.length > 0),
      background: overview.background.filter(matches),
      exceptions: overview.exceptions.filter(matches),
    };
  }, [overview, filter, keyword]);

  const empty = groups.length === 0 && background.length === 0 && exceptions.length === 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索权限名或权限码"
          aria-label="搜索权限"
          wrapperClassName="w-full md:w-64"
        />
        <div className="flex gap-1" role="group" aria-label="筛选">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={filter === item.value}
              onClick={() => setFilter(item.value)}
              className={cn(
                'rounded-md px-2 py-1 text-micro',
                filter === item.value
                  ? 'bg-panel-soft font-medium text-ink'
                  : 'text-ink-soft hover:bg-panel-soft',
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {empty ? (
        <EmptyState title="没有匹配的权限点" description="换个词，或把筛选切回「全部」。" />
      ) : null}

      {groups.map((group) => (
        <GroupBlock
          key={group.code}
          group={group}
          readOnly={readOnly}
          pendingCode={pendingCode}
          onDeny={onDeny}
          onAllow={onAllow}
          onRestore={onRestore}
        />
      ))}

      {background.length > 0 ? (
        <section className="overflow-hidden rounded-lg border border-line-soft">
          <header className="flex items-center gap-2 border-b border-line bg-line-soft px-3 py-1.5">
            <h3 className="text-xs font-semibold text-ink">后台能力</h3>
            <span className="text-micro text-ink-mute">不受页面开关影响</span>
          </header>
          {background.map((row) => (
            <Row
              key={row.code}
              row={row}
              readOnly={readOnly}
              pending={pendingCode === row.code}
              pageOpen
              onDeny={onDeny}
              onAllow={onAllow}
              onRestore={onRestore}
            />
          ))}
        </section>
      ) : null}

      {exceptions.length > 0 ? (
        <section data-exceptions className="overflow-hidden rounded-lg border border-line-soft">
          <header className="flex items-center gap-2 border-b border-line bg-line-soft px-3 py-1.5">
            <h3 className="text-xs font-semibold text-ink">例外</h3>
            <span className="tabular-nums text-micro text-ink-mute">{exceptions.length}</span>
          </header>
          {exceptions.map((row) => (
            <Row
              key={row.code}
              row={row}
              readOnly={readOnly}
              pending={pendingCode === row.code}
              pageOpen
              onDeny={onDeny}
              onAllow={onAllow}
              onRestore={onRestore}
            />
          ))}
        </section>
      ) : null}

      {overview.orphans.length > 0 ? (
        <section className="overflow-hidden rounded-lg border border-line-soft">
          <header className="flex items-center gap-2 border-b border-line bg-line-soft px-3 py-1.5">
            <h3 className="text-xs font-semibold text-ink">目录外</h3>
            <span className="text-micro text-ink-mute">目录里已没有这个码</span>
          </header>
          {overview.orphans.map((row) => (
            <Row
              key={row.code}
              row={row}
              readOnly={readOnly}
              pending={pendingCode === row.code}
              pageOpen
              onDeny={onDeny}
              onAllow={onAllow}
              onRestore={onRestore}
            />
          ))}
        </section>
      ) : null}
    </div>
  );
}
