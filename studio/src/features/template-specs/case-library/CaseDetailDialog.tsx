import type { TemplateCaseOut } from '@/shared/api/schema';
import {
  CASE_STATUS_LABELS,
  formatTime,
  snapshotModel,
  snapshotPrompt,
  snapshotSlotCount,
} from '@/entities/template-case/model/caseMeta';
import { Button, Chip, Modal, Notice } from '@/shared/ui';

export interface CaseDetailDialogProps {
  item: TemplateCaseOut | null;
  onClose: () => void;
  onReuse: (item: TemplateCaseOut) => void;
}

/**
 * 案例详情。
 *
 * 重点是把**案例与模板的关系**摊开：案例是关联模板当前草稿的一份快照，
 * 所以这里同时给两边的信息 —— 模板名 / key / 版本，以及这份快照里带了什么
 * （槽位数、模型、提示词）。
 *
 * 「复用」按钮只对**已发布**的案例开放：下架的案例别人拿不到复用入口
 * （后端也不让），摆一个点了就报错的按钮没有意义。
 */
export function CaseDetailDialog({ item, onClose, onReuse }: CaseDetailDialogProps) {
  if (!item) return null;

  const reusable = item.status === 'published';
  const slots = snapshotSlotCount(item.config);
  const model = snapshotModel(item.config);
  const prompt = snapshotPrompt(item.config);

  const rows: {label: string; value: string}[] = [
    {label: '关联模板', value: `${item.specName || item.specKey}（${item.specKey}）`},
    {label: '模板版本', value: `v${item.specVersion}`},
    {label: '发布者', value: item.ownerName || '未知'},
    {label: '发布时间', value: formatTime(item.publishedAt ?? item.createdAt)},
    {label: '分类', value: item.category || '未分类'},
    {label: '快照槽位', value: `${slots} 个`},
    {label: '快照模型', value: model || '未指定'},
  ];

  return (
    <Modal open onClose={onClose} title={item.name} width={560}>
      <div className="flex flex-col gap-3 px-[18px] py-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip tone={item.status === 'published' ? 'ok' : 'warn'}>
            {CASE_STATUS_LABELS[item.status] ?? item.status}
          </Chip>
          {item.category && <Chip tone="primary">{item.category}</Chip>}
          {item.sourceMissing && <Chip tone="warn">源模板已删除</Chip>}
        </div>

        {item.description && (
          <p className="text-body leading-relaxed text-ink-soft">{item.description}</p>
        )}

        {item.sourceMissing && (
          <Notice tone="warning">
            关联的模板已被删除。案例自带配置快照，仍然可以复用。
          </Notice>
        )}

        <dl className="overflow-hidden rounded-lg border border-line-soft">
          {rows.map((row, index) => (
            <div
              key={row.label}
              className={
                index === 0
                  ? 'flex items-start gap-3 px-2.5 py-1.5'
                  : 'flex items-start gap-3 border-t border-line-soft px-2.5 py-1.5'
              }
            >
              <dt className="w-20 shrink-0 pt-px text-meta text-ink-mute">{row.label}</dt>
              <dd className="min-w-0 flex-1 break-words text-body text-ink">{row.value}</dd>
            </div>
          ))}
        </dl>

        {prompt && (
          <div className="rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
            <p className="text-micro text-ink-mute">快照里的主提示词</p>
            <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-meta leading-[1.5] text-ink-soft">
              {prompt}
            </p>
          </div>
        )}

        {!reusable && (
          <Notice tone="warning">已下架的案例不能再复用；作者可以在「我发布的」里重新上架。</Notice>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
            关闭
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!reusable}
            onClick={() => onReuse(item)}
          >
            复用为新模板
          </Button>
        </div>
      </div>
    </Modal>
  );
}
