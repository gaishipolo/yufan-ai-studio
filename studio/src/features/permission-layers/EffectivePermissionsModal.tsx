import { useMemo } from 'react';
import { ShieldCheck } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { AlertCircle, Loader2 } from 'lucide-react';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { useEffectivePermissions } from '@/entities/group/api/group.queries';
import { displayNameOf, type User } from '@/entities/user/model/types';

function CodeList({ codes, labelOf }: { codes: string[]; labelOf: (code: string) => string }) {
  if (codes.length === 0) return <p className="text-[11px] text-ink-mute">未授予</p>;
  return (
    <div className="flex flex-wrap gap-1">
      {codes.map((code) => (
        <span key={code} title={code}>
          <Chip>{labelOf(code)}</Chip>
        </span>
      ))}
    </div>
  );
}

function Section({
  title,
  hint,
  codes,
  labelOf,
}: {
  title: string;
  hint: string;
  codes: string[];
  labelOf: (code: string) => string;
}) {
  return (
    <section className="rounded-lg border border-line-soft">
      <header className="flex items-center gap-2 border-b border-line-soft bg-panel-soft px-2.5 py-1.5">
        <h3 className="text-[11.5px] font-semibold text-ink-soft">{title}</h3>
        <span className="text-[11px] tabular-nums text-ink-mute">{codes.length}</span>
        <span className="ml-auto text-[11px] text-ink-mute">{hint}</span>
      </header>
      <div className="px-2.5 py-2">
        <CodeList codes={codes} labelOf={labelOf} />
      </div>
    </section>
  );
}

/**
 * 「有效权限」详情：按 团队 / 小组 / 个人 / 角色 四段展示来源，并给出并集。
 *
 * 这里**只读**。判定永远发生在后端 `require()`，前端不拿这份数据自己去算 `can()` ——
 * 一旦前端开始重算，就等于把「合并规则」复制到两个地方，漏一层就是越权或误禁。
 */
export function EffectivePermissionsModal({
  open,
  user,
  onClose,
  onEditPersonal,
}: {
  open: boolean;
  user: User | null;
  onClose: () => void;
  onEditPersonal: () => void;
}) {
  const query = useEffectivePermissions(open ? (user?.id ?? null) : null);
  const catalogQuery = usePermissionCatalog();

  const labelOf = useMemo(() => {
    const names = new Map<string, string>();
    for (const group of catalogQuery.data?.groups ?? []) {
      for (const item of group.items) names.set(item.code, item.name);
    }
    return (code: string) => names.get(code) ?? code;
  }, [catalogQuery.data]);

  const data = query.data;
  const effective = data?.effective ?? [];

  return (
    <Modal open={open} onClose={onClose} title={`有效权限 · ${user ? displayNameOf(user) : ''}`} width={640}>
      <div className="space-y-3 px-[18px] py-4">
        <p className="rounded-lg bg-panel-soft px-2.5 py-2 text-[11.5px] leading-[1.6] text-ink-soft">
          有效权限 = 团队 ∪ 所有小组 ∪ 个人 ∪ 角色。四层取并集、没有否决规则，
          所以个人权限只会比小组或团队更多，不会更少。
        </p>

        {query.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-xs text-ink-mute">
            <Loader2 size={14} className="animate-spin" /> 正在读取权限来源…
          </div>
        ) : query.isError ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <AlertCircle size={22} className="text-danger" />
            <p className="text-xs text-ink-soft">权限来源加载失败：{query.error.message}</p>
            <Button size="sm" onClick={() => void query.refetch()}>
              重试
            </Button>
          </div>
        ) : (
          <>
            <div className="flex items-start gap-2 rounded-lg border border-primary-line bg-primary-soft px-2.5 py-2">
              <ShieldCheck size={14} className="mt-[2px] shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="text-[11.5px] font-medium text-ink">
                  有效权限 {effective.length} 项
                  {effective.includes('*') && <span className="ml-1.5 text-primary">（超级管理员：全量）</span>}
                </p>
                <p className="mt-0.5 text-[11px] leading-[1.5] text-ink-mute">
                  这是 require() 的唯一判据；下面四段只是它的来源明细。
                </p>
              </div>
            </div>

            <Section title="团队" hint="全体启用账号" codes={data?.team ?? []} labelOf={labelOf} />
            {(data?.groups ?? []).map((group) => (
              <Section
                key={group.id}
                title={`小组 · ${group.name}`}
                hint="所属小组"
                codes={group.permissions ?? []}
                labelOf={labelOf}
              />
            ))}
            <Section title="个人" hint="单独授予" codes={data?.personal ?? []} labelOf={labelOf} />
            <Section title="角色" hint="现有角色体系" codes={data?.roles ?? []} labelOf={labelOf} />
          </>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose}>关闭</Button>
          <Can permission="identity:user:permission:manage">
            <Button variant="primary" disabled={!user} onClick={onEditPersonal}>
              编辑个人权限
            </Button>
          </Can>
        </div>
      </div>
    </Modal>
  );
}
