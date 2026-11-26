import { useMemo } from 'react';
import { X } from 'lucide-react';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { cn } from '@/shared/lib/cn';
import {
  leafTagsForSelection,
  selectableTags,
  tagLabel,
  tagsAtDepth,
} from '../model/tagTree';

export interface TagFilterProps {
  /** 词表（任意级别：一级维度 / 二级取值） */
  tags: LibraryTagStatOut[];
  /**
   * 只列**这批素材身上实际有**的标签（`assetCount > 0`）。
   *
   * 模板库用：作者圈了候选集之后，要显示的是「这批素材实际有的二级标签」。
   * 列出一堆这批素材一个都没挂的标签，用户点了只得到空列表 ——
   * 那是词表的全貌，不是这批素材的。
   */
  onlyWithAssets?: boolean;
  /**
   * 不显示一级维度行。
   *
   * 模板库用：一级是**维度**（产品 / 产品图类型），那只是分组头 ——
   * 范围已经由候选集定死了，用户要挑的是范围里的**具体取值**。
   * 摆一行维度出来反而让人以为还能改范围。
   */
  hideTopLevel?: boolean;
  /**
   * 不作为选项出现的标签 id —— 通常是**候选集本身**。
   *
   * 候选集里的标签就是范围本身：圈了「产品图类型 / 三视图」，那这批素材
   * 已经是三视图了，再把它列成一个可点的筛选项，点了等于没筛。
   */
  excludeIds?: string[];
  /**
   * 单选：整个组件至多一个选中项。
   *
   * 模板库/封装那侧用这个 —— 候选集已经把范围圈死了，用户在范围内
   * 只想「挑一档看看」，多选没有意义（多选的组合口径要靠提示语解释，
   * 而单选不需要解释）。
   *
   * 单选下**取值行不做维度收窄**：一级行隐藏时收窄会把用户困死在一个
   * 维度里（选了「产品/6036」之后就看不到别的取值了，换都换不了）。
   */
  single?: boolean;
  /** 已挑的标签 id（可空） */
  value: string[];
  onChange: (ids: string[]) => void;
  /** 行标题 */
  title?: string;
}

const PILL =
  'inline-flex shrink-0 items-center gap-1 rounded-xs border px-2 py-[2px] text-micro leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';

/**
 * 标签筛选条（选择器用）：**两行**，一级维度 / 二级取值。
 *
 * 口径与资产库标签栏一致：**同维度多选是或、跨维度是且**。
 *
 * 组件只管「挑了哪些」；同一维度里「维度与取值只留取值」这件事由调用方
 * 用 `effectiveTagSelection` 收敛（这里保留用户点的全部，让胶囊都高亮）。
 *
 * `single` 打开时变成**单选**：至多一个选中项，点已选项取消、点别的替换。
 */
export function TagFilter({
  tags,
  onlyWithAssets = false,
  hideTopLevel = false,
  excludeIds = [],
  single = false,
  value,
  onChange,
  title = '按标签筛选',
}: TagFilterProps) {
  /* 只留有素材的标签：词表是全的，而这批素材只挂了其中几个。 */
  const allowed = useMemo(
    () => (onlyWithAssets ? tags.filter((tag) => (tag.assetCount ?? 0) > 0) : tags),
    [tags, onlyWithAssets],
  );
  /*
   * 排除项**只在展示层**生效，计算层留着完整的树。
   *
   * 为什么不能直接从 `allowed` 里剔掉：`dimensionOf` 是靠父链找维度的，
   * 一级维度一旦被剔掉，它的取值（6036）就找不到自己的维度了 ——
   * 于是「同维度」的判定失效，兄弟取值（T35 / 3030）跟着一起消失，
   * 用户就没法做同维度多选了。
   */
  const excluded = useMemo(() => new Set(excludeIds), [excludeIds]);
  const rows = useMemo(() => {
    const visible = (items: LibraryTagStatOut[]) =>
      items.filter((tag) => !excluded.has(tag.id));
    /* 行标题带 `title` 说明这一行的**组合语义**（与 `LibraryTagBar` 同一口径：
       同维度是或、跨维度是且）。原来那是一行常驻小字，挂在顶上与它约束的
       两行隔着一段距离 —— 现在规则贴在它约束的那一行上。

       **单选时不给组合语义**：单选至多一个选中项，没有「多选是或」可言。 */
    const hints: Record<string, string | undefined> = single
      ? {}
      : {
          维度: '同一维度内多选是「或」：两个都圈上，两个都要看',
          取值: '与维度行是「且」：圈了取值就只留同时满足这些取值的素材',
        };
    return [
      ...(hideTopLevel
        ? []
        : [
            {
              label: '维度',
              hint: hints.维度,
              items: visible(tagsAtDepth(allowed, 1)),
            },
          ]),
      /* 取值行随选中项收窄：点了「产品」就只剩产品的取值，
         点了「产品」+「角色」就是两条轴的取值之和。

         列的是**叶子**（能打在素材上的词），不是硬编码「二级」——
         孤立的一级词（「音乐」）也是叶子、也真能打，按层级取会漏掉；
         有子标签的一级（产品）不是叶子，自然不出现。

         **单选时不收窄**：一级行隐藏时收窄会把用户困死在一个维度里 ——
         选了「产品/6036」之后别的取值就都不见了，连换个取值都做不到。 */
      {
        label: '取值',
        hint: hints.取值,
        items: visible(
          single ? selectableTags(allowed) : leafTagsForSelection(allowed, value),
        ),
      },
    ];
  }, [allowed, value, hideTopLevel, excluded, single]);
  const active = useMemo(() => new Set(value), [value]);

  function toggle(id: string) {
    if (single) {
      /* 单选：点已选项取消、点别的替换它（至多一个选中项）。 */
      onChange(active.has(id) ? [] : [id]);
      return;
    }
    onChange(active.has(id) ? value.filter((item) => item !== id) : [...value, id]);
  }

  if (!allowed.length) return null;

  return (
    <div className="space-y-1" role="group" aria-label={title}>
      <div className="flex items-center gap-1.5">
        <span className="text-meta text-ink-mute">{title}</span>
        {value.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            aria-label="清除标签筛选"
            className="inline-flex items-center gap-0.5 text-micro text-ink-mute hover:text-primary-deep"
          >
            <X size={10} /> 清除
          </button>
        )}
        {/* 「同一维度多选是或 · 跨维度是且」原来是一行常驻小字。
            它约束的是**下面这两行**，挂在顶上隔着一段距离。
            现在并进**行标题的 `title`**（悬浮才看）—— 规则贴在它约束的那一行上。

            单选时那句「选一个标签筛选」也删：单选胶囊（至多一个高亮）
            本身就说得出这件事。 */}
      </div>
      {rows.map((row) =>
        row.items.length ? (
          <div
            key={row.label}
            role="group"
            aria-label={`${row.label}标签`}
            className="flex flex-wrap items-center gap-1"
          >
            {/* 只剩一行时不写层级前缀：标签本身说得出自己是什么，
                再加个层级标签只是噪音（用户约定：避免不必要的辅助标签）。

                有两行时，行标题带 `title` 说明这一行的组合语义
                （与 `LibraryTagBar` 同一口径：同维度是或、跨维度是且）。 */}
            {rows.length > 1 && (
              <span
                className="w-8 shrink-0 text-micro text-ink-mute"
                title={row.hint}
              >
                {row.label}
              </span>
            )}
            {row.items.map((tag) => {
              const isActive = active.has(tag.id);
              /* 显示短名，悬停说明给完整路径 —— 与标签栏同一口径。 */
              const short = tag.name;
              const full = tagLabel(tag);
              return (
                <button
                  key={tag.id}
                  type="button"
                  onClick={() => toggle(tag.id)}
                  aria-pressed={isActive}
                  title={full}
                  className={cn(PILL, isActive ? ACTIVE : IDLE)}
                >
                  <span className="max-w-[140px] truncate">{short}</span>
                </button>
              );
            })}
          </div>
        ) : null,
      )}
    </div>
  );
}
