import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryFacets } from '@/shared/api/schema';
import { LibraryToolbar, type LibraryToolbarProps } from './LibraryToolbar';

const FACETS: LibraryFacets = {
  all: 21,
  personal: 18,
  group: 3,
  team: 3,
  drafts: 0,
  trash: 3,
};

function renderToolbar(overrides: Partial<LibraryToolbarProps> = {}) {
  const props: LibraryToolbarProps = {
    view: 'all',
    facets: FACETS,
    kind: 'all',
    keyword: '',
    sort: '-createdAt',
    canCreate: true,
    pendingCount: 0,
    onView: vi.fn(),
    onKind: vi.fn(),
    onKeyword: vi.fn(),
    onSort: vi.fn(),
    onUpload: vi.fn(),
    onNewText: vi.fn(),
    onPack: vi.fn(),
    onRequests: vi.fn(),
  };
  const view = render(<LibraryToolbar {...props} {...overrides} />);
  return { props, unmount: view.unmount };
}

describe('LibraryToolbar · 一级入口', () => {
  it('六个入口按总览 → 三个范围 → 草稿箱 → 回收站排列，各用自己的角标', () => {
    renderToolbar();

    // 渲染顺序里前六个按钮就是一级 Tabs（后面才是类型筛选与操作按钮）
    expect(screen.getAllByRole('button').slice(0, 6).map((button) => button.textContent)).toEqual([
      '全部素材21',
      '个人18',
      '小组3',
      '团队3',
      '草稿箱0',
      '回收站3',
    ]);
  });

  it('点入口回传 view 值，不夹带当前筛选状态', async () => {
    const user = userEvent.setup();
    const { props } = renderToolbar();

    await user.click(screen.getByRole('button', { name: /^个人/ }));
    expect(props.onView).toHaveBeenCalledWith('personal');
    await user.click(screen.getByRole('button', { name: /^草稿箱/ }));
    expect(props.onView).toHaveBeenCalledWith('drafts');
  });

  it('默认落地页「全部素材」也有上传/新建封装，草稿箱与回收站没有', () => {
    const { unmount } = renderToolbar();
    expect(screen.getByRole('button', { name: '上传' })).toBeInTheDocument();
    // 动作叫「新建封装」：类型筛选里另有一档「封装」，同名两个按钮会让人分不清
    // 哪个是筛、哪个是建（这也是它改名而不是筛选改名的原因）。
    expect(screen.getByRole('button', { name: '新建封装' })).toBeInTheDocument();
    unmount();

    renderToolbar({ view: 'trash' });
    expect(screen.queryByRole('button', { name: '上传' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建封装' })).not.toBeInTheDocument();
  });

  it('类型筛选里有一档「封装」，和成片/图片/音频/文本并列', () => {
    const { props } = renderToolbar();
    const tab = screen.getByRole('button', { name: '封装' });
    tab.click();
    expect(props.onKind).toHaveBeenCalledWith('pack');
  });

  it('「新建文本」与上传同显隐，点击回传一次', async () => {
    const user = userEvent.setup();
    const { props, unmount } = renderToolbar();
    const button = screen.getByRole('button', { name: '新建文本' });
    await user.click(button);
    expect(props.onNewText).toHaveBeenCalledTimes(1);
    unmount();

    renderToolbar({ view: 'trash' });
    expect(screen.queryByRole('button', { name: '新建文本' })).not.toBeInTheDocument();
  });
});
