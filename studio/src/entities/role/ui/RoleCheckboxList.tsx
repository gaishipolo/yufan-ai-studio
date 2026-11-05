import { Chip } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { Role } from '../model/types';

/**
 * 角色多选列表 —— 新建用户与「分配角色」共用同一份交互。
 * 只负责「选哪些」，不管提交（提交的入参形状由各自的 feature 决定）。
 *
 * 布局用 grid 而不是「label 里再套两层 span」：一是让描述稳定落在第二行，
 * 二是 a11y 规则（`label-has-associated-control`）要求可访问文本在两层以内。
 */
export function RoleCheckboxList({
  roles,
  value,
  onChange,
  disabled,
}: {
  roles: Role[];
  value: string[];
  onChange: (roleIds: string[]) => void;
  disabled?: boolean;
}) {
  if (roles.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line-strong bg-panel-soft px-3 py-4 text-center text-[11.5px] text-ink-mute">
        还没有可用角色，请先在「角色与权限」页签创建
      </p>
    );
  }

  return (
    <div className="max-h-[196px] space-y-1 overflow-auto rounded-lg border border-line-soft bg-panel-soft p-1.5">
      {roles.map((role) => {
        const checked = value.includes(role.id);
        const inputId = `role-checkbox-${role.id}`;
        return (
          <label
            key={role.id}
            htmlFor={inputId}
            className={cn(
              'grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-0.5 rounded-[7px] px-2 py-1.5 transition-colors',
              checked ? 'bg-white shadow-card' : 'hover:bg-white/70',
              disabled && 'cursor-not-allowed opacity-60',
            )}
          >
            <input
              id={inputId}
              type="checkbox"
              className="row-span-2 h-3 w-3 shrink-0 accent-primary"
              checked={checked}
              disabled={disabled}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...value, role.id]
                    : value.filter((id) => id !== role.id),
                )
              }
            />
            <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-ink">
              {role.name}
              {role.isSuperuser && <Chip tone="warn">超管</Chip>}
              {role.isBuiltin && !role.isSuperuser && <Chip>内置</Chip>}
            </span>
            <span className="truncate text-[11px] text-ink-mute">{role.description || role.code}</span>
          </label>
        );
      })}
    </div>
  );
}
