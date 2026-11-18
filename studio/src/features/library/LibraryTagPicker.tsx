import { useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { LibraryAssetOut, LibraryTagStatOut } from '@/shared/api/schema';
import { selectableTags, tagLabel } from '@/entities/library/model/tagTree';
import { displayName } from '@/entities/library/model/types';

export interface LibraryTagPickerProps {
  asset: LibraryAssetOut;
  /** 整个词表（含 0 条的），来自当前入口的 `GET /library/tags` */
  tags: LibraryTagStatOut[];
  busy?: boolean;
  /** 交回**完整**的 tagIds，上层一次 PATCH 写回，避免「新增」变成新接口 */
  onSubmit: (asset: LibraryAssetOut, tagIds: string[]) => void;
  onClose: () => void;
}

const PILL =
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-[3px] text-body leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';

interface Option {
  value: string;
  label: string;
  parentId: string;
  /** 分组标题：父标签的整条链（「产品 / 3030」），三级词要能看出挂在哪个系列下 */
  groupLabel: string;
  /** 后端给的父链（父不在词表里时的兜底） */
  parentName: string;
  /** 排序用：父标签不在词表里时退回组内第一项的添加时间 */
  createdAt: string;
}

/**
 * 卡片标签行末尾那个 `+` 点开的东西。
 *
 * 只列**叶子**（`childCount === 0`）：有下级的层级只做分组，后端会拒掉把它
 * 直接打在素材上的请求，摆出来等于给用户一个必定失败的按钮。一级、二级、三级
 * 的叶子都在列表里，分组标题取父标签的整条链（「产品」「产品 / 3030」），
 * 同名的系列才不会混在一起；无父的归到「未分组」。
 *
 * **这里不提供新建**：标签词表是全局结构，由「标签管理」统一维护。
 * 就地新建只能建一级（挂不上维度），建出来的词落在错误的层级上，
 * 还得再去标签管理里挪一次 —— 与其给一条走不通的路，不如只留一个去处。
 */
export function LibraryTagPicker({
  asset,
  tags,
  busy,
  onSubmit,
  onClose,
}: LibraryTagPickerProps) {
  const currentIds = useMemo(() => (asset.tags ?? []).map((tag) => tag.id), [asset.tags]);
  const [picked, setPicked] = useState<string[]>([]);

  const byId = useMemo(() => new Map(tags.map((tag) => [tag.id, tag])), [tags]);

  const options = useMemo<Option[]>(() => {
    const all: Option[] = selectableTags(tags).map((tag) => {
      const parent = tag.parentId ? byId.get(tag.parentId) : undefined;
      return {
        value: tag.id,
        label: tagLabel(tag),
        parentId: tag.parentId ?? '',
        groupLabel: parent ? tagLabel(parent) : tag.parentName || '未分组',
        parentName: tag.parentName ?? '',
        createdAt: tag.createdAt,
      };
    });
    const seen = new Set<string>();
    return all.filter((option) => {
      if (seen.has(option.value) || currentIds.includes(option.value)) return false;
      seen.add(option.value);
      return true;
    });
  }, [tags, byId, currentIds]);

  const groups = useMemo(() => {
    const byParent = new Map<string, Option[]>();
    for (const option of options) {
      byParent.set(option.parentId, [...(byParent.get(option.parentId) ?? []), option]);
    }
    return [...byParent.entries()]
      .map(([parentId, items]) => {
        const parent = parentId ? byId.get(parentId) : undefined;
        return {
          key: parentId || '_none',
          // 分组标题用父标签的整条链：三级词挂在二级下面时要显示「产品 / 3030」
          title: parent ? tagLabel(parent) : '未分组',
          order: parent?.createdAt ?? items[0]?.createdAt ?? '',
          items,
        };
      })
      .sort((a, b) => {
        // 「未分组」放最后，其余按父标签的添加时间排，跟词表顺序一致
        if (a.key === '_none') return 1;
        if (b.key === '_none') return -1;
        return a.order.localeCompare(b.order) || a.key.localeCompare(b.key);
      });
  }, [options, byId]);

  function toggle(id: string) {
    setPicked((previous) =>
      previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id],
    );
  }

  function submit() {
    if (!picked.length || busy) return;
    onSubmit(asset, [...currentIds, ...picked]);
  }

  return (
    <Modal
      open
      onClose={onClose}
      width={460}
      title={`给「${displayName(asset.name)}」添加标签`}
    >
      <div className="space-y-3 p-[18px]">
        {/* 原来这三句都在说**界面上已经看得见的事**：
            - 「按上级分组列出」→ 分组标题就在每组上方；
            - 「带下级的层级只作分组，不能直接打在素材上」→
              带下级的词**根本不出现在列表里**（列的是叶子）；
            - 「已有标签请在卡片上点 × 移除」→ 这个弹窗就是从卡片上的 ＋
              打开的，× 就在卡片上，属跨界面重复指引。
            全删。 */}

        <div className="flex max-h-[240px] min-h-[52px] flex-col gap-2 overflow-auto rounded-lg border border-line-soft bg-panel-soft/50 p-2">
          {groups.length === 0 ? (
            <span className="px-1 py-2 text-body text-ink-mute">
              词表里的标签都已添加
            </span>
          ) : (
            groups.map((group) => (
              <div key={group.key}>
                <div className="mb-1 px-0.5 text-xs font-medium text-ink-mute">
                  {group.title}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {group.items.map((option) => {
                    const isActive = picked.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        aria-pressed={isActive}
                        disabled={busy}
                        onClick={() => toggle(option.value)}
                        className={cn(PILL, isActive ? ACTIVE : IDLE, 'disabled:opacity-50')}
                      >
                        {isActive && <Check size={11} strokeWidth={2.5} />}
                        <span className="max-w-[160px] truncate">{option.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-line-soft pt-3">
          <span className="text-body text-ink-mute">
            {picked.length ? `已选 ${picked.length} 个` : '还没选标签'}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!picked.length || busy}
              onClick={submit}
            >
              {busy ? '添加中…' : '添加'}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
