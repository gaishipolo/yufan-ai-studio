import type { ReactNode } from 'react';
import { useCan } from '@/shared/access/use-access';
import { useOptionalRegistry } from './use-registry';
import type { ResolvedSlot, SlotName } from './types';

function SlotItem({ slot }: { slot: ResolvedSlot }) {
  const allowed = useCan(slot.permissions);
  if (!allowed) return null;
  const Component = slot.component;
  return <Component />;
}

/**
 * 插槽渲染器：模块往 `slots` 里声明内容，这里按 order 渲染。
 * 没有 Provider / 没有贡献时渲染 fallback，因此在单测里可以裸渲染。
 */
export function Slot({ name, fallback = null }: { name: SlotName; fallback?: ReactNode }) {
  const registry = useOptionalRegistry();
  const slots = registry?.slots[name];
  if (!slots?.length) return <>{fallback}</>;

  return (
    <>
      {/* key 用注册序号而不是数组下标：下标是排序后的位置，order 一变就整体漂移 */}
      {slots.map((slot) => (
        <SlotItem key={`${slot.moduleId}:${slot.seq}`} slot={slot} />
      ))}
    </>
  );
}
