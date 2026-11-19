import { useMemo } from 'react';
import { ShieldCheck } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { AlertCircle, Loader2 } from 'lucide-react';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { useEffectivePermissions } from '@/entities/group/api/group.queries';
import { displayNameOf, type User } from '@/entities/user/model/types';

function CodeList({ codes, labelOf }: { codes: string[]; labelOf: (code: string) => string }) {
  if (codes.length === 0) return <p className="text-meta text-ink-mute">未授予</p>;
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
  codes,
  labelOf,
}: {
  title: string;
  codes: string[];
  labelOf: (code: string) => string;
}) {
  return (
    <section className="rounded-lg border border-line-soft">
      <header className="flex items-center gap-2 border-b border-line-soft bg-panel-soft px-2.5 py-1.5">
        <h3 className="text-xs font-semibold text-ink-soft">{title}</h3>
        <span className="text-meta tabular-nums text-ink-mute">{codes.length}</span>
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
        {/* 这一句是**模型**不是解释：四层取并集、没有否决规则，
            决定了「个人权限只会更多，不会更少」。删了它管理员会以为
            个人层能收回小组给的权限。 */}
        <p className="rounded-lg bg-panel-soft px-2.5 py-2 text-meta leading-[1.6] text-ink-soft">
          有效权限 = 团队 ∪ 所有小组 ∪ 个人 ∪ 角色。四层取并集、没有否决规则，
          所以个人权限只会比小组或团队更多，不会更少。
          页面层单独算：接口权限还要看它所属的页面开没开。
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
                <p className="text-meta font-medium text-ink">
                  有效权限 {effective.length} 项
                  {effective.includes('*') && <span className="ml-1.5 text-primary">（超级管理员：全量）</span>}
                </p>
              </div>
            </div>

            <Section title="团队" codes={data?.team ?? []} labelOf={labelOf} />
            {(data?.groups ?? []).map((group) => (
              <Section
                key={group.id}
                title={`小组 · ${group.name}`}
                codes={group.permissions ?? []}
                labelOf={labelOf}
              />
            ))}
            <Section title="个人" codes={data?.personal ?? []} labelOf={labelOf} />
            <Section title="角色" codes={data?.roles ?? []} labelOf={labelOf} />

            {/* 页面层：它决定「看得见哪一页」，也决定下面那些接口还算不算数 */}
            <Section title="页面（决定能看见哪一页）" codes={data?.pages ?? []} labelOf={labelOf} />

            {/* 门控掉的接口码：这一段是整个界面里最该被看见的 ——
                「权限列表里明明有 task:read，接口却 403」的答案就在这里。
                没有它，管理员只能靠猜（或者去翻日志）。 */}
            {(data?.gatedOff ?? []).length > 0 ? (
              <section className="rounded-lg border border-danger-line bg-danger-soft px-2.5 py-2">
                <header className="flex items-center gap-2">
                  <AlertCircle size={13} className="shrink-0 text-danger" />
                  <h3 className="text-xs font-semibold text-ink">被页面门控关掉的接口</h3>
                  <span className="text-meta tabular-nums text-ink-mute">
                    {data?.gatedOff?.length}
                  </span>
                </header>
                <p className="mt-1 text-meta leading-[1.6] text-ink-soft">
                  下面这些接口权限已经授予，但它们所属的页面一个都没开，所以现在不生效。
                  开对应页面即可恢复；页面权限在「角色 / 小组 / 个人」里配置。
                </p>
                <div className="mt-1.5">
                  <CodeList codes={data?.gatedOff ?? []} labelOf={labelOf} />
                </div>
              </section>
            ) : null}
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
