import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/shared/api/types';
import { ErrorState } from './ErrorState';

describe('ErrorState', () => {
  it('401 提示重新登录，而不是「没有数据」', () => {
    render(<ErrorState error={new ApiError('登录状态已失效', 40_100)} />);

    expect(screen.getByRole('alert')).toHaveTextContent('登录状态已失效');
    expect(screen.getByRole('alert')).not.toHaveTextContent('数据加载失败');
  });

  it('403 提示权限，不暴露后端措辞以外的猜测', () => {
    render(<ErrorState error={new ApiError('缺少 identity:read', 40_300)} />);

    expect(screen.getByText('没有查看权限')).toBeInTheDocument();
    expect(screen.getByText('缺少 identity:read')).toBeInTheDocument();
  });

  it('5xx 展示后端原文（便于截图给后端）', () => {
    render(<ErrorState error={new ApiError('数据库连接失败', 50_001)} />);

    expect(screen.getByText('服务端出错了')).toBeInTheDocument();
    expect(screen.getByText('数据库连接失败')).toBeInTheDocument();
  });

  it('网络错误说清楚是连不上服务', () => {
    render(<ErrorState error={new ApiError('Network Error', -1)} />);

    expect(screen.getByText('连接不上服务')).toBeInTheDocument();
  });

  it('非 ApiError 用调用方给的语境标题兜底', () => {
    render(<ErrorState error="boom" title="任务加载失败" />);

    expect(screen.getByText('任务加载失败')).toBeInTheDocument();
  });

  it('给 onRetry 才出现重试按钮，点击即回调', async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<ErrorState error={new ApiError('超时', 408)} onRetry={onRetry} />);

    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('没有 onRetry 时不渲染按钮（详情页由上层操作）', () => {
    render(<ErrorState error={new ApiError('超时', 408)} />);

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
