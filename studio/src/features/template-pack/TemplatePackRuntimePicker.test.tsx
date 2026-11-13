import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { TemplatePackCandidateListOut, TemplatePackOut } from '@/shared/api/schema';
import { TemplatePackRuntimePicker } from './TemplatePackRuntimePicker';

const mock = vi.hoisted(() => ({get: vi.fn(), post: vi.fn()}));
vi.mock('@/shared/api/http', () => ({api: {get: mock.get, post: mock.post}}));

const pack = {
  id: 'pack-1',
  templateKey: 'tp-1',
  name: '产品主图模板',
  prompt: '用 {{Mixed 2}} 做主图',
  slots: [
    {
      position: 1,
      token: '{{Mixed 1}}',
      slotKind: 'fixed',
      label: '参考视频',
      mediaType: 'video',
      assetId: 'vid-1',
    },
    {
      position: 2,
      token: '{{Mixed 2}}',
      slotKind: 'filtered',
      label: '产品主图',
      mediaType: 'image',
      filterKind: 'image',
      filterTagIds: ['tag-1'],
    },
  ],
} as unknown as TemplatePackOut;

const candidates = {
  token: '{{Mixed 2}}',
  label: '产品主图',
  filterKind: 'image',
  filterTagIds: ['tag-1'],
  items: [
    {assetId: 'a-1', name: '产品正面.png', kind: 'image', coverUrl: ''},
    {assetId: 'a-2', name: '产品侧面.png', kind: 'image', coverUrl: ''},
  ],
  total: 2,
} as unknown as TemplatePackCandidateListOut;

function renderWithClient(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {queries: {retry: false}, mutations: {retry: false}},
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

/**
 * 运行时选材。
 *
 * 守的是一件事：**候选池由服务端算，前端不自己筛**。
 *
 * 如果前端自己按标签筛一遍，筛选口径必然与服务端漂移 —— 而漂移的方向
 * 总是「多给权限」：用户能看到本不该出现在池子里的素材。
 */
describe('运行时选材', () => {
  it('候选池走服务端接口，前端不自己筛', async () => {
    mock.get.mockResolvedValueOnce(candidates);
    renderWithClient(
      <TemplatePackRuntimePicker pack={pack} picks={{}} onChange={() => {}} />,
    );

    await waitFor(() => {
      expect(mock.get).toHaveBeenCalledWith(
        '/template-packs/pack-1/candidates',
        {params: {token: '{{Mixed 2}}', limit: 100}},
      );
    });
    expect(await screen.findByText('产品正面.png')).toBeTruthy();
  });

  it('固定槽位不出现在选材列表里', async () => {
    mock.get.mockResolvedValueOnce(candidates);
    renderWithClient(
      <TemplatePackRuntimePicker pack={pack} picks={{}} onChange={() => {}} />,
    );

    await waitFor(() => expect(mock.get).toHaveBeenCalled());
    /* 「参考视频」是固定槽位，用户不能换 —— 不该出现在选择列表里。 */
    expect(screen.queryByText('参考视频')).toBeNull();
  });

  it('选素材后回调带上槽位标识', async () => {
    mock.get.mockResolvedValueOnce(candidates);
    const onChange = vi.fn();
    renderWithClient(
      <TemplatePackRuntimePicker pack={pack} picks={{}} onChange={onChange} />,
    );

    const option = await screen.findByText('产品正面.png');
    await userEvent.click(option);
    expect(onChange).toHaveBeenCalledWith({'{{Mixed 2}}': 'a-1'});
  });

  it('池子为空时说明原因，而不是空白', async () => {
    mock.get.mockResolvedValueOnce({
      token: '{{Mixed 2}}',
      label: '产品主图',
      filterKind: 'image',
      filterTagIds: ['tag-1'],
      items: [],
      total: 0,
    });
    renderWithClient(
      <TemplatePackRuntimePicker pack={pack} picks={{}} onChange={() => {}} />,
    );

    expect(await screen.findByText(/没有符合条件的素材/)).toBeTruthy();
  });

  it('没有筛选槽位时说明「不需要选」', () => {
    const fixedOnly = {
      ...pack,
      slots: [pack.slots![0]],
    } as unknown as TemplatePackOut;
    renderWithClient(
      <TemplatePackRuntimePicker
        pack={fixedOnly}
        picks={{}}
        onChange={() => {}}
      />,
    );

    expect(screen.getByText(/没有需要你选的槽位/)).toBeTruthy();
    expect(mock.get).not.toHaveBeenCalled();
  });
});
