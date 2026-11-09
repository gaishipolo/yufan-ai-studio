import { useMemo } from 'react';
import { UsersRound } from 'lucide-react';
import { Button, Chip, Skeleton } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { useTeamPermissions } from '@/entities/group/api/group.queries';

/**
 * 「角色与权限」Tab 顶部的团队基础权限卡片。
 *
 * 放在这里而不是「用户」Tab：它是**全站授权口径**，与角色同属「权限从哪来」，
 * 和某个具体用户的表格混在一起会让人误以为只作用于当前筛选出来的那几个人。
 */
export function TeamBasePermissionsCard({ onEdit }: { onEdit: () => void }) {
  const query = useTeamPermissions();
  const catalogQuery = usePermissionCatalog();

  const labelOf = useMemo(() => {
    const names = new Map<string, string>();
    for (const group of catalogQuery.data?.groups ?? []) {
      for (const item of group.items) names.set(item.code, item.name);
    }
    return (code: string) => names.get(code) ?? code;
  }, [catalogQuery.data]);

  const codes = query.data ?? [];

  return (
    <section className="mb-3 rounded-card border border-line-soft bg-panel p-3.5">
      <header className="flex flex-wrap items-center gap-2">
        <UsersRound size={14} className="text-primary" />
        <h3 className="text-[13px] font-semibold text-ink">团队基础权限</h3>
        <span className="text-[11px] text-ink-mute">所有启用账号自动获得</span>
        <Can permission="identity:team:manage">
          <Button size="sm" className="ml-auto" onClick={onEdit}>
            编辑
          </Button>
        </Can>
      </header>

      <p className="mt-1 text-[11.5px] leading-[1.6] text-ink-mute">
        这一层只放只读等基础能力；组织管理类权限请用小组或个人层授予，避免给全站发管理权。
      </p>

      <div className="mt-2.5 flex flex-wrap gap-1">
        {query.isLoading ? (
          <Skeleton className="h-5 w-40" />
        ) : query.isError ? (
          <p className="text-[11px] text-danger">团队权限加载失败：{query.error.message}</p>
        ) : codes.length === 0 ? (
          <p className="text-[11px] text-ink-mute">尚未配置基础权限</p>
        ) : (
          codes.map((code) => (
            <span key={code} title={code}>
              <Chip>{labelOf(code)}</Chip>
            </span>
          ))
        )}
      </div>
    </section>
  );
}
