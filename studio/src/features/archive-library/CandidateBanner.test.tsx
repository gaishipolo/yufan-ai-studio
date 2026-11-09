import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { CandidateList } from '@/shared/api/schema';
import { CandidateBanner } from './CandidateBanner';

/* 候选横幅的规矩都在这一层，所以用组件测试盯住：
   为 0 时消失、分组渲染、空格选择、A 采纳、D 弃用。
   其中"为 0 时消失"最容易被后来的人改成"留一个空框"，而那正是设计文档
   点名要避免的东西——空待办只是噪音。 */

const mock = vi.hoisted(() => ({
  data: undefined as CandidateList | undefined,
  pending: false,
  error: null as Error | null,
  refetch: vi.fn(),
  adopt: vi.fn(),
  adoptMany: vi.fn(),
  discard: vi.fn(),
}));

vi.mock('@/entities/asset/api/candidate.queries', () => ({
  useCandidates: () => ({
    data: mock.data,
    isPending: mock.pending,
    error: mock.error,
    refetch: mock.refetch,
  }),
  useAdoptCandidate: () => ({mutateAsync: mock.adopt, isPending: false}),
  useAdoptCandidates: () => ({mutateAsync: mock.adoptMany, isPending: false}),
  useDiscardCandidate: () => ({mutateAsync: mock.discard, isPending: false}),
}));

const candidate = (id: string, recipeId: string) => ({
  id,
  name: `候选 ${id}`,
  category: 'scene' as const,
  type: 'image' as const,
  mimeType: 'image/png',
  sizeBytes: 1024,
  path: `drafts/${id}.png`,
  sha256: 'sha',
  createdAt: '2026-11-08T02:00:00Z',
  url: `/api/v1/assets/${id}/file`,
  previewUrl: `/api/v1/assets/${id}/thumbnail`,
  origin: 'generate',
  stage: 'candidate',
  recipeId,
  complianceStatus: 'pending',
});

const oneGroup: CandidateList = {
  total: 2,
  groups: [
    {
      recipeId: 'R200',
      summary: 'Agnes Image 2.5 Flash · 2 张 · 1024×1536',
      promptSummary: '赛博朋克街头，霓虹灯牌',
      expiresAt: new Date(Date.now() + 3 * 86_400_000).toISOString(),
      items: [candidate('c1', 'R200'), candidate('c2', 'R200')],
    },
  ],
};

function app(permissions: string[] = ['asset:read', 'asset:update']) {
  return render(
    <AccessProvider subject={{id: 'u_001', name: '测试', roles: [], permissions}}>
      <CandidateBanner />
    </AccessProvider>,
  );
}

const listbox = () => screen.getByRole('listbox');

beforeEach(() => {
  mock.data = undefined;
  mock.pending = false;
  mock.error = null;
  mock.refetch.mockReset();
  mock.adopt.mockReset().mockResolvedValue({});
  mock.adoptMany.mockReset().mockResolvedValue({items: [], already: 0});
  mock.discard.mockReset().mockResolvedValue({});
});

describe('候选横幅', () => {
  it('候选为 0 时整段消失（空待办只是噪音）', () => {
    mock.data = {groups: [], total: 0};
    const {container} = app();
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('按批分组：组摘要与提示词摘要都在，同批两张挨着', () => {
    mock.data = oneGroup;
    app();
    expect(screen.getByText('Agnes Image 2.5 Flash · 2 张 · 1024×1536')).toBeInTheDocument();
    expect(screen.getByText('赛博朋克街头，霓虹灯牌')).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(2);
    expect(screen.getByText(/3 天后过期/)).toBeInTheDocument();
  });

  it('空格切换选中，采纳按钮的数量跟着变', () => {
    mock.data = oneGroup;
    app();
    // 初始高亮第一张：不选中时 A/D 作用于它，所以按钮已经说明"会动 1 张"
    expect(screen.getByRole('button', {name: '采纳 1 张'})).toBeInTheDocument();
    fireEvent.keyDown(listbox(), {key: ' '});
    fireEvent.keyDown(listbox(), {key: 'ArrowRight'});
    fireEvent.keyDown(listbox(), {key: ' '});
    expect(screen.getByRole('button', {name: '采纳 2 张'})).toBeInTheDocument();
  });

  it('未选中时按 A 采纳高亮的那一张（走单条接口）', async () => {
    mock.data = oneGroup;
    app();
    fireEvent.keyDown(listbox(), {key: 'a'});
    await waitFor(() => expect(mock.adopt).toHaveBeenCalledWith({assetId: 'c1'}));
    expect(mock.adoptMany).not.toHaveBeenCalled();
  });

  it('选中两张后按 A 走批量接口（全成或全不成）', async () => {
    mock.data = oneGroup;
    app();
    fireEvent.keyDown(listbox(), {key: ' '});
    fireEvent.keyDown(listbox(), {key: 'ArrowRight'});
    fireEvent.keyDown(listbox(), {key: ' '});
    fireEvent.keyDown(listbox(), {key: 'a'});
    await waitFor(() =>
      expect(mock.adoptMany).toHaveBeenCalledWith({assetIds: ['c1', 'c2']}),
    );
    expect(mock.adopt).not.toHaveBeenCalled();
  });

  it('按 D 弃用当前项', async () => {
    mock.data = oneGroup;
    app();
    fireEvent.keyDown(listbox(), {key: 'd'});
    await waitFor(() => expect(mock.discard).toHaveBeenCalledWith('c1'));
  });

  it('没有 asset:update 权限时整段不渲染（读取也不发）', () => {
    mock.data = oneGroup;
    const {container} = app(['asset:read']);
    expect(container).toBeEmptyDOMElement();
  });
});
