import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PromptBox, type PromptChip } from './PromptBox';

const CHIPS: PromptChip[] = [
  {token: '{{slot:image_1}}', label: '主图', all: false},
  {token: '{{slot:image_2}}', label: '细节图', all: false},
  {token: '{{slot:group*}}', label: '整组（全部）', all: true},
];

/* 候选面板里的项和下方的 chip 按钮同名（都是「主图」），用容器区分：
   候选项带「单个/全部」标记，chip 按钮不带。 */
function candidate(name: RegExp) {
  return screen.getByRole('button', {name: new RegExp(`${name.source}.*(单个|全部)`)});
}

/** 受控外壳：与真实用法一致（值由父组件持有）。 */
function Harness({chips = CHIPS}: {chips?: PromptChip[]}) {
  const [text, setText] = useState('');
  return (
    <>
      <PromptBox value={text} chips={chips} onChange={setText} onInsert={setText} />
      <output data-testid="value">{text}</output>
    </>
  );
}

/**
 * 输入框里 `{{slot:key}}` **内联渲染成胶囊**。
 *
 * 这是这个组件最容易退化的地方：一旦有人把实现换回 `<textarea>`
 * （比如为了省事），作者又会看到 `{{slot:image_1}}` 这种内部 id。
 * 原来的兜底是输入框下面另开一块「预览」，那块已经取消 ——
 * 胶囊现在只在输入框里，做不出来就等于没有。
 */
describe('输入框内联胶囊', () => {
  it('token 显示成插槽名，不显示内部 id', () => {
    render(
      <PromptBox
        value="先拍 {{slot:image_1}}，再补 {{slot:image_2}}。"
        chips={CHIPS}
        onChange={() => {}}
        onInsert={() => {}}
      />,
    );

    const box = screen.getByRole('textbox', { name: '提示词' });
    const capsules = box.querySelectorAll('[data-token]');
    expect(capsules).toHaveLength(2);
    expect(capsules[0]).toHaveTextContent('主图');
    expect(capsules[1]).toHaveTextContent('细节图');

    // 界面上一个字都不该露出 token
    expect(box.textContent).not.toContain('{{slot:');
    // 但 token 留在 data 属性上：序列化要拿它还原成提交用的正文
    expect(capsules[0]).toHaveAttribute('data-token', '{{slot:image_1}}');
  });

  it('胶囊不可编辑（光标不进去，退格一次整个删掉）', () => {
    render(<PromptBox value="拍 {{slot:image_1}}" chips={CHIPS} onChange={() => {}} onInsert={() => {}} />);

    const capsule = screen.getByRole('textbox', { name: '提示词' }).querySelector('[data-token]');
    expect(capsule).toHaveAttribute('contenteditable', 'false');
  });

  it('普通文字照旧是普通文字，不被胶囊吞掉', () => {
    render(
      <PromptBox value="先拍一张" chips={CHIPS} onChange={() => {}} onInsert={() => {}} />,
    );

    const box = screen.getByRole('textbox', { name: '提示词' });
    expect(box.textContent).toBe('先拍一张');
    expect(box.querySelector('[data-token]')).toBeNull();
  });

  it('没有预览块了（胶囊取代了它，别再摆一块讲同一件事）', () => {
    render(<PromptBox value="拍 {{slot:image_1}}" chips={CHIPS} onChange={() => {}} onInsert={() => {}} />);

    expect(screen.queryByTestId('prompt-preview')).not.toBeInTheDocument();
    expect(screen.queryByText(/素材占位显示插槽名/)).not.toBeInTheDocument();
  });

  /* 带空格的写法（`{{ slot:key }}`）在老草稿里存在。它也得认出来并画成胶囊，
     否则作者会看到一段裸露的大括号。 */
  it('带空格的 token 也认', () => {
    render(
      <PromptBox value="拍 {{ slot:image_1 }}" chips={CHIPS} onChange={() => {}} onInsert={() => {}} />,
    );

    const capsule = screen.getByRole('textbox', { name: '提示词' }).querySelector('[data-token]');
    expect(capsule).toHaveTextContent('主图');
  });
});

/* 还没有槽位时给一句话，别留一个空框让人猜。 */
describe('空态', () => {
  it('没有槽位时 chip 行给一句提示', () => {
    render(<PromptBox value="" chips={[]} onChange={() => {}} onInsert={() => {}} />);

    expect(screen.getByText('先在左边加槽位，再打 @ 引用')).toBeInTheDocument();
  });
});

describe('提示词框的 @ 提及', () => {
  it('打 @ 弹出候选面板', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');

    expect(screen.getByText('选择要引用的槽位')).toBeInTheDocument();
    expect(candidate(/主图/)).toBeInTheDocument();
  });

  it('继续打字过滤候选', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@细节');

    expect(screen.getByText('匹配「细节」')).toBeInTheDocument();
    expect(candidate(/细节图/)).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /主图.*(单个|全部)/})).not.toBeInTheDocument();
  });

  it('鼠标点候选：@查询词 整段换成 token', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '把 @主');
    await user.click(candidate(/主图/));

    expect(screen.getByTestId('value')).toHaveTextContent('把 {{slot:image_1}}');
    // 选完面板收起
    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
  });

  it('键盘：↑↓ 选候选，Enter 确认', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');

    // 默认高亮第一项，↓ 之后是第二项
    expect(screen.getByTestId('value')).toHaveTextContent('{{slot:image_2}}');
  });

  it('Escape 收起面板但不改动文本', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@主');
    await user.keyboard('{Escape}');

    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
    expect(screen.getByTestId('value')).toHaveTextContent('@主');
  });

  it('没有候选时（输入邮箱）不弹面板', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '联系 a@b.com');

    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
  });

  it('英文后面打 @ 照样能唤起候选面板（用户报过的坑）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    // **@ 前面没有空格** —— 用户报的就是这种：一串英文紧跟着打 @
    await user.type(box, 'cinematic handheld shot@');

    expect(screen.getByText('选择要引用的槽位')).toBeInTheDocument();
    expect(candidate(/主图/)).toBeInTheDocument();
  });

  it('英文后面打 @ 再继续过滤，候选也跟着筛', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, 'cinematic shot@主');

    expect(screen.getByText('匹配「主」')).toBeInTheDocument();
    expect(candidate(/主图/)).toBeInTheDocument();
  });

  it('下方的 chip 按钮照旧能插入引用（不破坏原功能）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    /* chip 现在是一枚带状态的小胶囊（圆点 + 名字 + 「用户填写」），
       可访问名里带上了状态文字，所以用包含匹配。 */
    await user.click(screen.getByRole('button', {name: /主图/}));

    expect(screen.getByTestId('value')).toHaveTextContent('{{slot:image_1}}');
  });

  /* chip 插入必须走 `onInsert`（程序插入），不能走 `onChange`（用户手打）。
   *
   * 回归：父组件对 `onChange` 有「不许手写花括号」的校验（手写 key 拼错要
   * 到编译期才 400）。这两条路径一旦并成一条，点一下 chip 就会弹红字
   * 「引用请用 @ 或下方按钮插入，不要手写大括号」—— 而用户点的正是那个按钮。
   *
   * 上面那条用例把 onChange 与 onInsert 传成同一个函数，所以看不出区别；
   * 这里必须分开传。 */
  it('点 chip 走 onInsert，不走 onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onInsert = vi.fn();
    render(<PromptBox value="" chips={CHIPS} onChange={onChange} onInsert={onInsert} />);

    await user.click(screen.getByRole('button', {name: /主图/}));

    expect(onInsert).toHaveBeenCalledWith('{{slot:image_1}}');
    expect(onChange).not.toHaveBeenCalled();
  });
});

/**
 * 粘贴。
 *
 * 用户报的原话：「我直接粘贴正确的插槽 `{{slot:image_1}}` 不会变成胶囊」。
 *
 * 根因不在渲染，在**中间态**：以前粘贴是往 DOM 里塞一个文本节点再同步 ——
 * 序列化结果和粘的内容一模一样，所以「值变了没有」看不出来；而胶囊化要等
 * `value` 变化触发重建。写入被父组件拒掉时 `value` 根本不变，于是那段字就
 * 以「半截文本」的样子留在框里（看见的和会存的不是一回事）。
 *
 * 现在粘贴走 `value`：整段文字与它的胶囊是同一次渲染出来的。
 */
describe('粘贴', () => {
  it('粘一段合法的槽位引用 → 直接变胶囊', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.paste('先拍 {{slot:image_1}}');

    const capsule = box.querySelector('[data-token]');
    expect(capsule).toHaveTextContent('主图');
    // 界面上不该再露 token
    expect(box.textContent).not.toContain('{{slot:');
    expect(screen.getByTestId('value')).toHaveTextContent('先拍 {{slot:image_1}}');
  });

  it('粘多段引用：各自成胶囊', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.paste('{{slot:image_1}} 和 {{slot:group*}}');

    const capsules = box.querySelectorAll('[data-token]');
    expect(capsules).toHaveLength(2);
    expect(capsules[1]).toHaveTextContent('整组（全部）');
  });

  /* 被拒时界面必须回退：不能出现「框里有字、值里没有」。 */
  it('写入被拒（父组件返回 false）时，框里不留半截文本', async () => {
    const user = userEvent.setup();
    render(<PromptBox value="" chips={CHIPS} onChange={() => false} onInsert={() => false} />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.paste('{{slot:image_9}}');

    expect(box.textContent).toBe('');
    expect(box.querySelector('[data-token]')).toBeNull();
  });

  /* 手打花括号被拒时也一样：浏览器已经把字插进 DOM 了，`value` 却没变 —
     不主动对齐就会留下「看得见、存不进去」的字。 */
  it('手打花括号被拒时，那个字符不会留在框里', async () => {
    const user = userEvent.setup();

    function Strict() {
      const [text, setText] = useState('');
      const accept = (next: string) => {
        if (/[{}]/.test(next)) return false;
        setText(next);
        return true;
      };
      return (
        <>
          <PromptBox value={text} chips={CHIPS} onChange={accept} onInsert={accept} />
          <output data-testid="value">{text}</output>
        </>
      );
    }

    render(<Strict />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    // userEvent 的键盘语法里 `{` 是特殊字符：`{{` 才是字面量
    await user.type(box, 'a{{b');

    // `{` 被拒、被撤掉；前后的普通字留着
    expect(screen.getByTestId('value')).toHaveTextContent('ab');
    expect(box.textContent).toBe('ab');
  });
});

/**
 * 撤销。
 *
 * 浏览器的原生撤销在这个框里是不工作的：胶囊靠重建 DOM 实现，一重建就把元素的
 * undo 记录清掉；粘贴又被 `preventDefault` 接管，浏览器根本没记下那一步。
 * 所以编辑器自己维护一份文本栈（见 `RichPromptEditor` 里的 `history`）。
 */
describe('撤销 / 重做', () => {
  it('粘贴后 Ctrl+Z 退回粘贴前，胶囊也一起消失', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);

    await user.paste('参考 {{slot:image_1}}');
    expect(screen.getByTestId('value')).toHaveTextContent('参考 {{slot:image_1}}');

    await user.keyboard('{Control>}z{/Control}');

    expect(screen.getByTestId('value').textContent).toBe('');
    expect(box.querySelector('[data-token]')).toBeNull();
  });

  it('Ctrl+Shift+Z 重做回来', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);

    await user.paste('参考 {{slot:image_1}}');
    await user.keyboard('{Control>}z{/Control}');
    await user.keyboard('{Control>}{Shift>}Z{/Shift}{/Control}');

    expect(screen.getByTestId('value')).toHaveTextContent('参考 {{slot:image_1}}');
    // 重做之后胶囊也回来了（DOM 是按 value 重画的）
    expect(box.querySelector('[data-token]')).toHaveTextContent('主图');
  });

  /* 一个词一次 `Ctrl+Z` 就够 —— 逐字退会让长提示词退到天荒地老。 */
  it('连续打出来的字合并成一步', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, 'abc');
    expect(screen.getByTestId('value').textContent).toBe('abc');

    await user.keyboard('{Control>}z{/Control}');

    expect(screen.getByTestId('value').textContent).toBe('');
  });

  /* 退到底就停住，不能把框弄成空白之外的东西，也不能崩。 */
  it('没有可退的时候按 Ctrl+Z 什么也不发生', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, 'a');
    await user.keyboard('{Control>}z{/Control}');
    await user.keyboard('{Control>}z{/Control}');

    expect(screen.getByTestId('value').textContent).toBe('');
    expect(box.textContent).toBe('');
  });
});

/**
 * 浮层方向：提示词框在模板封装页的右栏最下面，朝下展开会整块掉到视口外 ——
 * 用户只看到输入框闪一下、什么也没出现。空间不够就得翻到上面去。
 */
describe('候选面板的方向', () => {
  /** 把输入框钉在视口里的某个位置：下方还剩多少空间由 innerHeight 决定。 */
  function placeBoxAt(bottom: number, innerHeight: number) {
    Object.defineProperty(window, 'innerHeight', { value: innerHeight, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      bottom,
      top: bottom - 60,
      height: 100,
      left: 0,
      right: 400,
      width: 400,
      x: 0,
      y: bottom - 60,
      toJSON: () => ({}),
    } as DOMRect);
  }

  const panel = () => screen.getByText('选择要引用的槽位').closest('[data-placement]');

  it('下方空间不够时朝上展开（提示词框贴着视口底边的场景）', async () => {
    const user = userEvent.setup();
    placeBoxAt(760, 800);
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');

    expect(panel()).toHaveAttribute('data-placement', 'top');
    // 朝上时不能还留着朝下的定位类，否则会同时挂两条定位
    expect(panel()?.className).not.toContain('top-[calc(100%+4px)]');
  });

  it('下方够高就朝下展开', async () => {
    const user = userEvent.setup();
    placeBoxAt(120, 800);
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');

    expect(panel()).toHaveAttribute('data-placement', 'bottom');
  });
});
