/**
 * 参考图投放区：三种来源各走各的路，别互相抢。
 *
 * 钉住两件事：
 *
 * 1. **从桌面拖文件不能让页面跳走**。不 `preventDefault` 的话浏览器按默认动作
 *    打开 `file:///…`，整页导航，未提交的提示词/参数/已选参考图全丢；
 * 2. **只认素材库的自定义 MIME**，不能拿 `text/plain` 兜底，否则拖一段文字
 *    也会被当成一张素材。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LIBRARY_ASSET_DRAG_TYPE } from '@/shared/lib/libraryDrag';
import { ReferenceImages } from './ReferenceImages';

function fakeDataTransfer(options: {
  types: string[];
  files?: File[];
  data?: Record<string, string>;
}) {
  return {
    types: options.types,
    files: options.files ?? [],
    dropEffect: 'none',
    getData: (type: string) => options.data?.[type] ?? '',
    setData: vi.fn(),
  } as unknown as DataTransfer;
}

function setup() {
  const onPick = vi.fn();
  const onAddAsset = vi.fn();
  render(
    <ReferenceImages
      items={[]}
      max={4}
      onPick={onPick}
      onRemove={vi.fn()}
      onAddAsset={onAddAsset}
    />,
  );
  return { zone: screen.getByRole('group', { name: '参考图投放区' }), onPick, onAddAsset };
}

describe('ReferenceImages · 投放区', () => {
  it('桌面文件拖进来时接住：preventDefault 并交给本地上传', () => {
    const { zone, onPick } = setup();
    const file = new File(['x'], 'cat.png', { type: 'image/png' });
    const dataTransfer = fakeDataTransfer({ types: ['Files'], files: [file] });

    // fireEvent 返回 false = 事件被 preventDefault 了（否则浏览器会去打开文件）
    const notCancelled = fireEvent.dragOver(zone, { dataTransfer });
    expect(notCancelled).toBe(false);

    fireEvent.drop(zone, { dataTransfer });
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0][0]).toBe(file);
  });

  it('素材库拖进来走 onAddAsset，不去碰本地上传', () => {
    const { zone, onPick, onAddAsset } = setup();
    const payload = JSON.stringify({ id: 'lib_1', name: '封面.png', url: '/x.png' });
    const dataTransfer = fakeDataTransfer({
      types: [LIBRARY_ASSET_DRAG_TYPE],
      data: { [LIBRARY_ASSET_DRAG_TYPE]: payload },
    });

    fireEvent.dragOver(zone, { dataTransfer });
    fireEvent.drop(zone, { dataTransfer });

    expect(onAddAsset).toHaveBeenCalledWith({ id: 'lib_1', name: '封面.png', url: '/x.png' });
    expect(onPick).not.toHaveBeenCalled();
  });

  it('拖一段纯文本进来既不接也不拦：不认识的类型不抢事件', () => {
    const { zone, onPick, onAddAsset } = setup();
    const dataTransfer = fakeDataTransfer({ types: ['text/plain'] });

    // 返回 true = 没 preventDefault，浏览器该干嘛干嘛（比如在输入框里粘文本）
    expect(fireEvent.dragOver(zone, { dataTransfer })).toBe(true);
    fireEvent.drop(zone, { dataTransfer });

    expect(onPick).not.toHaveBeenCalled();
    expect(onAddAsset).not.toHaveBeenCalled();
  });
});

describe('ReferenceImages · 多类参考素材', () => {
  const audioLimit = { image: 5, audio: 3 };

  it('模型声明收音频时，文件选择器也放开音频', () => {
    /* 这条守的是「控件有没有跟着模型走」：accept 写死图片的话，
       点「添加」时音频文件在选择器里是灰的 —— 音轨只能靠拖拽进去，
       而用户根本不知道可以拖。后端收得下、界面选不到，就是功能不存在。 */
    render(
      <ReferenceImages
        items={[]}
        max={8}
        kinds={['image', 'audio']}
        limits={audioLimit}
        onPick={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    const input = screen.getByLabelText('上传参考素材');
    expect(input.getAttribute('accept')).toContain('audio/mpeg');
    expect(input.getAttribute('accept')).toContain('audio/wav');
    expect(input.getAttribute('accept')).toContain('image/png');
  });

  it('按类显示余量：图 2/5 · 音频 1/3', () => {
    render(
      <ReferenceImages
        items={[
          { id: 'i1', name: 'a.png', url: '/a', kind: 'image' },
          { id: 'i2', name: 'b.png', url: '/b', kind: 'image' },
          { id: 'a1', name: 'x.wav', url: '/x', kind: 'audio' },
        ]}
        max={8}
        kinds={['image', 'audio']}
        limits={audioLimit}
        onPick={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText(/图 2\/5/)).toBeInTheDocument();
    expect(screen.getByText(/音频 1\/3/)).toBeInTheDocument();
    // 角标按类型各自编号：图 1、图 2、音频 1 —— 与提示词里的指代一一对应
    expect(screen.getByRole('button', { name: /移除图 2/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /移除音频 1/ })).toBeInTheDocument();
  });

  it('图片名额满了但音频还有：添加按钮不能消失', () => {
    /* 只守总数的话这里会算成「8 个名额还剩 3 个」照旧显示按钮，
       但再选一张图必然被后端拒。判据要按类：图满 ≠ 全都满。 */
    const fiveImages = Array.from({ length: 5 }, (_, index) => ({
      id: `i${index}`,
      name: `${index}.png`,
      url: `/u/${index}`,
      kind: 'image',
    }));
    render(
      <ReferenceImages
        items={fiveImages}
        max={8}
        kinds={['image', 'audio']}
        limits={audioLimit}
        onPick={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /添加/ })).toBeInTheDocument();
  });

  it('每一类都满了才收起添加按钮', () => {
    const full = [
      ...Array.from({ length: 5 }, (_, index) => ({
        id: `i${index}`,
        name: `${index}.png`,
        url: `/u/${index}`,
        kind: 'image',
      })),
      ...Array.from({ length: 3 }, (_, index) => ({
        id: `a${index}`,
        name: `${index}.wav`,
        url: `/v/${index}`,
        kind: 'audio',
      })),
    ];
    render(
      <ReferenceImages
        items={full}
        max={8}
        kinds={['image', 'audio']}
        limits={audioLimit}
        onPick={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', { name: /添加/ })).toBeNull();
  });
});
