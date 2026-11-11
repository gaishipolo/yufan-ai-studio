import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { TextRefPicker } from './TextRefPicker';
import { hasForeignMustache, retargetTextTokens } from './textRefs';

const { picked, toastMock } = vi.hoisted(() => ({
  picked: { assets: [] as LibraryAssetOut[] },
  toastMock: vi.fn(),
}));

/** 选择器只需把「确认」时交回的资产喂给被测组件，查询过程不是这里的重点 */
vi.mock('@/entities/library/ui/LibraryPicker', () => ({
  LibraryPicker: (props: {
    open: boolean;
    onConfirm: (assets: LibraryAssetOut[]) => void;
    onClose: () => void;
  }) =>
    props.open ? (
      <div role="dialog" aria-label="选择文本资产">
        <button type="button" onClick={() => props.onConfirm(picked.assets)}>
          确认选择
        </button>
      </div>
    ) : null,
}));

vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const A: LibraryAssetOut = {
  id: 'a',
  name: '脚本A.md',
  kind: 'text',
  origin: 'upload',
  ownerId: 'u1',
  visibility: 'personal',
  state: 'active',
  createdAt: '2026-11-01T00:00:00Z',
  textVersion: 2,
};
const B: LibraryAssetOut = { ...A, id: 'b', name: '脚本B.txt' };
const C: LibraryAssetOut = { ...A, id: 'c', name: '脚本C.md' };

function Harness({
  initial = [] as LibraryAssetOut[],
  initialPrompt = '',
}: {
  initial?: LibraryAssetOut[];
  initialPrompt?: string;
}) {
  const [value, setValue] = useState<LibraryAssetOut[]>(initial);
  const [prompt, setPrompt] = useState(initialPrompt);
  return (
    <>
      <div data-testid="prompt">{prompt}</div>
      <div data-testid="ids">{value.map((item) => item.id).join(',')}</div>
      <TextRefPicker
        value={value}
        onChange={setValue}
        prompt={prompt}
        onPromptChange={setPrompt}
      />
    </>
  );
}

beforeEach(() => {
  picked.assets = [];
  toastMock.mockReset();
});

describe('TextRefPicker · 插入与解绑', () => {
  it('确认后按绑定顺序在提示词末尾插入 {{Text 1}} {{Text 2}}', async () => {
    picked.assets = [A, B];
    const user = userEvent.setup();
    render(<Harness initialPrompt="画一个场景" />);

    await user.click(screen.getByRole('button', { name: '插入文本引用' }));
    await user.click(screen.getByRole('button', { name: '确认选择' }));

    expect(screen.getByTestId('prompt').textContent).toBe('画一个场景 {{Text 1}} {{Text 2}}');
    expect(screen.getByTestId('ids').textContent).toBe('a,b');
    // 绑定项带版本快照 v{textVersion}
    expect(screen.getAllByText('v2')).toHaveLength(2);
  });

  it('解绑中间一项后，后面的 {{Text N}} 自动重编号，不出现越界编号', async () => {
    picked.assets = [A, B, C];
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '插入文本引用' }));
    await user.click(screen.getByRole('button', { name: '确认选择' }));
    expect(screen.getByTestId('prompt').textContent).toContain('{{Text 3}}');

    await user.click(screen.getByRole('button', { name: /解绑文本引用 脚本B/ }));

    const text = screen.getByTestId('prompt').textContent ?? '';
    expect(text).toMatch(/\{\{Text 1\}\}\s+\{\{Text 2\}\}/);
    expect(text).not.toContain('Text 3');
    expect(screen.getByTestId('ids').textContent).toBe('a,c');
  });

  it('上移绑定项后，提示词里的 {{Text N}} 跟着新顺序重编号', async () => {
    picked.assets = [A, B];
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '插入文本引用' }));
    await user.click(screen.getByRole('button', { name: '确认选择' }));
    expect(screen.getByTestId('prompt').textContent).toBe('{{Text 1}} {{Text 2}}');

    await user.click(screen.getByRole('button', { name: '上移文本引用 脚本B' }));

    // A / B 换位：原来指向 A 的 {{Text 1}} 变成 {{Text 2}}，指向 B 的变成 {{Text 1}}
    expect(screen.getByTestId('prompt').textContent).toBe('{{Text 2}} {{Text 1}}');
    expect(screen.getByTestId('ids').textContent).toBe('b,a');
  });
});

describe('retargetTextTokens · 按 id 重编号', () => {
  it('删中间项按 id 重编号，解绑的 token 被丢弃并计数', () => {
    const result = retargetTextTokens(
      '{{Text 1}} 与 {{Text 2}} 和 {{Text 3}}',
      [A, B, C],
      [A, C],
    );
    expect(result.text).toContain('{{Text 1}}');
    expect(result.text).toContain('{{Text 2}}');
    expect(result.text).not.toContain('Text 3');
    expect(result.dropped).toBe(1);
  });

  it('用户先手写 {{Text 1}} 再挑素材：占位符原地保留，不搬到末尾', () => {
    // 回归：旧实现把「该位从未绑定」当成失效 token 删掉，再追加到结尾，
    // 结果 `把 {{Text 1}} 的卖点做成海报` 变成 `把  的卖点做成海报 {{Text 1}}`
    const result = retargetTextTokens('把 {{Text 1}} 的卖点做成海报', [], [A]);
    expect(result.text).toBe('把 {{Text 1}} 的卖点做成海报');
    expect(result.dropped).toBe(0);
  });

  it('新增绑定不会重复追加已有的编号', () => {
    const result = retargetTextTokens('{{Text 1}} 配 {{Text 2}}', [A], [A, B]);
    expect(result.text).toBe('{{Text 1}} 配 {{Text 2}}');
    expect(result.dropped).toBe(0);
  });

  it('手写编号超出新绑定数量才丢弃', () => {
    const result = retargetTextTokens('{{Text 1}} 和 {{Text 3}}', [], [A]);
    expect(result.text).toBe('{{Text 1}} 和 ');
    expect(result.dropped).toBe(1);
  });

  it('hasForeignMustache 放行 {{Text N}}，拦其它模板括号', () => {
    expect(hasForeignMustache('照 {{Text 1}} 写')).toBe(false);
    expect(hasForeignMustache('照 {{Foo}} 写')).toBe(true);
  });
});
