import { RefreshCw } from 'lucide-react';
import { Button, SearchInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

export interface TaskSearchRowProps {
  keyword: string;
  onKeywordChange: (value: string) => void;
  onRefresh: () => void;
  isFetching: boolean;
  total: number;
}

export function TaskSearchRow({
  keyword,
  onKeywordChange,
  onRefresh,
  isFetching,
  total,
}: TaskSearchRowProps) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <SearchInput
        placeholder="搜索任务名称 / 模型 / 任务 ID"
        value={keyword}
        onChange={(e) => onKeywordChange(e.target.value)}
        wrapperClassName="w-full sm:w-auto sm:min-w-[280px]"
      />
      <Button size="sm" icon={<RefreshCw size={13} className={cn(isFetching && 'animate-spin')} />} onClick={onRefresh}>
        刷新
      </Button>
      <span className="ml-auto text-[11.5px] text-ink-mute">共 {total} 条任务</span>
    </div>
  );
}
