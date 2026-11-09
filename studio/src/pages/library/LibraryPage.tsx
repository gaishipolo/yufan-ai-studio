import { FileClock, FolderOpen, Info, Trash2, UserRound, Users, UsersRound } from 'lucide-react';
import { Chip, PageHeader } from '@/shared/ui';

/**
 * 资产库 v2 · 骨架占位页（M0）
 *
 * 只固定「有哪几个入口、各自装什么」，不接数据：M1 建表、M2 可见性、
 * M3 封装与下载、M4 全套页面。这样 M4 换页面时路由与导航不用动，
 * 也避免现在先写一版「看起来能用、点进去全是假数据」的界面。
 */

const ENTRIES = [
  {
    key: 'drafts',
    label: '草稿箱',
    hint: 'AI 生成结果先落这里，本人确认后才进个人库',
    icon: FileClock,
  },
  {
    key: 'personal',
    label: '个人素材',
    hint: '自己上传与已采纳的生成结果，只有本人可见',
    icon: UserRound,
  },
  {
    key: 'group',
    label: '小组素材',
    hint: '共享给小组的素材，组内成员可见，负责人审核',
    icon: Users,
  },
  {
    key: 'team',
    label: '团队素材',
    hint: '全员可见，任何人不含上传者都不可直接删除',
    icon: UsersRound,
  },
  {
    key: 'trash',
    label: '回收站',
    hint: '保留 30 天，被引用的条目不自动清理',
    icon: Trash2,
  },
];

/** M0 已交付 / 后续批次：写在页面上，避免「看起来是空的」被当成故障 */
const DELIVERED = [
  '模块注册与 11 个权限点',
  '三个跨模块注入口（查询 / 入库 / 引用检查）',
  '列表与分面接口壳（空库）',
];
const NEXT = [
  'M1 建表、上传与存量导入',
  'M2 可见性、共享审核、草稿与回收站',
  'M3 封装、批量下载、生成入库',
  'M4 页面全套并切换入口',
];

export default function LibraryPage() {
  return (
    <>
      <PageHeader
        title="资产库 v2"
        description="草稿箱、个人、小组、团队与回收站五个入口的统一资产库，与现役素材库并行开发。"
        actions={<Chip tone="warn">骨架阶段 M0</Chip>}
      />

      <div className="mt-4 flex items-start gap-2 rounded-card border border-line-soft bg-panel-soft px-3 py-2.5">
        <Info size={14} className="mt-0.5 shrink-0 text-ink-mute" />
        <p className="text-[11.5px] leading-[1.65] text-ink-soft">
          第二版素材库正在并行开发，现役
          <code className="mx-1 rounded bg-panel px-1 py-px font-mono text-[11px] text-ink">
            /assets
          </code>
          不受影响。两边表、权限、接口、存储前缀全部隔离，切换时只改前端模块入口一行，
          随时可回滚。
        </p>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {ENTRIES.map((entry) => (
          <div
            key={entry.key}
            className="rounded-card border border-line-soft bg-panel p-3 shadow-card"
          >
            <div className="flex items-center gap-1.5">
              <entry.icon size={14} className="text-ink-mute" />
              <span className="text-[13px] font-semibold text-ink">{entry.label}</span>
              <span className="ml-auto font-mono text-[11px] text-ink-mute">0</span>
            </div>
            <p className="mt-1.5 text-[11.5px] leading-[1.6] text-ink-soft">{entry.hint}</p>
          </div>
        ))}
      </div>

      <div className="mt-4 grid gap-2 lg:grid-cols-2">
        <div className="rounded-card border border-line-soft bg-panel p-3.5">
          <div className="flex items-center gap-1.5">
            <FolderOpen size={14} className="text-ink-mute" />
            <span className="text-[12.5px] font-semibold text-ink">M0 已交付</span>
          </div>
          <ul className="mt-2 space-y-1.5">
            {DELIVERED.map((item) => (
              <li key={item} className="flex items-start gap-1.5 text-[11.5px] text-ink-soft">
                <span className="mt-[7px] size-1 shrink-0 rounded-full bg-emerald-500" />
                {item}
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-card border border-line-soft bg-panel p-3.5">
          <span className="text-[12.5px] font-semibold text-ink">后续批次</span>
          <ul className="mt-2 space-y-1.5">
            {NEXT.map((item) => (
              <li key={item} className="flex items-start gap-1.5 text-[11.5px] text-ink-soft">
                <span className="mt-[7px] size-1 shrink-0 rounded-full bg-line-strong" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}
