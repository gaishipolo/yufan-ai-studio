import { expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { SubjectDetail, SubjectOut } from '@/shared/api/schema';
import { SubjectCard } from './SubjectCard';
import { SubjectDetailPanel } from './SubjectDetailPanel';

/**
 * 主体卡片的**三态**与详情条带。
 *
 * 两条断言值得单独存在：
 *
 * 1. 角标文案必须来自服务端给的 `availability`。前端自己重算"成员齐不齐"
 *    必然与服务端漂移，而漂移的表现是「卡片说能发、点下去报缺少能力」。
 * 2. 缺项要落到**具体哪一张**（`memberGaps`）。只显示主体级结论的话，用户
 *    得自己点十张图去数，而那个动作没人会真的做。
 *
 * 未知状态一律回落成"待补齐"：后端将来加第四态，最差是少一个角标，不能白屏。
 */

const base: SubjectOut = {
  id: 'sbj_1',
  name: '示例主体 · 人物替换',
  subjectType: 'character',
  status: 'published',
  visibility: 'personal',
  memberCount: 2,
  currentVersion: 1,
  coverUrl: '',
};

const detail: SubjectDetail = {
  ...base,
  availability: 'incomplete',
  availabilityNote: '成员缺少能力：download',
  members: [
    {
      assetId: 'a1',
      name: '正面图',
      role: 'front',
      sortOrder: 0,
      grants: ['generate', 'download'],
      inOutput: true,
    },
    {
      assetId: 'a2',
      name: '细节图',
      role: 'detail',
      sortOrder: 1,
      grants: ['generate'],
      inOutput: true,
    },
    {
      assetId: 'a3',
      name: '已删的图',
      role: 'side',
      sortOrder: 2,
      grants: ['generate'],
      inOutput: true,
      missing: true,
    },
  ],
  requires: ['generate', 'download'],
  requiredRoles: ['front', 'detail'],
  missingCapabilities: ['download'],
  missingRoles: [],
  memberGaps: {a2: ['download']},
};

const mock = vi.hoisted(() => ({
  detail: undefined as unknown,
  publish: vi.fn(),
}));

vi.mock('@/entities/asset/api/subject.api', () => ({
  useSubject: () => ({data: mock.detail, isLoading: false, error: null}),
  usePublishSubject: () => ({mutateAsync: mock.publish}),
}));

function card(state: string) {
  render(<SubjectCard subject={{...base, availability: state}} onOpen={vi.fn()} />);
}

it('三态各自映射到不同的角标', () => {
  card('ready');
  expect(screen.getByText('可发布')).toBeTruthy();
  document.body.innerHTML = '';

  card('broken');
  expect(screen.getByText('缺图')).toBeTruthy();
  document.body.innerHTML = '';

  card('incomplete');
  expect(screen.getByText('待补齐')).toBeTruthy();
});

it('未知状态回落成待补齐，而不是渲染空白', () => {
  card('some_future_state');
  expect(screen.getByText('待补齐')).toBeTruthy();
});

it('卡片显示成员数与版本号，没有成员时不显示 0 张', () => {
  render(<SubjectCard subject={{...base, availability: 'ready'}} onOpen={vi.fn()} />);
  expect(screen.getByText('2 张成员 · v1')).toBeTruthy();
  document.body.innerHTML = '';

  render(
    <SubjectCard
      subject={{...base, memberCount: 0, currentVersion: 0, availability: 'incomplete'}}
      onOpen={vi.fn()}
    />,
  );
  expect(screen.getByText('还没有成员')).toBeTruthy();
});

it('详情条带把缺口落到具体哪一张，并标出已删的成员', () => {
  mock.detail = detail;
  render(<SubjectDetailPanel subjectId="sbj_1" canPublish onClose={vi.fn()} />);

  // 主体级结论
  expect(screen.getByText('缺能力 · 可下载')).toBeTruthy();
  // 逐成员缺口：只有 a2 缺 download
  expect(screen.getByText('缺 · 可下载')).toBeTruthy();
  // 已删成员仍然占位（不删行）
  expect(screen.getByText('已删除')).toBeTruthy();
  expect(screen.getByText('正面图')).toBeTruthy();
});

it('有成员图被删时发布按钮禁用，因为恢复那张图不在这里做', () => {
  mock.detail = {...detail, availability: 'broken', availabilityNote: '1 张成员图已被删除'};
  render(<SubjectDetailPanel subjectId="sbj_1" canPublish onClose={vi.fn()} />);
  const button = screen.getByRole('button', {name: '发布'});
  expect((button as HTMLButtonElement).disabled).toBe(true);
});

it('没有发布权限时按钮禁用（后端仍会 403，这里只是别让用户白点）', () => {
  mock.detail = detail;
  render(<SubjectDetailPanel subjectId="sbj_1" canPublish={false} onClose={vi.fn()} />);
  const button = screen.getByRole('button', {name: '发布'});
  expect((button as HTMLButtonElement).disabled).toBe(true);
});
