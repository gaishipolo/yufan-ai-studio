import { Loader2 } from 'lucide-react';
import { Button, Chip } from '@/shared/ui';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { cn } from '@/shared/lib/cn';

export interface PermissionPickerProps {
  selected: string[];
  onChange: (codes: string[]) => void;
  /**
   * 这一层不允许授予的码。团队基础权限禁 `identity:*` 与 `*`：
   * 后端同样会拒绝，前端提前禁用是为了不让人白点一次「保存」才知道。
   */
  forbidden?: (code: string) => boolean;
  forbiddenHint?: string;
}

/**
 * 三层授权共用的权限勾选器。
 *
 * 没有把 `RolePermissionsModal` 改成复用它：那个弹窗背着两件角色专属的事
 * （`orphanedPermissions` 失效码清理、超管角色恒为 `*` 的说明与禁保存），
 * 抽成一个「通用」组件就得让团队/小组/个人也带上这两组分支，
 * 反而把不属于它们的概念塞进来了。这里只做最纯粹的一件事：按目录勾选、整组替换。
 */
export function PermissionPicker({
  selected,
  onChange,
  forbidden,
  forbiddenHint,
}: PermissionPickerProps) {
  const catalogQuery = usePermissionCatalog();

  const toggle = (code: string) => {
    if (forbidden?.(code)) return;
    onChange(selected.includes(code) ? selected.filter((item) => item !== code) : [...selected, code]);
  };

  const toggleGroup = (codes: string[], on: boolean) => {
    const next = new Set(selected);
    for (const code of codes) {
      if (forbidden?.(code)) continue;
      if (on) next.add(code);
      else next.delete(code);
    }
    onChange([...next]);
  };

  const groups = catalogQuery.data?.groups ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-ink-mute">
        <span>
          已选 <span className="font-semibold tabular-nums text-ink">{selected.length}</span> 个权限点
        </span>
        <span className="text-line-strong">·</span>
        <span>保存后整组替换</span>
        <Button size="sm" variant="ghost" className="ml-auto" disabled={selected.length === 0} onClick={() => onChange([])}>
          全部清空
        </Button>
      </div>

      {forbiddenHint && (
        <p className="rounded-lg border border-[#fbe8c8] bg-[#fff6e6] px-2.5 py-2 text-[11px] leading-[1.6] text-[#b97a10]">
          {forbiddenHint}
        </p>
      )}

      {catalogQuery.isLoading ? (
        <div className="flex items-center justify-center gap-2 py-10 text-xs text-ink-mute">
          <Loader2 size={14} className="animate-spin" /> 正在加载权限点…
        </div>
      ) : catalogQuery.isError ? (
        <p className="py-8 text-center text-xs text-danger">
          权限点加载失败：{catalogQuery.error.message}
        </p>
      ) : (
        <div className="max-h-[46vh] space-y-3 overflow-auto pr-1">
          {groups.map((group) => {
            const codes = group.items.map((item) => item.code);
            const selectable = codes.filter((code) => !forbidden?.(code));
            const allChecked =
              selectable.length > 0 && selectable.every((code) => selected.includes(code));
            return (
              <section key={group.group} className="rounded-lg border border-line-soft">
                <header className="flex items-center gap-2 border-b border-line-soft bg-panel-soft px-2.5 py-1.5">
                  <h3 className="text-[11.5px] font-semibold text-ink-soft">{group.group}</h3>
                  <span className="text-[11px] tabular-nums text-ink-mute">{codes.length}</span>
                  <button
                    type="button"
                    className="ml-auto text-[11px] text-primary hover:underline disabled:opacity-50"
                    disabled={selectable.length === 0}
                    onClick={() => toggleGroup(selectable, !allChecked)}
                  >
                    {allChecked ? '取消本组' : '全选本组'}
                  </button>
                </header>
                <ul className="grid gap-1 p-1.5 md:grid-cols-2">
                  {group.items.map((item) => {
                    const checked = selected.includes(item.code);
                    const blocked = Boolean(forbidden?.(item.code));
                    return (
                      <li key={item.code}>
                        <label
                          title={blocked ? forbiddenHint : undefined}
                          className={cn(
                            'flex items-start gap-2 rounded-[7px] px-2 py-1.5 transition-colors',
                            blocked ? 'cursor-not-allowed opacity-45' : 'cursor-pointer',
                            checked && !blocked ? 'bg-primary-soft' : !blocked && 'hover:bg-panel-soft',
                          )}
                        >
                          <input
                            type="checkbox"
                            className="mt-[3px] h-3 w-3 shrink-0 accent-primary"
                            checked={checked}
                            disabled={blocked}
                            onChange={() => toggle(item.code)}
                          />
                          <span className="min-w-0">
                            <span className="flex items-center gap-1.5">
                              <span className="text-xs text-ink">{item.name}</span>
                              <Chip>{item.code}</Chip>
                            </span>
                            {item.description && (
                              <span className="mt-0.5 block text-[11px] leading-[1.5] text-ink-mute">
                                {item.description}
                              </span>
                            )}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
