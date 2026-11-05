import { Link } from 'react-router-dom';
import { Bell, CircleHelp } from 'lucide-react';
import { useSubject } from '@/shared/access/use-access';
import { toast } from '@/shared/lib/toast';
import { Slot } from '@/shared/registry/Slot';
import { IconButton } from '@/shared/ui';
import { APP_NAME, HOME_PATH } from '@/shared/config/app';

export function TopBar() {
  const subject = useSubject();

  return (
    <header className="sticky top-0 z-30 flex h-[54px] items-center justify-between border-b border-[rgba(214,228,232,.8)] bg-[rgba(255,255,255,.72)] px-[22px] backdrop-blur-[10px] 2xl:px-7">
      <Link to={HOME_PATH} className="flex items-center gap-2.5">
        <img src="/favicon.svg?v=yufan" alt="雨范 Logo" className="h-9 w-10 shrink-0 object-contain" />
        <div className="flex flex-col leading-none">
          <b className="text-[15px] font-extrabold tracking-[3px] text-[#26251f]">雨范</b>
          <span className="mt-[3px] text-[7px] tracking-[1.6px] text-ink-mute">AI STUDIO</span>
        </div>
        <div className="h-4 w-px bg-line-strong" />
        <span className="text-sm font-semibold tracking-[.3px] text-ink">{APP_NAME}</span>
        <span className="rounded-pill border border-line-strong bg-panel px-[7px] py-px text-[10px] tabular-nums text-ink-mute">
          v3.0
        </span>
      </Link>

      <div className="flex items-center gap-2">
        {/* 顶栏动作插槽：搜索、全局新建、通知中心等由模块注入 */}
        <Slot name="topbar.actions" />
        <IconButton icon={<Bell size={16} />} label="消息" onClick={() => toast('暂无新消息')} />
        <IconButton icon={<CircleHelp size={16} />} label="帮助" onClick={() => toast('文档中心建设中')} />
        <button
          type="button"
          className="grid h-[30px] w-[30px] place-items-center rounded-[10px] bg-[linear-gradient(140deg,#4f8cff,#12c4c8)] text-xs font-bold text-white shadow-[0_4px_10px_rgba(43,124,255,.3)]"
          title={subject.name}
        >
          {subject.name.slice(0, 1) || '?'}
        </button>
      </div>
    </header>
  );
}
