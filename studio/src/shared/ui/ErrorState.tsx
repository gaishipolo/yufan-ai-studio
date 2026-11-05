import type { ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { ApiError, errorHttpStatus } from '@/shared/api/types';
import { Button } from './Button';

/* ------------------------------------------------------------------
 * 列表 / 详情的失败态
 *
 * 存在的理由是一条真实事故链：`data === undefined` 时页面统一走「空数据」分支，
 * 于是 401、500、网络断开、真的没数据，在界面上长得一模一样——
 * 用户看到「你没有任务」，会以为数据丢了，而不是「请你重新登录」。
 *
 * 错误分类复用 `errorHttpStatus`（`code // 100` 即 HTTP 状态），
 * 只按状态段给标题，具体原因仍展示后端 message，不替后端编故事。
 * ------------------------------------------------------------------ */

/** 默认标题；页面可以按语境覆盖（「任务加载失败」比「数据加载失败」更好懂） */
const DEFAULT_TITLE = '数据加载失败';

function describe(error: unknown, fallbackTitle: string): { title: string; detail: string } {
  if (!(error instanceof ApiError)) {
    return { title: fallbackTitle, detail: '请稍后重试；若持续失败请联系管理员。' };
  }

  const status = errorHttpStatus(error.code);

  if (status === 401) {
    // 正常情况下 401 已经被 http 拦截器清场并送去登录页，走到这里只是兜底
    return { title: '登录状态已失效', detail: '请重新登录后再试。' };
  }
  if (status === 403) {
    return { title: '没有查看权限', detail: error.message || '请联系管理员分配对应权限。' };
  }
  if (status === 408) {
    return { title: '请求超时', detail: '服务响应较慢，请稍后重试。' };
  }
  if (status === -1) {
    return { title: '连接不上服务', detail: '请检查网络，或确认后端服务已启动。' };
  }
  if (status >= 500) {
    return { title: '服务端出错了', detail: error.message || '请稍后重试；若持续失败请联系管理员。' };
  }
  return { title: fallbackTitle, detail: error.message || '请稍后重试。' };
}

export interface ErrorStateProps {
  error: unknown;
  /** 重试入口；不传则不显示按钮（例如详情页由上层提供操作） */
  onRetry?: () => void;
  /** 语境化标题 */
  title?: string;
  icon?: ReactNode;
  className?: string;
}

export function ErrorState({ error, onRetry, title, icon, className }: ErrorStateProps) {
  const described = describe(error, title ?? DEFAULT_TITLE);

  return (
    <div
      role="alert"
      className={
        className ?? 'flex flex-col items-center justify-center px-5 py-16 text-center'
      }
    >
      <div className="mb-3 text-[#f5b3ae]">
        {icon ?? <AlertTriangle size={38} strokeWidth={1.4} aria-hidden />}
      </div>
      <p className="text-[13px] font-medium text-ink-soft">{described.title}</p>
      <p className="mt-1.5 max-w-sm text-xs leading-[1.6] text-ink-mute">{described.detail}</p>
      {onRetry && (
        <Button
          size="sm"
          className="mt-4"
          icon={<RefreshCw size={12} aria-hidden />}
          onClick={onRetry}
        >
          重试
        </Button>
      )}
    </div>
  );
}
