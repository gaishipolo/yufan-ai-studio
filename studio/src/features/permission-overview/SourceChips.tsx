import { cn } from '@/shared/lib/cn';
import type { Origin } from './use-overview';

const KIND_LABEL: Record<string, string> = {
  team: '团队',
  group: '小组',
  user: '个人',
  role: '角色',
};

/**
 * 一行权限的来源。
 *
 * 只有「个人」用强调色：它是**唯一能在这里改**的一层。其余三层是共享单元，
 * 在本页只读 —— 用颜色把这件事说出来，比加一句「团队/小组/角色请去别处改」省。
 */
export function SourceChips({ origins }: { origins: Origin[] }) {
  if (origins.length === 0) {
    return <span className="text-meta text-ink-mute">无来源</span>;
  }
  return (
    <span className="flex flex-wrap items-center gap-1">
      {origins.map((origin) => {
        const own = origin.kind === 'user';
        const kind = KIND_LABEL[origin.kind] ?? origin.kind;
        // 团队没有具体实体，名字固定是「团队」；再拼一次就成了「团队·团队」
        const text = origin.kind === 'team' || !origin.name ? kind : `${kind}·${origin.name}`;
        return (
          <span
            key={`${origin.kind}:${origin.id || origin.name}`}
            title={own ? '个人层：只能影响这个账号' : `${kind}层：改动会影响该${kind}的所有人`}
            className={cn(
              'max-w-[9.5rem] truncate rounded-full border px-1.5 py-[1px] text-micro',
              own
                ? 'border-primary-line bg-primary-soft text-primary'
                : 'border-line-soft bg-panel-soft text-ink-soft',
            )}
          >
            {text}
          </span>
        );
      })}
    </span>
  );
}
