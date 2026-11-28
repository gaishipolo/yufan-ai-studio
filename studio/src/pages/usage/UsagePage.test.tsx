import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type {
  UsageOverviewOut,
  UsageRecordOut,
} from '@/entities/usage/model/types';
import type { UsageOverviewParams, UsageRecordsParams } from '@/entities/usage/api/usage.api';
import UsagePage from './UsagePage';

const mock = vi.hoisted(() => ({
  overviewParams: {} as UsageOverviewParams,
  recordsParams: {} as UsageRecordsParams,
  overviewEnabled: true,
  recordsEnabled: true,
  overview: {} as unknown,
  records: {} as unknown,
}));

vi.mock('@/entities/usage/api/usage.queries', () => ({
  useUsageOverview: (params: UsageOverviewParams, enabled = true) => {
    mock.overviewParams = params;
    mock.overviewEnabled = enabled;
    return mock.overview;
  },
  useUsageRecords: (params: UsageRecordsParams, enabled = true) => {
    mock.recordsParams = params;
    mock.recordsEnabled = enabled;
    return mock.records;
  },
}));

function overviewResult(overrides: Partial<UsageOverviewOut> = {}): UsageOverviewOut {
  return {
    summary: {
      start: '2026-11-01T00:00:00Z',
      end: '2026-11-30T23:59:59Z',
      granularity: 'day',
      scope: 'mine',
      total: 10,
      succeeded: 7,
      failed: 2,
      pending: 1,
      successRate: 0.7,
      quantity: 12,
      durationSec: 95,
      avgDurationSec: 13.5,
      sources: [{source: 'video', total: 6, succeeded: 4, failed: 1, pending: 1, quantity: 4, durationSec: 60}],
    },
    trend: [
      {bucket: '2026-11-01', total: 4, succeeded: 3, failed: 1, pending: 0, quantity: 3, durationSec: 40},
      {bucket: '2026-11-02', total: 6, succeeded: 4, failed: 1, pending: 1, quantity: 9, durationSec: 55},
    ],
    breakdown: [
      {dimension: 'model', key: 'seedance', label: 'Seedance 2.5', total: 6, quantity: 4, durationSec: 60, share: 0.6},
      {dimension: 'kind', key: 't2v', label: '文生视频', total: 10, quantity: 12, durationSec: 95, share: 1},
      {dimension: 'owner', key: 'u_1', label: '我', total: 10, quantity: 12, durationSec: 95, share: 1},
      {dimension: 'status', key: 'succeeded', label: '已产出', total: 7, quantity: 12, durationSec: 95, share: 0.7},
    ],
    templateUsers: [
      {
        templateKey: 'tpl-a',
        templateName: '模板 A',
        ownerId: 'u_1',
        ownerName: '我',
        total: 6,
        succeeded: 4,
        quantity: 4,
      },
      {
        templateKey: 'tpl-a',
        templateName: '模板 A',
        ownerId: 'u_2',
        ownerName: '同事',
        total: 3,
        succeeded: 2,
        quantity: 2,
      },
      {
        templateKey: 'tpl-b',
        templateName: '模板 B',
        ownerId: 'u_1',
        ownerName: '我',
        total: 1,
        succeeded: 1,
        quantity: 1,
      },
    ],
    sources: ['video'],
    ...overrides,
  };
}

const record: UsageRecordOut = {
  id: 'task1',
  source: 'video',
  title: '测试视频',
  status: 'completed',
  modelId: 'seedance-2.5',
  modelName: 'Seedance 2.5',
  quantity: 1,
  durationSec: 12,
  createdAt: '2026-11-20T02:00:00Z',
  ownerId: 'u_1',
  ownerName: '我',
  detailUrl: '/tasks?source=video&keyword=task1',
};

function App({
  permissions = ['usage:read'],
  entry = '/usage',
}: {
  permissions?: string[];
  entry?: string;
}) {
  return (
    <MemoryRouter initialEntries={[entry]}>
      <AccessProvider subject={{id: 'u_1', name: '我', roles: [], permissions}}>
        <UsagePage />
      </AccessProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  mock.overviewEnabled = true;
  mock.recordsEnabled = true;
  mock.overview = {
    data: overviewResult(),
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
  mock.records = {
    data: {items: [record], total: 1, page: 1, pageSize: 20},
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
});

describe('用量看板页面', () => {
  it('概览展示汇总、趋势与构成', () => {
    render(<App />);
    /* 主卡标签是 <p>：图例里也有一个「已产出」，按标签选才不会撞上两个匹配 */
    expect(screen.getByText('已产出', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('成功率')).toBeInTheDocument();
    expect(screen.getByText('70.0%')).toBeInTheDocument();
    expect(screen.getByText('Seedance 2.5')).toBeInTheDocument();
    expect(screen.getByText('模板 × 使用人')).toBeInTheDocument();
    expect(screen.getByText('模板 A')).toBeInTheDocument();
    expect(screen.getByText('同事')).toBeInTheDocument();
  });

  it('没接数据来源时给出提示，而不是假装用量为零', () => {
    mock.overview = {
      data: overviewResult({sources: []}),
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
    render(<App />);
    // 提示用的是 `status` 而不是 `alert`：它不打断读屏当前朗读
    expect(screen.getByRole('status')).toHaveTextContent('暂无可用的数据来源');
  });

  it('明细页签只请求明细，不重复请求概览', () => {
    render(<App />);
    expect(mock.recordsEnabled).toBe(false);

    fireEvent.click(screen.getByRole('button', {name: '明细'}));
    expect(mock.recordsEnabled).toBe(true);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('测试视频')).toBeInTheDocument();
  });

  it('没有 usage:read:all 时「全部成员」不可选', () => {
    render(<App />);
    const option = screen.getByRole('radio', {name: '全部成员'});
    expect(option).toBeDisabled();
    expect(option).toHaveAttribute('title', '需要「查看全部用量」权限');
  });

  it('有 usage:read:all 时「全部成员」可选，并按全部范围请求', () => {
    render(<App permissions={['usage:read', 'usage:read:all']} />);
    const option = screen.getByRole('radio', {name: '全部成员'});
    expect(option).not.toBeDisabled();

    fireEvent.click(option);
    expect(mock.overviewParams.scope).toBe('all');
  });

  it('URL 里带着 scope=all 但没有权限时降级成 mine', () => {
    render(<App entry="/usage?scope=all" />);
    // 后端也会降级；界面不能显示「全部成员」却只给别人的空数据
    expect(mock.overviewParams.scope).toBe('mine');
  });

  it('换区间会带上新的起止日期，并回到第 1 页', () => {
    render(<App entry="/usage?page=3" />);
    expect(mock.recordsParams.page).toBe(3);

    fireEvent.click(screen.getByRole('radio', {name: '近 7 天'}));
    expect(mock.overviewParams.start).not.toBe(mock.overviewParams.end);
    expect(mock.recordsParams.page).toBe(1);
  });

  it('粒度只影响趋势，明细不关心', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('radio', {name: '按周'}));
    expect(mock.overviewParams.granularity).toBe('week');
    expect(mock.recordsParams).not.toHaveProperty('granularity');
  });
});
