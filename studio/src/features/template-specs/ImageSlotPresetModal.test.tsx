import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from '@/shared/lib/toast';
import { ImageSlotPresetModal } from './ImageSlotPresetModal';
import { loadPresets, SELF_REF } from './imageSlotPresets';

vi.mock('@/shared/lib/toast', () => ({ toast: vi.fn() }));

beforeEach(() => {
  localStorage.clear();
  vi.mocked(toast).mockClear();
});

describe('图片插槽配置弹窗', () => {
  it('打开时列出已存预设的名称与文案', () => {
    localStorage.setItem(
      'sf_image_slot_presets',
      JSON.stringify([{id: 'p1', name: '场景参考', filledText: `参考 ${SELF_REF}`}]),
    );
    render(<ImageSlotPresetModal open onClose={vi.fn()} />);

    expect(screen.getByLabelText('预设 1 名称')).toHaveValue('场景参考');
    expect(screen.getByLabelText('预设 1 选择素材时文案')).toHaveValue('参考 {{slot:key}}');
  });

  it('改名与改文案即时落盘，不用点保存', async () => {
    const user = userEvent.setup();
    render(<ImageSlotPresetModal open onClose={vi.fn()} />);

    const name = screen.getAllByLabelText(/名称$/)[0];
    await user.clear(name);
    await user.type(name, '我的场景');
    const text = screen.getAllByLabelText(/选择素材时文案$/)[0];
    await user.clear(text);
    await user.type(text, '场景以图为准');

    await waitFor(() => expect(loadPresets()[0].name).toBe('我的场景'));
    expect(loadPresets()[0].filledText).toBe('场景以图为准');
  });

  it('「插引用」把占位符写进文案光标处', async () => {
    const user = userEvent.setup();
    render(<ImageSlotPresetModal open onClose={vi.fn()} />);

    const text = screen.getAllByLabelText(/选择素材时文案$/)[0];
    await user.clear(text);
    await user.type(text, '参考');
    await user.click(screen.getAllByRole('button', {name: '插引用（这次选中的图）'})[0]);

    expect(text).toHaveValue('参考{{slot:key}}');
  });

  it('加一个预设 / 删一个预设都即时落盘', async () => {
    const user = userEvent.setup();
    render(<ImageSlotPresetModal open onClose={vi.fn()} />);

    const before = loadPresets().length;
    await user.click(screen.getByRole('button', {name: '加一个预设'}));
    await waitFor(() => expect(loadPresets()).toHaveLength(before + 1));

    await user.click(screen.getByRole('button', {name: `删除预设 ${before + 1}`}));
    await waitFor(() => expect(loadPresets()).toHaveLength(before));
  });

  it('删空后再打开不会把示例灌回来', async () => {
    const user = userEvent.setup();
    const view = render(<ImageSlotPresetModal open onClose={vi.fn()} />);
    for (let count = loadPresets().length; count > 0; count -= 1) {
      await user.click(screen.getByRole('button', {name: `删除预设 ${count}`}));
    }
    expect(loadPresets()).toEqual([]);

    view.rerender(<ImageSlotPresetModal open={false} onClose={vi.fn()} />);
    view.rerender(<ImageSlotPresetModal open onClose={vi.fn()} />);
    expect(loadPresets()).toEqual([]);
    expect(screen.getByText(/还没有预设/)).toBeInTheDocument();
  });

  it('关闭不丢改动：关掉后本地存储里仍是刚改的内容', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<ImageSlotPresetModal open onClose={onClose} />);

    const name = screen.getAllByLabelText(/名称$/)[0];
    await user.clear(name);
    await user.type(name, '改过的名字');
    fireEvent.click(screen.getByRole('button', {name: '完成'}));

    expect(onClose).toHaveBeenCalled();
    expect(loadPresets()[0].name).toBe('改过的名字');
  });
});
