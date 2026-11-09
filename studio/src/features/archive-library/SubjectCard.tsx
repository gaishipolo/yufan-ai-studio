import { Layers } from 'lucide-react';
import type { SubjectOut } from '@/shared/api/schema';
import { Chip, CoverImage } from '@/shared/ui';
import { availabilityOf, memberSummary } from '@/entities/asset/model/subject';

/**
 * 主体卡片：**堆叠态**。
 *
 * 一张主体是"一组图"，所以不能画成和素材卡一样的单张缩略图——那会让用户
 * 以为点进去是一张图。三层错位的底板 + 顶上一张封面，是不用文字就能表达
 * "这里面有好几张"的唯一办法（也是相册、文件夹这些既有隐喻的做法）。
 *
 * 角标用**服务端给的** `availability`，不在这里重算：卡片说能发、点下去报
 * 缺少能力，是这类界面最容易失去信任的失败方式。
 */
export function SubjectCard({
  subject,
  onOpen,
}: {
  subject: SubjectOut;
  onOpen: (id: string) => void;
}) {
  const state = availabilityOf(subject.availability);
  return (
    <button
      type="button"
      onClick={() => onOpen(subject.id)}
      aria-label={`打开主体 ${subject.name}`}
      className="group w-full rounded-lg border border-line-soft bg-panel p-2.5 text-left transition hover:border-primary-line hover:shadow-sm"
    >
      <div className="relative h-36">
        {/* 两层底板：只做"还有别的"这一件事，所以刻意压暗、不加载图片 */}
        <div className="absolute inset-x-3 -bottom-1 h-32 rounded-md border border-line-soft bg-panel-soft" />
        <div className="absolute inset-x-1.5 -bottom-0.5 h-32 rounded-md border border-line-soft bg-panel-soft opacity-80" />
        <CoverImage
          src={subject.coverUrl}
          seed={subject.id}
          alt={subject.name}
          className="absolute inset-x-0 bottom-0 h-32 rounded-md"
        />
        <div className="absolute right-1.5 top-1.5 flex items-center gap-1">
          {!!subject.memberCount && (
            <span className="inline-flex items-center gap-0.5 rounded-[5px] bg-black/55 px-1.5 py-px text-[10px] leading-[15px] text-white">
              <Layers size={10} />
              {subject.memberCount}
            </span>
          )}
        </div>
      </div>

      <div className="mt-2.5 space-y-1">
        <div className="truncate text-sm font-medium text-ink" title={subject.name}>
          {subject.name || '未命名主体'}
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-ink-mute">
            {memberSummary(subject.memberCount, subject.currentVersion)}
          </span>
          <Chip tone={state.tone}>{state.label}</Chip>
        </div>
        {/* 有时间就给一句话解释；没有就不占这一行，免得卡片高矮不齐 */}
        {!!subject.availabilityNote && (
          <div className="truncate text-[11px] text-ink-mute" title={subject.availabilityNote}>
            {subject.availabilityNote}
          </div>
        )}
      </div>
    </button>
  );
}
