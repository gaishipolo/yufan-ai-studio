import { useMemo, useState } from 'react';
import { cn } from '@/shared/lib/cn';
import { Button, EmptyState, SearchInput, Segmented } from '@/shared/ui';
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
        'grid grid-cols-[minmax(11rem,26rem)_minmax(0,1fr)_7.5rem] items-center gap-3 border-b border-line-soft px-3 py-2 transition-colors last:border-b-0',
        // 行本身要像一个「条目」：悬停给底色，否则一屏全是白条，眼睛没有落点
        'hover:bg-panel-soft/70',
        !pageOpen && 'opacity-55',
      )}
    >
      <div className="min-w-0">
        <div
          className={cn(
            'truncate text-xs font-medium',
            closed ? 'text-ink-mute line-through' : 'text-ink',
          )}
        >
          {row.name}
        </div>
        <div
          className={cn(
            'truncate font-mono text-micro tracking-[.01em] text-ink-mute',
            closed && 'line-through',
          )}
        >
          {row.code}
        </div>
      </div>

      <div className="min-w-0">
        {closed ? (
          <span className="rounded-full border border-line bg-panel-soft px-2 py-[1px] text-micro text-ink-soft">
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

      <div className="flex justify-end">
        {/* 没有操作权时**不摆灰按钮**：灰按钮会让人以为「差点就能点」，
            而真正的原因（缺权限）在按钮上是看不出来的 */}
        {action === 'none' || readOnly ? (
          <span
            className="text-micro text-ink-mute"
            title={action === 'none' ? '这一页没开，先开页面' : undefined}
          >
            —
          </span>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            /* 紧凑一档 + **边框加深**：`border-line` 在满屏白条上几乎融进去，
               看起来还是像一段文字；`line-strong` 才让它是个可点的对象 */
            className="h-6 border-line-strong px-2 text-micro text-ink hover:bg-panel-soft"
            loading={pending}
            onClick={trigger}
          >
            {ACTION_LABEL[action]}
          </Button>
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
          'flex flex-wrap items-center gap-x-2 gap-y-1 border-b px-3 py-2',
          group.open
            ? 'border-line bg-line-soft'
            : 'border-danger-line bg-danger-soft',
        )}
      >
        <h3 className={cn('text-body font-semibold', group.open ? 'text-ink' : 'text-danger')}>
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
        <Segmented
          aria-label="筛选"
          options={FILTERS}
          value={filter}
          onChange={setFilter}
          className="w-auto shrink-0 whitespace-nowrap"
        />
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
