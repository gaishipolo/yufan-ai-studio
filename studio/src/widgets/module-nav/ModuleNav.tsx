import { useLocation, useNavigate } from 'react-router-dom';
import { useCan } from '@/shared/access/use-access';
import { cn } from '@/shared/lib/cn';
import { isPathActive } from '@/shared/lib/path';
import { Slot } from '@/shared/registry/Slot';
import { useRegistry } from '@/shared/registry/use-registry';
import type { ResolvedCard } from '@/shared/registry/types';

function ModuleCardButton({ card, active }: { card: ResolvedCard; active: boolean }) {
  const navigate = useNavigate();
  const allowed = useCan(card.permissions);
  if (!allowed) return null;

  const Icon = card.icon;

  return (
    <button
      type="button"
      onClick={() => navigate(card.path)}
      className={cn(
        'flex items-center gap-3 rounded-[14px] border px-3 py-2.5 text-left transition-all duration-150',
        active
          ? 'border-[#c6dde5] bg-panel shadow-float'
          : 'border-[rgba(211,224,229,.55)] bg-[rgba(255,255,255,.45)] hover:-translate-y-px hover:bg-[rgba(255,255,255,.8)]',
      )}
    >
      <span
        className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[11px] text-white shadow-[0_4px_10px_rgba(30,60,70,.14)]"
        style={{ background: card.gradient }}
      >
        <Icon size={20} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[13.5px] font-semibold tracking-[.2px] text-ink">
          {card.title}
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-ink-mute">{card.subtitle}</span>
      </span>
    </button>
  );
}

export function ModuleNav() {
  const { pathname } = useLocation();
  const { cards } = useRegistry();

  return (
    <nav className="grid grid-cols-2 gap-2.5 px-3.5 pb-2 pt-4 md:px-[22px] lg:grid-cols-4 2xl:px-7">
      {cards.map((card) => (
        <ModuleCardButton
          key={card.path}
          card={card}
          active={Boolean(card.matchPrefixes?.some((prefix) => isPathActive(pathname, prefix)))}
        />
      ))}
      <Slot name="module-nav.trailing" />
    </nav>
  );
}
