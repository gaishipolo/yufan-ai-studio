import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/shared/api/types';
import OpsPage from './OpsPage';

const { refreshMock } = vi.hoisted(() => ({ refreshMock: vi.fn() }));
vi.mock('@/entities/model/api/driver.api', () => ({
  driverApi: { refresh: refreshMock },
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OpsPage />
    </QueryClientProvider>,
  );
}

describe('运维页 · 刷新驱动目录', () => {
  beforeEach(() => refreshMock.mockReset());

  it('明说刷新是进程级的、改已加载文件要重启', () => {
    renderPage();
    expect(screen.getByText(/进程级/)).toBeInTheDocument();
    expect(screen.getByText(/改已加载的文件不热替换/)).toBeInTheDocument();
  });

  it('刷新成功：把本次结果摊开，不假装能查历史', async () => {
    const user = userEvent.setup();
    refreshMock.mockResolvedValue({
      added: ['new-provider'],
      kept: ['bailian', 'agnes'],
      changedFiles: ['drivers/agnes.py'],
      missingHandlers: [],
      errors: [],
      operator: 'u-1',
      refreshedAt: '2026-11-24T21:00:00+00:00',
    });
    renderPage();

    await user.click(screen.getByRole('button', { name: '刷新驱动目录' }));

    expect(await screen.findByRole('region', { name: '刷新结果' })).toBeInTheDocument();
    expect(screen.getByText('new-provider')).toBeInTheDocument();
    expect(screen.getByText('drivers/agnes.py')).toBeInTheDocument();
    expect(screen.getByText('无')).toBeInTheDocument(); // missingHandlers 为空
    expect(screen.queryByText(/刷新历史/)).not.toBeInTheDocument();
  });

  it('刷新失败：错误一直挂着，不清空上一次结果', async () => {
    const user = userEvent.setup();
    refreshMock.mockRejectedValueOnce(new ApiError('刷新被拒', 403));
    renderPage();

    await user.click(screen.getByRole('button', { name: '刷新驱动目录' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('刷新被拒');
    expect(screen.queryByRole('region', { name: '刷新结果' })).not.toBeInTheDocument();
  });
});
