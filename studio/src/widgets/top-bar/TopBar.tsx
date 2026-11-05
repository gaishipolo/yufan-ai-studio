import { Link } from 'react-router-dom';
import { Bell, CircleHelp } from 'lucide-react';
import { toast } from '@/shared/lib/toast';
import { Slot } from '@/shared/registry/Slot';
import { BrandMark, IconButton } from '@/shared/ui';
import { APP_NAME, HOME_PATH } from '@/shared/config/app';
import { UserMenu } from '@/widgets/user-menu/UserMenu';

export function TopBar() {
  return (
    <header className="sticky top-0 z-30 flex h-[54px] items-center justify-between border-b border-[rgba(214,228,232,.8)] bg-[rgba(255,255,255,.72)] px-[22px] backdrop-blur-[10px] 2xl:px-7">
      <Link to={HOME_PATH} className="flex min-w-0 items-center gap-2.5">
        <BrandMark size={26} />
        <div className="flex shrink-0 flex-col leading-none whitespace-nowrap">
          <b className="text-[15px] font-extrabold tracking-[3px] text-ink">雨范</b>
          <span className="mt-[3px] text-[7px] tracking-[1.6px] text-ink-mute">AI STUDIO</span>
        </div>
        {/* 640 以下只留字标：产品名 + 版本号挤在同一行会把「雨范」压成两行（390px 实测） */}
        <div className="hidden h-4 w-px shrink-0 bg-line-strong sm:block" />
        <span className="hidden whitespace-nowrap text-sm font-semibold tracking-[.3px] text-ink sm:inline">
          {APP_NAME}
        </span>
        <span className="hidden shrink-0 whitespace-nowrap rounded-pill border border-line-strong bg-panel px-[7px] py-px text-[10px] tabular-nums text-ink-mute sm:inline">
          v3.0
        </span>
      </Link>

      <div className="flex shrink-0 items-center gap-2">
        {/* 顶栏动作插槽：搜索、全局新建、通知中心等由模块注入 */}
        <Slot name="topbar.actions" />
        <IconButton icon={<Bell size={16} />} label="消息" onClick={() => toast('暂无新消息')} />
        <IconButton icon={<CircleHelp size={16} />} label="帮助" onClick={() => toast('文档中心建设中')} />
        <UserMenu />
      </div>
    </header>
  );
}
