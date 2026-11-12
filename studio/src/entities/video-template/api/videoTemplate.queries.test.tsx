import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { VideoTemplateListOut, VideoTemplateOut } from '@/shared/api/schema';
import { useUpdateVideoTemplatePrompt, videoTemplateKeys } from './videoTemplate.queries';

const mock = vi.hoisted(() => ({patch: vi.fn()}));
vi.mock('@/shared/api/http', () => ({api: {patch: mock.patch}}));

describe('保存封装提示词请求', () => {
  it('PATCH 原模板并立即更新缓存，不发送图片描述或改写正文', async () => {
    const client = new QueryClient({defaultOptions: {mutations: {retry: false}}});
    const previous = {id: 'template-1', prompt: '原文', updatedAt: '2026-11-12T00:00:00Z'} as VideoTemplateOut;
    const other = {...previous, id: 'other'};
    client.setQueryData<VideoTemplateListOut>(videoTemplateKeys.all, {items: [previous, other], total: 2});
    const raw = ' \n编辑 {{Mixed 1}}，参考 {{Mixed 3}}。\n\n ';
    const updated = {...previous, prompt: raw, updatedAt: '2026-11-12T01:00:00Z'};
    mock.patch.mockResolvedValueOnce(updated);
    const {result, unmount} = renderHook(() => useUpdateVideoTemplatePrompt(), {
      wrapper: ({children}: {children: ReactNode}) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    });
    await act(async () => {await result.current.mutateAsync({id: previous.id, prompt: raw});});
    expect(mock.patch).toHaveBeenCalledWith('/templates/video-templates/template-1/prompt', {prompt: raw});
    expect(client.getQueryData<VideoTemplateListOut>(videoTemplateKeys.all)?.items).toEqual([updated, other]);
    expect(client.getQueryState(videoTemplateKeys.all)?.isInvalidated).toBe(true);
    unmount();
    client.clear();
  });
});
