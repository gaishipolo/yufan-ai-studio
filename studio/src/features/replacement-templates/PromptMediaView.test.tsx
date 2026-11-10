import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PromptMediaView, MediaGallery, type PromptMediaItem } from './PromptMediaView';

vi.mock('@/entities/asset/ui/ArchivedThumbnail', () => ({
  ArchivedThumbnail: ({url}: {url?: string}) => <img src={url} alt="媒体缩略图" />,
}));
vi.mock('@/entities/asset/ui/ArchivedMedia', () => ({
  ArchivedMedia: ({url, name}: {url: string; name: string}) => <div data-testid="preview" data-url={url}>{name}</div>,
}));
const media: PromptMediaItem[] = [
  {id: 'v', token: '{{Mixed 1}}', name: '原片.mp4', type: 'video', url: '/snapshot/video', previewUrl: '/snapshot/video/thumbnail'},
  {id: 'views', token: '{{Mixed 2}}', name: 'T35黑色三视图.png', type: 'image', url: '/snapshot/views', usage: '多视结构'},
  {id: 'front', token: '{{Mixed 3}}', name: 'T35黑色原.jpg', type: 'image', url: '/snapshot/front'},
  {id: 'detail', token: '{{Mixed 4}}', name: 'T35产品图.png', type: 'image', url: '/snapshot/detail'},
];

describe('提示词媒体核对', () => {
  it('百炼按类型编号可打开媒体，不把图10误认成图1', () => {
    render(<PromptMediaView prompt='编辑视频1，以图10为准，参考图1。' media={[
      {...media[0], token: '视频1'}, {...media[1], token: '图1'}, {...media[2], token: '图10'},
    ]} />);
    expect(screen.getAllByRole('button', {name: '查看 图1：T35黑色三视图.png'})).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', {name: '查看 图10：T35黑色原.jpg'}));
    expect(screen.getByTestId('preview')).toHaveAttribute('data-url', '/snapshot/front');
  });
  it('正文引用显示对应缩略图，点按查看确切的当次输入，原文仍可见', () => {
    const prompt = '编辑 {{Mixed 1}}，以 {{Mixed 2}} 为准。';
    render(<PromptMediaView prompt={prompt} media={media} />);
    fireEvent.click(screen.getByRole('button', {name: '查看 {{Mixed 2}}：T35黑色三视图.png'}));
    expect(screen.getByTestId('preview')).toHaveAttribute('data-url', '/snapshot/views');
    expect(screen.getByText(prompt)).toBeInTheDocument();
    expect(screen.getAllByRole('img')[0]).toHaveAttribute('src', '/snapshot/video/thumbnail');
  });

  it('完整输入图库列出三张图片，区分正文引用和仅传入的图片', () => {
    render(<MediaGallery prompt='{{Mixed 1}} {{Mixed 2}}' media={media} />);
    expect(screen.getByText('1 个视频 · 3 张图片')).toBeVisible();
    for (const item of media) expect(screen.getByText(item.name)).toBeVisible();
    expect(screen.getAllByText('已传入，正文未点名')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', {name: '查看 {{Mixed 4}}：T35产品图.png'}));
    expect(screen.getByTestId('preview')).toHaveAttribute('data-url', '/snapshot/detail');
  });

  it('未知引用明确显示未记录，不把第一张图错配过去', () => {
    render(<PromptMediaView prompt='查看 {{Mixed 9}}' media={media} />);
    expect(screen.getByText('{{Mixed 9}} · 媒体未记录')).toBeVisible();
    expect(screen.queryByRole('button', {name: /查看.*Mixed 9/})).not.toBeInTheDocument();
  });
});
