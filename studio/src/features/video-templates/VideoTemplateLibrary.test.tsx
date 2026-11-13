import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { VideoTemplateOut } from '@/shared/api/schema';
import { VideoTemplateLibrary } from './VideoTemplateLibrary';

const mock = vi.hoisted(() => {
  const template: VideoTemplateOut = {
    id: 'a'.repeat(32),
    templateKey: 'milan_night_3030_pink',
    name: '米兰之夜 · 3030 粉色文胸局部替换',
    description: '固定样片 + 固定提示词 + 3 张产品图',
    prompt: '对输入的原视频 {{Mixed 1}} 进行替换，目标外观由 {{Mixed 3}} 决定。',
    promptSha256: 'abc123',
    sourceVideoName: '我穿的是米兰之夜模板测试.mp4',
    sourceVideoAssetId: 'ac1d588d36294fa49c809d46bb1dbb6d',
    sourceVideoPath: '',
    sourceDurationSec: 5.312,
    aspectRatio: '9:16',
    packId: 'b'.repeat(32),
    packName: '3030 粉色',
    packVersion: 2,
    defaultKind: 'product',
    mediaOrder: [
      {token: '{{Mixed 1}}', type: 'video', role: 'source_video', name: '样片.mp4', inPrompt: true},
      {token: '{{Mixed 2}}', type: 'image', role: 'front', name: '3030粉色.png', inPrompt: false},
      {token: '{{Mixed 3}}', type: 'image', role: 'multiview', name: '3030三视图粉产品-1.png', inPrompt: true},
      {token: '{{Mixed 4}}', type: 'image', role: 'detail', name: '3030粉产品细节图.png', inPrompt: false},
    ],
    generation: {duration: 5},
    rules: {},
    coverUrl: '',
    pinned: true,
    createdAt: '2026-11-09T07:00:00Z',
    updatedAt: '2026-11-09T07:00:00Z',
  };
  return {template, create: vi.fn().mockResolvedValue(template)};
});

vi.mock('@/entities/video-template/api/videoTemplate.queries', () => ({
  useVideoTemplates: () => ({
    data: {items: [mock.template], total: 1, pinnedId: mock.template.id},
    isPending: false, error: null, refetch: vi.fn(),
  }),
  useVideoTemplateMutations: () => ({
    create: {mutateAsync: mock.create, isPending: false},
    pin: {mutateAsync: vi.fn(), isPending: false},
  }),
}));

vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveAssets: () => ({data: {items: [
    {id: 'ac1d588d36294fa49c809d46bb1dbb6d', name: '我穿的是米兰之夜模板测试.mp4',
      category: 'source', type: 'video', duration: 5.31, hasAudio: true},
  ], total: 1}}),
}));

vi.mock('@/entities/template/api/pack.queries', () => ({
  useAvailablePacks: () => ({
    data: {items: [{id: mock.template.packId, name: '3030 粉色', version: 2, referenceIds: ['1', '2', '3']}], total: 1},
    isPending: false, error: null,
  }),
}));

describe('模板库里的视频模板', () => {
  it.each([1, 3])('复制用途绑定蓝本（原片槽位 %s）由服务端生成清单', async sourceSlot => {
    const previous = mock.template;
    mock.template = {...previous, ...(sourceSlot === 3 ? {prompt: '编辑 {{Mixed 3}}，参考 {{Mixed 2}}。'} : {}), rules: {referenceBindings: sourceSlot === 3 ? {
      '{{Mixed 1}}': 'front', '{{Mixed 2}}': 'multiview', '{{Mixed 3}}': 'source_video',
    } : {
      '{{Mixed 1}}': 'source_video', '{{Mixed 2}}': 'front', '{{Mixed 3}}': 'multiview',
    }}};
    mock.create.mockClear();
    try {
      render(<VideoTemplateLibrary />);
      fireEvent.click(screen.getByRole('button', {name: '复制固定蓝本'}));
      fireEvent.change(screen.getByLabelText('替换素材包'), {target: {value: mock.template.packId}});
      fireEvent.click(screen.getByRole('button', {name: '保存封装'}));
      await vi.waitFor(() => expect(mock.create).toHaveBeenCalledWith(expect.objectContaining({
        mediaOrder: [], rules: expect.objectContaining({referenceBindings: mock.template.rules?.referenceBindings}),
      })));
    } finally {mock.template = previous;}
  });
  it('文字换场景蓝本可复制保存，不要求素材包或参考图', async () => {
    const previous = mock.template;
    mock.template = {...previous, defaultKind: 'scene', packId: null, packName: '', packVersion: 0,
      prompt: '{{ScenePrompt}}\n\n保留人物与产品。', mediaOrder: previous.mediaOrder?.slice(0, 1),
      rules: {inputMode: 'video_scene_text', scenePromptDefault: '将场景替换为酒店会客厅。'}};
    mock.create.mockClear();
    try {
      render(<VideoTemplateLibrary />);
      fireEvent.click(screen.getByRole('button', {name: '复制固定蓝本'}));
      expect(screen.getByLabelText('默认场景描述')).toHaveValue('将场景替换为酒店会客厅。');
      expect(screen.queryByLabelText('替换素材包')).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText('默认场景描述'), {target: {value: '将场景替换为海边花园。'}});
      fireEvent.click(screen.getByRole('button', {name: '保存封装'}));
      await vi.waitFor(() => expect(mock.create).toHaveBeenCalledWith(expect.objectContaining({
        packId: null, mediaOrder: [], defaultKind: 'scene',
        rules: expect.objectContaining({inputMode: 'video_scene_text', scenePromptDefault: '将场景替换为海边花园。'}),
      })));
    } finally {mock.template = previous;}
  });

  it('显示固定蓝本与提示词引用的编号（三视图是 Mixed 3）', () => {
    render(<VideoTemplateLibrary />);
    expect(screen.getByText('米兰之夜 · 3030 粉色文胸局部替换')).toBeVisible();
    expect(screen.getByText('固定蓝本')).toBeVisible();
    expect(screen.getByText(/提示词引用：/)).toHaveTextContent('{{Mixed 1}}、{{Mixed 3}}');
    expect(screen.getByText(/3030 粉色 v2/)).toBeVisible();
  });

  it('展开后按素材包原顺序列出 4 项媒体，未引用的图只插入接口', () => {
    render(<VideoTemplateLibrary />);
    fireEvent.click(screen.getByRole('button', {name: '查看提示词与顺序'}));
    expect(screen.getByText('3030三视图粉产品-1.png')).toBeVisible();
    expect(screen.getByText('3030粉色.png')).toBeVisible();
    expect(screen.getByText('3030粉产品细节图.png')).toBeVisible();
    expect(screen.getAllByText('仅插入接口')).toHaveLength(2);
    expect(screen.getAllByText('提示词引用')).toHaveLength(2);
  });

  it('复制固定蓝本会带出原提示词，名称标为副本', () => {
    render(<VideoTemplateLibrary />);
    fireEvent.click(screen.getByRole('button', {name: '复制固定蓝本'}));
    expect(screen.getByDisplayValue('米兰之夜 · 3030 粉色文胸局部替换 · 副本')).toBeVisible();
    expect(screen.getByDisplayValue(mock.template.prompt)).toBeVisible();
  });

  it('卡片标明已绑定原片，复制封装时沿用原片与默认产品替换', () => {
    render(<VideoTemplateLibrary />);
    expect(screen.getByText(/已绑定原片卡片 · 默认产品替换/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '复制固定蓝本'}));
    expect(screen.getByLabelText(/样片原片/)).toHaveValue('ac1d588d36294fa49c809d46bb1dbb6d');
    expect(screen.getByLabelText('默认替换类型')).toHaveValue('product');
  });

  it('封装编辑器保存功能名称及提示词范围参数', async () => {
    render(<VideoTemplateLibrary />);
    fireEvent.click(screen.getByRole('button', {name: '复制固定蓝本'}));
    fireEvent.change(screen.getByLabelText('替换素材包'), {target: {value: mock.template.packId}});
    fireEvent.change(screen.getByLabelText('工作台功能名称'), {target: {value: '外穿替换'}});
    fireEvent.change(screen.getByLabelText(/替换范围选项/), {target: {value: '仅前景\n全片'}});
    fireEvent.change(screen.getByDisplayValue(mock.template.prompt), {
      target: {value: mock.template.prompt + '范围：{{ReplacementScope}}'},
    });
    expect(screen.getByLabelText('样片时长（秒）')).toBeValid();
    fireEvent.click(screen.getByRole('button', {name: '保存封装'}));
    await vi.waitFor(() => expect(mock.create).toHaveBeenCalledWith(expect.objectContaining({
      rules: {workbenchLabel: '外穿替换', scopeOptions: ['仅前景', '全片']},
    })));
  });
});

describe('用途槽位', () => {
  it('可以从零声明槽位、加/删槽位，不绑素材包也能保存', async () => {
    mock.create.mockClear();
    render(<VideoTemplateLibrary />);
    fireEvent.click(screen.getByRole('button', {name: '新建封装'}));
    fireEvent.click(screen.getByRole('button', {name: '改为按用途声明槽位'}));
    expect(screen.getByLabelText('{{Mixed 2}} 用途')).toHaveValue('front');

    fireEvent.click(screen.getByRole('button', {name: '加一个用途槽位'}));
    fireEvent.change(screen.getByLabelText('{{Mixed 3}} 用途'), {target: {value: 'multiview'}});
    fireEvent.click(screen.getByRole('button', {name: '加一个用途槽位'}));
    // 删掉中间那个不会留空洞：编号按剩余用途重排
    fireEvent.click(screen.getByLabelText('删除 {{Mixed 4}}'));

    fireEvent.change(screen.getByLabelText('模板名称'), {target: {value: '只声明槽位'}});
    fireEvent.change(screen.getByLabelText(/样片原片/), {target: {value: 'ac1d588d36294fa49c809d46bb1dbb6d'}});
    fireEvent.change(screen.getByLabelText(/固定提示词/), {target: {value: '对 {{Mixed 1}} 替换，参考 {{Mixed 3}}。'}});
    fireEvent.click(screen.getByRole('button', {name: '保存封装'}));

    await vi.waitFor(() => expect(mock.create).toHaveBeenCalledWith(expect.objectContaining({
      packId: null,
      mediaOrder: [],
      rules: expect.objectContaining({
        referenceBindings: {
          '{{Mixed 1}}': 'source_video',
          '{{Mixed 2}}': 'front',
          '{{Mixed 3}}': 'multiview',
        },
      }),
    })));
  });
});
