import { RefreshCw } from 'lucide-react';
import { IconButton, SearchInput, Select, Switch } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { TaskScope, TaskSort } from '@/entities/task/model/types';
import type { TaskFeedSource } from '@/entities/task/model/feed.types';

/**
 * 任务中心的筛选条：来源 / 范围 / 排序 / 仅可下载 / 搜索 / 刷新，一行排完。
 *
 * 以前这些控件散在四个地方（页头 actions、说明段、单独一行来源、搜索行），
 * 从上到下要滑过四行才看到任务本身；而且「共 N 条」和页签的「全部 N」重复。
 * 现在只有一行：左边是"看什么"，右边是"多少条"。
 */

const SCOPE_OPTIONS = [
  {value: 'mine', label: '我的任务'},
  {value: 'all', label: '全部任务'},
  // 'team'（团队任务）：后端团队数据范围尚未实现（当前与 all 同义），实现后再放出
];

const SORT_OPTIONS = [
  {value: 'createdAt_desc', label: '最新优先'},
  {value: 'createdAt_asc', label: '最早优先'},
  {value: 'title_asc', label: '按名称'},
];

export interface TaskFilterBarProps {
  keyword: string;
  onKeywordChange: (value: string) => void;
  source: TaskFeedSource;
  onSourceChange: (value: TaskFeedSource) => void;
  canReadImages: boolean;
  scope: TaskScope;
  onScopeChange: (value: TaskScope) => void;
  /** 无 `task:read:all` 时后端会静默降级为「只看自己」，
   *  不收起范围选项的话，用户会以为「全部任务」里就是全站数据 */
  canReadAll: boolean;
  sort: TaskSort;
  onSortChange: (value: TaskSort) => void;
  onlyAuthorized: boolean;
  onAuthorizedChange: (value: boolean) => void;
  onRefresh: () => void;
  isFetching: boolean;
  /** 未知（请求失败）时传 undefined：显示"共 0 条"会让人以为数据被清空了 */
  total?: number;
  /** 当前**有没有筛选生效**。用于决定要不要摆「重置筛选」。 */
  filtered: boolean;
  /** 一键清空所有筛选条件。 */
  onReset: () => void;
}

export function TaskFilterBar({
  keyword,
  onKeywordChange,
  source,
  onSourceChange,
  canReadImages,
  scope,
  onScopeChange,
  canReadAll,
  sort,
  onSortChange,
  onlyAuthorized,
  onAuthorizedChange,
  onRefresh,
  isFetching,
  total,
  filtered,
  onReset,
}: TaskFilterBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SearchInput
        aria-label="搜索任务"
        placeholder="搜索名称 / 模型 / 任务 ID"
        value={keyword}
        onChange={event => onKeywordChange(event.target.value)}
        wrapperClassName="w-full sm:w-[260px]"
      />
      <Select
        aria-label="任务来源"
        value={source}
        onChange={event => onSourceChange(event.target.value as TaskFeedSource)}
        options={[
          {value: 'all', label: '全部来源'},
          /* 「通用视频」是**后端模块名**（feed_schema 里那一路叫通用视频任务），
             摆在下拉里使用者会以为它是某一种特定的产品，而不是「所有视频任务」。
             两个选项不在同一语义轴上 —— 「生图」是模态，「通用视频」是内部实现。
             改成平行命名：**视频** / **生图**。 */
          {value: 'video', label: '视频'},
          ...(canReadImages ? [{value: 'image', label: '生图'}] : []),
        ]}
      />
      {canReadAll && (
        <Select
          aria-label="任务范围"
          value={scope}
          onChange={event => onScopeChange(event.target.value as TaskScope)}
          options={SCOPE_OPTIONS}
        />
      )}
      <Select
        aria-label="排序"
        value={sort}
        onChange={event => onSortChange(event.target.value as TaskSort)}
        options={SORT_OPTIONS}
      />
      {/* 「仅可下载」是在说**权限**，而 `only_authorized` 实际过滤的是
          `downloadable`（后端定义：已成功且有产物）—— 是**状态**。
          一个产品里同时有「能不能下载」和「有没有成片」两件事，
          名字说成前者会把用户引到错误的方向（以为是权限不够）。
          改成按实际语义命名。 */}
      <Switch checked={onlyAuthorized} onChange={onAuthorizedChange}>
        只看有成片
      </Switch>

      <div className="ml-auto flex items-center gap-1.5">
        {/* 「重置筛选」只在**有筛选生效时**出现。
            之前空态写着「换个筛选条件或清空搜索关键字再试试」，
            但这一行有 5 个控件、**没有一键清空**的入口 ——
            说明文字在承诺一个不存在的功能。给一个真的按钮，比改文案准。 */}
        {filtered && (
          <button
            type="button"
            onClick={onReset}
            className="rounded-pill px-2 py-1 text-xs text-ink-mute transition-colors hover:bg-panel-soft hover:text-ink"
          >
            重置筛选
          </button>
        )}
        {/* 图标按钮而不是「刷新」两个字：这一行有 5 个控件，多两个字的宽度就被挤到下一行 */}
        <IconButton
          tone="soft"
          label="刷新列表"
          onClick={onRefresh}
          icon={<RefreshCw size={13} className={cn(isFetching && 'animate-spin')} />}
        />
        <span className="tabular-nums text-meta text-ink-mute">
          {total === undefined ? '共 — 条' : `共 ${total} 条`}
        </span>
      </div>
    </div>
  );
}
