import { useState } from 'react';
import type { TemplateCaseOut } from '@/shared/api/schema';
import { useTemplateCases } from '@/entities/template-case/api/case.queries';
import { snapshotSlotCount } from '@/entities/template-case/model/caseMeta';
import { Button, Chip, EmptyState, Modal, SearchInput } from '@/shared/ui';

export interface CasePickerDialogProps {
  open: boolean;
  onClose: () => void;
  /** 选中一份案例：调用方把它打开成一份新草稿。 */
  onPick: (item: TemplateCaseOut) => void;
}

/**
 * 案例选择器（新建模板流程里的「从案例库复用」入口）。
 *
 * 只列**公共案例库**里已发布的案例：下架的与回收站里的别人本来就看不到，
 * 这里也不该出现（后端的 `library` 口径已经这么过滤了）。
 *
 * 选中之后带过去的是案例的**配置快照**：槽位、提示词、模型、容器名原样带入，
 * 模板 key 与名称换成新的 —— 复用者拿到的是一份能直接改的起点。
 */
export function CasePickerDialog({ open, onClose, onPick }: CasePickerDialogProps) {
  const [keyword, setKeyword] = useState('');
  const cases = useTemplateCases({ scope: 'library', keyword, pageSize: 100 });
  const items = cases.data?.items ?? [];

  return (
    <Modal open={open} onClose={onClose} title="从案例库复用" width={640}>
      <div className="flex flex-col gap-3 px-[18px] py-4">
        <p className="text-meta leading-[1.6] text-ink-mute">
          选中一份案例后会新建一份模板：案例里的槽位、提示词与模型会原样带过来，
          模板名称与 key 换成新的，你可以接着改。
        </p>

        <SearchInput
          value={keyword}
          placeholder="搜索案例名称、描述或模板 key"
          onChange={event => setKeyword(event.target.value)}
        />

        {cases.isLoading ? (
          <p className="py-8 text-center text-xs text-ink-mute">正在读取…</p>
        ) : items.length === 0 ? (
          <EmptyState title="没有可复用的案例" />
        ) : (
          <div className="flex max-h-[46vh] flex-col gap-2 overflow-auto pr-1">
            {items.map(item => (
              <div
                key={item.id}
                className="flex items-start gap-3 rounded-xl border border-line-soft bg-panel px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-body font-medium text-ink">{item.name}</span>
                    {item.category && <Chip tone="primary">{item.category}</Chip>}
                  </div>
                  {item.description && (
                    <p className="mt-0.5 line-clamp-2 text-meta leading-[1.5] text-ink-soft">
                      {item.description}
                    </p>
                  )}
                  <p className="mt-1 flex flex-wrap items-center gap-1.5 text-micro text-ink-mute">
                    <span className="truncate">关联模板 {item.specName || item.specKey}</span>
                    <span className="text-line-strong">·</span>
                    <span className="tabular-nums">{snapshotSlotCount(item.config)} 个槽位</span>
                    <span className="text-line-strong">·</span>
                    <span className="truncate">{item.ownerName || '未知'}</span>
                  </p>
                </div>
                <Button size="sm" variant="primary" onClick={() => onPick(item)}>
                  复用
                </Button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center justify-end border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" onClick={onClose}>
            取消
          </Button>
        </div>
      </div>
    </Modal>
  );
}
