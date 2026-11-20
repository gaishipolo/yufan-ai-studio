import { useMemo, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { EmptyState, ErrorState, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { usePermissionTree } from '@/entities/user/api/user.queries';
import type { PermissionTreeUserOut } from '@/shared/api/schema';
import { UserPermissionsPanel } from './UserPermissionsPanel';

/**
 * 权限总览：左边「分组 → 用户」，右边是这个人的权限。
 *
 * 为什么不是「先选组、再选人」两级列表：配权限时脑子里想的是**人**（「张三为什么
 * 能删任务」），而不是「他在哪个组」。树把归属也一起显示出来 —— 同一个人的来源里
 * 往往就有他所在的小组。
 */
export function PermissionsSection() {
  const tree = usePermissionTree();
  const [selected, setSelected] = useState<PermissionTreeUserOut | null>(null);
  const [keyword, setKeyword] = useState('');
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  const searching = keyword.trim().length > 0;

  const { groups, ungrouped } = useMemo(() => {
    const text = keyword.trim().toLowerCase();
    const matches = (user: PermissionTreeUserOut) =>
      !text ||
      user.username.toLowerCase().includes(text) ||
      (user.displayName ?? '').toLowerCase().includes(text);
    const all = tree.data?.groups ?? [];
    const ungroupedUsers = (tree.data?.ungrouped ?? []).filter(matches);
    if (!text) return { groups: all, ungrouped: ungroupedUsers };
    // 搜索时丢掉没有命中的组 —— 否则要在折叠的树里一格格翻
    return {
      groups: all
        .map((group) => ({ ...group, members: (group.members ?? []).filter(matches) }))
        .filter((group) => (group.members ?? []).length > 0),
      ungrouped: ungroupedUsers,
    };
  }, [tree.data, keyword]);
  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const total = (tree.data?.groups ?? []).reduce(
    (sum, group) => sum + (group.members ?? []).length,
    0,
  ) + (tree.data?.ungrouped ?? []).length;

  return (
    <div className="grid gap-3 md:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col rounded-xl border border-line bg-panel">
        <header className="flex items-baseline gap-2 border-b border-line px-3 py-2">
          <h2 className="text-body font-semibold text-ink">分组</h2>
          {/* 这个数字是**人数**不是组数：写「分组 20」会被读成二十个分组 */}
          <span className="text-micro text-ink-mute">共 {total} 人</span>
        </header>

        <div className="border-b border-line-soft p-2">
          <SearchInput
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索账号"
            aria-label="搜索账号"
            wrapperClassName="w-full"
          />
        </div>

        <div className="max-h-[calc(100vh-300px)] min-h-0 overflow-auto p-1.5">
          {tree.isError ? (
            <ErrorState
              error={tree.error}
              title="分组加载失败"
              onRetry={() => void tree.refetch()}
            />
          ) : tree.isLoading ? (
            <div className="space-y-1 p-1">
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="h-7 w-full" />
              ))}
            </div>
          ) : (
            <>
              {groups.map((group) => {
                const isOpen = searching ? true : !collapsed.has(group.id);
                return (
                  <div key={group.id}>
                    <button
                      type="button"
                      onClick={() => toggle(group.id)}
                      aria-expanded={isOpen}
                      className="flex w-full items-center gap-1 rounded-md px-1.5 py-1.5 text-left hover:bg-panel-soft"
                    >
                      <ChevronRight
                        size={12}
                        className={cn(
                          'shrink-0 text-ink-mute transition-transform',
                          isOpen && 'rotate-90',
                        )}
                      />
                      <span className="min-w-0 truncate text-xs font-medium text-ink-soft">
                        {group.name}
                      </span>
                      <span className="ml-auto shrink-0 tabular-nums text-micro text-ink-mute">
                        {(group.members ?? []).length}
                      </span>
                    </button>

                    {isOpen ? (
                      <ul className="mb-1 space-y-0.5">
                        {(group.members ?? []).map((user) => (
                          <li key={user.id}>
                            <UserNode
                              user={user}
                              selected={selected?.id === user.id}
                              onSelect={() => setSelected(user)}
                            />
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                );
              })}

              {ungrouped.length > 0 ? (
                <div className="mt-1 border-t border-line-soft pt-1">
                  <div className="flex items-center gap-1 px-1.5 py-1.5">
                    <span className="text-xs font-medium text-ink-mute">未分组</span>
                    <span className="ml-auto tabular-nums text-micro text-ink-mute">
                      {ungrouped.length}
                    </span>
                  </div>
                  <ul className="space-y-0.5">
                    {ungrouped.map((user) => (
                      <li key={user.id}>
                        <UserNode
                          user={user}
                          selected={selected?.id === user.id}
                          onSelect={() => setSelected(user)}
                        />
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {groups.length === 0 && ungrouped.length === 0 ? (
                <p className="px-2 py-6 text-center text-micro text-ink-mute">没有匹配的账号</p>
              ) : null}
            </>
          )}
        </div>
      </aside>

      <section className="min-w-0">
        {selected ? (
          <UserPermissionsPanel key={selected.id} userId={selected.id} fallback={selected} />
        ) : (
          <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-line bg-panel/60 px-6 py-16">
            <EmptyState
              title="从左边选一个账号"
              description="他的权限、每一条的来源，以及「只对他关闭」，都在这里。"
            />
          </div>
        )}
      </section>
    </div>
  );
}

function UserNode({
  user,
  selected,
  onSelect,
}: {
  user: PermissionTreeUserOut;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected || undefined}
      className={cn(
        'flex w-full items-center gap-1.5 rounded-md py-1 pl-6 pr-1.5 text-left transition-colors',
        selected ? 'bg-primary-soft text-primary' : 'text-ink-soft hover:bg-panel-soft',
        !user.isActive && 'opacity-55',
      )}
    >
      <span className="min-w-0 truncate text-xs">{user.displayName || user.username}</span>
      <span className="ml-auto shrink-0 font-mono text-micro text-ink-mute">
        {user.isActive ? user.username : '已停用'}
      </span>
    </button>
  );
}
