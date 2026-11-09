import { Building2, FolderClosed, Globe2 } from 'lucide-react';
import { useTeams } from '@/entities/asset/api/team.api';
import { cn } from '@/shared/lib/cn';
import { libraryOptions, type LibraryId } from './model/library';

/**
 * 第①段：顶栏库切换。
 *
 * 这一条是这次改造的**入口导航**，不是筛选项。以前"公共 / 我的"是两个并排的
 * tab，团队库落地后那个形态撑不住（5 个库平铺会把分类条挤到第二行），
 * 所以改成一组分组的按钮：我的 / 团队 / 公共。
 *
 * **团队列表读不到时只显示我的与公共**，不显示错误：库切换器坏了不该让整页
 * 打不开，而"少一个团队库"是用户能理解的状态（他可能确实不在任何团队里）。
 */
export function LibrarySwitcher({
  value,
  onChange,
}: {
  value: LibraryId;
  onChange: (next: LibraryId) => void;
}) {
  const {data} = useTeams();
  const teams = data?.items ?? [];
  const options = libraryOptions(teams);

  return (
    <nav aria-label="库切换" className="flex flex-wrap items-center gap-1.5">
      {options.map((option) => {
        const active = option.id === value;
        // 团队库的高亮要按 id 判，不能按 `kind`：多个团队库同时存在时
        // 按 kind 判会让所有团队按钮一起亮
        const Icon = option.kind === 'personal' ? FolderClosed : option.kind === 'brand' ? Globe2 : Building2;
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.id)}
            className={cn(
              'inline-flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors',
              active
                ? 'border-primary-line bg-primary-soft font-semibold text-primary-deep'
                : 'border-line bg-panel text-ink-soft hover:border-line-strong',
            )}
          >
            <Icon size={13} aria-hidden />
            {option.label}
            {option.kind === 'team' && option.role && (
              <span className="rounded-[4px] bg-panel-soft px-1 text-[10px] text-ink-mute">
                {option.role === 'admin' ? '管理员' : '成员'}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
