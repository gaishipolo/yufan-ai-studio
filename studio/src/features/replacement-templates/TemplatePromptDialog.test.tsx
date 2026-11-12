import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReplacementPack, VideoTemplateOut } from '@/shared/api/schema';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { TemplatePromptDialog } from './TemplatePromptDialog';
import { readPromptDom, setPromptSelection } from './promptEditorDom';

const mock = vi.hoisted(() => ({save: vi.fn(), upload: vi.fn()}));
vi.mock('@/entities/video-template/api/videoTemplate.queries', () => ({
  useUpdateVideoTemplatePrompt: () => ({mutateAsync: mock.save, isPending: false}),
}));
vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchivedMedia: () => ({data: undefined}),
  useArchiveUpload: () => ({mutateAsync: mock.upload}),
}));

const prompt = '  编辑 {{Mixed 1}}，参考 {{Mixed 2}}。\n\n';
const template: VideoTemplateOut = {
  id: 'template', templateKey: 'product', name: '测试产品模板', description: '', prompt,
  promptSha256: 'hash', sourceVideoName: '原片', sourceVideoAssetId: 'video',
  sourceVideoPath: '', sourceDurationSec: 5, aspectRatio: '9:16', packId: 'pack',
  packName: '测试产品', packVersion: 1, defaultKind: 'product',
  mediaOrder: [
    {token: '{{Mixed 1}}', type: 'video', role: 'source_video', name: '原片', inPrompt: true},
    {token: '{{Mixed 2}}', type: 'image', role: 'front', assetId: 'front', name: '正面图', inPrompt: true},
    {token: '{{Mixed 3}}', type: 'image', role: 'multiview', assetId: 'views', name: '三视图', inPrompt: false},
  ],
  generation: {}, rules: {referenceBindings: {'{{Mixed 1}}': 'source_video', '{{Mixed 2}}': 'front'}},
  coverUrl: '', pinned: false, createdAt: '2026-11-12T00:00:00Z', updatedAt: '2026-11-12T00:00:00Z',
};
const pack: ReplacementPack = {
  id: 'pack', name: '测试产品', kind: 'product', version: 1,
  referenceIds: ['front', 'views'], previewUrls: ['/front', '/views'],
  bindings: [{role: 'front', description: '这是不能自动加入提示词的图片说明'}, {role: 'multiview', description: '三视图详细说明'}],
};

function edit(input: HTMLElement, text: string, caret = text.length) {
  input.replaceChildren(document.createTextNode(text));
  setPromptSelection(input, {start: caret, end: caret});
  fireEvent.input(input);
}
function mount(permissions = ['*'], templates = [template], packs = [pack]) {
  const onClose = vi.fn();
  render(<AccessProvider subject={{id: 'user', name: '测试', roles: [], permissions}}>
    <TemplatePromptDialog templates={templates} packs={packs} onClose={onClose} />
  </AccessProvider>);
  return onClose;
}

beforeEach(() => {
  mock.save.mockReset();
  mock.upload.mockReset();
  mock.save.mockImplementation(async ({id, prompt: text}: {id: string; prompt: string}) => ({...template, id, prompt: text}));
});

describe('封装提示词编辑', () => {
  it('可按包名或图片文件名搜索全部已封装素材，选择后绑定来源并保存新引用', async () => {
    const other = {...pack, id: 'other', name: '海边场景包', kind: 'scene' as const,
      referenceIds: ['sea'], referenceNames: ['机位蓝图.png'],
      bindings: [{role: 'main'}], previewUrls: ['/sea']};
    mount(['*'], [template], [pack, other]);
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    fireEvent.click(screen.getByRole('button', {name: '插入引用 @'}));
    const search = screen.getByRole('textbox', {name: '搜索引用素材'});
    fireEvent.change(search, {target: {value: '机位蓝图'}});
    const option = screen.getByRole('option', {name: /机位蓝图/});
    expect(option).toHaveTextContent('海边场景包');
    fireEvent.click(option);
    const input = screen.getByRole('textbox', {name: '封装提示词原文'});
    expect(readPromptDom(input)).toBe(prompt + '{{Mixed 4}}');
    expect(within(input).getByRole('button', {name: '预览引用：机位蓝图.png'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '保存提示词'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledWith({id: 'template', prompt: prompt + '{{Mixed 4}}', references: [
      {token: '{{Mixed 4}}', packId: 'other', packVersion: 1, assetId: 'sea'},
    ]}));
  });
  it('正文直接显示缩略图引用标签，标签文字不进入保存原文', () => {
    mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const input = screen.getByRole('textbox', {name: '封装提示词原文'});
    expect(input).toHaveAttribute('contenteditable', 'true');
    expect(within(input).getByRole('button', {name: '预览引用：正面图'})).toHaveTextContent('图片 2');
    expect(within(input).getByRole('button', {name: '预览引用：原片'})).toHaveTextContent('视频 1');
    expect(readPromptDom(input)).toBe(prompt);
  });

  it('切换封装功能保留各自草稿，保存只更新当前功能', async () => {
    const second = {...template, id: 'other', name: '另一场景', prompt: '另一场景 {{Mixed 1}} {{Mixed 3}}'};
    mount(['*'], [template, second]);
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const firstDraft = '第一份草稿 {{Mixed 1}} {{Mixed 2}}\n';
    edit(screen.getByRole('textbox', {name: '封装提示词原文'}), firstDraft);
    fireEvent.change(screen.getByRole('combobox', {name: '查看封装功能'}), {target: {value: 'other'}});
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(second.prompt);
    const secondDraft = '第二份草稿 {{Mixed 1}} {{Mixed 3}}';
    edit(screen.getByRole('textbox', {name: '封装提示词原文'}), secondDraft);
    fireEvent.change(screen.getByRole('combobox', {name: '查看封装功能'}), {target: {value: 'template'}});
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(firstDraft);
    fireEvent.click(screen.getByRole('button', {name: '保存提示词'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledWith({id: 'template', prompt: firstDraft}));
    await screen.findByText('当前功能提示词已保存');
    fireEvent.change(screen.getByRole('combobox', {name: '查看封装功能'}), {target: {value: 'other'}});
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(secondDraft);
    expect(mock.save).toHaveBeenCalledTimes(1);
  });

  it('上传调用素材接口，结果按功能隔离且不自动写入提示词或生成引用', async () => {
    const asset = {id: 'uploaded', name: '上传正面图.png', url: '/uploaded', type: 'image', category: 'product'};
    mock.upload.mockResolvedValue(asset);
    mount(['*'], [template, {...template, id: 'other', name: '另一个功能'}]);
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const file = new File(['test'], '上传正面图.png', {type: 'image/png'});
    fireEvent.change(screen.getByLabelText('上传模板固定参考图'), {target: {files: [file]}});
    await vi.waitFor(() => expect(mock.upload).toHaveBeenCalledWith({file, category: 'product'}));
    expect(await screen.findByLabelText('新上传固定参考图')).toHaveTextContent('待接入生成');
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(prompt);
    expect(mock.save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('combobox', {name: '查看封装功能'}), {target: {value: 'other'}});
    expect(screen.queryByLabelText('新上传固定参考图')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {name: '查看封装功能'}), {target: {value: 'template'}});
    expect(screen.getByLabelText('新上传固定参考图')).toHaveTextContent(asset.name);
  });

  it('引用菜单可搜索和按类型筛选，搜索不改变正文', () => {
    mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    fireEvent.click(screen.getByRole('button', {name: '插入引用 @'}));
    const menu = screen.getByRole('dialog', {name: '插入引用素材'});
    fireEvent.change(within(menu).getByRole('textbox', {name: '搜索引用素材'}), {target: {value: '三视图'}});
    expect(within(menu).getAllByRole('option')).toHaveLength(1);
    expect(within(menu).getByRole('option')).toHaveTextContent('三视图');
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(prompt);
    fireEvent.change(within(menu).getByRole('textbox', {name: '搜索引用素材'}), {target: {value: ''}});
    fireEvent.click(within(menu).getByRole('button', {name: '视频', pressed: false}));
    expect(within(menu).getAllByRole('option')).toHaveLength(1);
    expect(within(menu).getByRole('option')).toHaveTextContent('原片');
  });

  it('菜单键盘导航遵循已引用优先的显示顺序', () => {
    mount(['*'], [{...template, prompt: '编辑 {{Mixed 1}}，参考 {{Mixed 3}}'}]);
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    fireEvent.click(screen.getByRole('button', {name: '插入引用 @'}));
    const menu = screen.getByRole('dialog', {name: '插入引用素材'});
    fireEvent.keyDown(within(menu).getByRole('textbox', {name: '搜索引用素材'}), {key: 'ArrowDown'});
    const rows = within(menu).getAllByRole('option');
    expect(rows[1]).toHaveTextContent('三视图');
    expect(rows[1]).toHaveAttribute('aria-selected', 'true');
  });
  it('保存用户原文，包括首尾空白，不附带图片描述', async () => {
    mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const input = screen.getByRole('textbox', {name: '封装提示词原文'});
    expect(readPromptDom(input)).toBe(prompt);
    const raw = ' \n修改 {{Mixed 1}}，参考 {{Mixed 3}}。\n\n ';
    edit(input, raw);
    fireEvent.click(screen.getByRole('button', {name: '保存提示词'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledWith({id: 'template', prompt: raw}));
    expect(await screen.findByText('当前功能提示词已保存')).toBeInTheDocument();
    expect(screen.getByText(raw, {selector: 'pre', exact: true, normalizer: text => text})).toBeInTheDocument();
  });

  it('输入 @ 搜索三视图，只在光标处插入标记，保留周围原文', async () => {
    mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const input = screen.getByRole('textbox', {name: '封装提示词原文'}) as HTMLDivElement;
    const raw = '  编辑 {{Mixed 1}}，参考 @三视图\n保持动作。\n';
    const caret = raw.indexOf('\n');
    edit(input, raw, caret);
    const list = screen.getByRole('listbox', {name: '选择引用素材'});
    fireEvent.click(within(list).getByRole('option', {name: /三视图/}));
    const expected = raw.replace('@三视图', '{{Mixed 3}}');
    expect(readPromptDom(input)).toBe(expected);
    fireEvent.click(screen.getByRole('button', {name: '保存提示词'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledWith({id: 'template', prompt: expected}));
  });

  it('保存失败保留草稿供重试；取消编辑不提交', async () => {
    mock.save.mockRejectedValue(new Error('保存失败，请重试'));
    mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const draft = '编辑 {{Mixed 1}}，参考 {{Mixed 3}}。';
    edit(screen.getByRole('textbox', {name: '封装提示词原文'}), draft);
    fireEvent.click(screen.getByRole('button', {name: '保存提示词'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败，请重试');
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(draft);
    mock.save.mockClear();
    fireEvent.click(screen.getByRole('button', {name: '结束编辑'}));
    fireEvent.click(screen.getByRole('button', {name: '放弃草稿并结束'}));
    expect(mock.save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(prompt);
  });

  it('只读账号不能编辑', () => {
    mount(['template:read']);
    expect(screen.queryByRole('button', {name: '编辑提示词'})).not.toBeInTheDocument();
  });

  it('引用越界时阻止保存并给出错误，编辑时仍能切换功能', () => {
    mount(['*'], [template, {...template, id: 'other', name: '另一个功能'}]);
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    expect(screen.getByRole('combobox', {name: '查看封装功能'})).toBeEnabled();
    edit(screen.getByRole('textbox', {name: '封装提示词原文'}), '编辑 {{Mixed 1}}，参考 {{Mixed 9}}。');
    fireEvent.click(screen.getByRole('button', {name: '保存提示词'}));
    expect(screen.getByRole('alert')).toHaveTextContent('引用');
    expect(mock.save).not.toHaveBeenCalled();
  });

  it('Escape 先收起候选，不关闭弹窗；中文输入期间不选择素材', () => {
    const onClose = mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const input = screen.getByRole('textbox', {name: '封装提示词原文'});
    edit(input, `${prompt}@`);
    expect(screen.getByRole('listbox', {name: '选择引用素材'})).toBeInTheDocument();
    fireEvent.keyDown(input, {key: 'Escape'});
    expect(screen.queryByRole('listbox', {name: '选择引用素材'})).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, {key: 'Enter', isComposing: true});
    expect(readPromptDom(input)).toBe(`${prompt}@`);
  });

  it('键盘选择可以替换选区里的旧引用，关闭编辑有放弃保护', () => {
    const onClose = mount();
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    const input = screen.getByRole('textbox', {name: '封装提示词原文'}) as HTMLDivElement;
    const start = prompt.indexOf('{{Mixed 2}}');
    setPromptSelection(input, {start, end: start + '{{Mixed 2}}'.length});
    fireEvent.mouseUp(input);
    fireEvent.click(screen.getByRole('button', {name: '插入引用 @'}));
    fireEvent.keyDown(input, {key: 'ArrowDown'});
    fireEvent.keyDown(input, {key: 'ArrowDown'});
    fireEvent.keyDown(input, {key: 'Enter'});
    expect(readPromptDom(input)).toBe(prompt.replace('{{Mixed 2}}', '{{Mixed 3}}'));
    fireEvent.click(screen.getByRole('button', {name: '关闭'}));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('尚未保存的草稿');
    fireEvent.click(screen.getByRole('button', {name: '继续编辑'}));
    expect(readPromptDom(input)).toBe(prompt.replace('{{Mixed 2}}', '{{Mixed 3}}'));
    expect(mock.save).not.toHaveBeenCalled();
  });

  it('文字场景编辑的是含参数的模板原文，而非当次场景描述', () => {
    const raw = '  编辑 {{Mixed 1}}，场景：{{ScenePrompt}}。\n';
    mount(['*'], [{...template, defaultKind: 'scene', prompt: raw, rules: {inputMode: 'video_scene_text', scenePromptDefault: '海边花园'}, mediaOrder: [template.mediaOrder![0]]}]);
    fireEvent.click(screen.getByRole('button', {name: '编辑提示词'}));
    expect(readPromptDom(screen.getByRole('textbox', {name: '封装提示词原文'}))).toBe(raw);
    fireEvent.click(screen.getByRole('button', {name: '插入引用 @'}));
    expect(within(screen.getByRole('listbox', {name: '选择引用素材'})).getAllByRole('option')).toHaveLength(3);
  });
});
