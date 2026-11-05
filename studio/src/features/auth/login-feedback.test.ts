import { describe, expect, it } from 'vitest';
import { ApiError } from '@/shared/api/types';
import { describeLoginFailure, formatCooldown } from './login-feedback';

describe('describeLoginFailure', () => {
  it('401 用后端原文，不替后端猜「账号是否存在」', () => {
    // 真实后端的登录失败是 identity 模块号段里的 40121，不是框架的 40100
    const failure = describeLoginFailure(new ApiError('用户名或密码错误', 40_121));

    // 标题是类别、详情是原文：标题不能把同一句话再说一遍
    expect(failure.title).toBe('登录失败');
    expect(failure.detail).toBe('用户名或密码错误');
    expect(failure.cooldownSec).toBe(0);
  });

  it('裸 HTTP 状态码（网关透传 / mock 适配器）同样能分类', () => {
    expect(describeLoginFailure(new ApiError('用户名或密码错误', 401)).title).toBe('登录失败');
    expect(describeLoginFailure(new ApiError('密码错误次数过多', 429)).cooldownSec).toBe(60);
  });

  it('连续失败到第 3 次追加提醒，但不硬编码后端的阈值', () => {
    const failure = describeLoginFailure(new ApiError('用户名或密码错误', 40_121), 3);

    expect(failure.detail).toContain('会被暂时锁定');
    // 后端消息没有句号时要补上，否则读成半句话
    expect(failure.detail.startsWith('用户名或密码错误。')).toBe(true);
    // 不写具体次数：阈值是后端策略，写死就会出现"说 5 次实际 6 次"的错位
    expect(failure.detail).not.toMatch(/\d+\s*次/);
  });

  it('429 进入倒计时，时长从后端文案解析', () => {
    const failure = describeLoginFailure(new ApiError('密码错误次数过多，请 15 分钟后再试', 42_900));

    expect(failure.title).toBe('已被暂时锁定');
    expect(failure.cooldownSec).toBe(15 * 60);
  });

  it('429 文案里没有时长时退回兜底等待，而不是永久锁定', () => {
    const failure = describeLoginFailure(new ApiError('请求过于频繁', 42_900));

    expect(failure.cooldownSec).toBe(60);
  });

  it('403（账号停用）不说成密码错误——重试没有意义', () => {
    const failure = describeLoginFailure(new ApiError('账号已停用，请联系管理员', 40_321));

    expect(failure.title).toBe('账号不可用');
    expect(failure.cooldownSec).toBe(0);
  });

  it('超时与网络异常分开措辞（用户该做的事不同）', () => {
    expect(describeLoginFailure(new ApiError('请求超时，请稍后重试', 408)).title).toBe('请求超时');
    expect(describeLoginFailure(new ApiError('Network Error', -1)).title).toBe('网络异常');
  });

  it('5xx 归为服务异常', () => {
    expect(describeLoginFailure(new ApiError('服务器内部错误', 50_000)).title).toBe('服务异常');
  });

  it('非 ApiError 也不会把 unknown 抛给用户', () => {
    const failure = describeLoginFailure(new Error('boom'));

    expect(failure.title).toBe('登录失败');
    expect(failure.detail).not.toContain('boom');
  });
});

describe('formatCooldown', () => {
  it('不足一分钟只显示秒', () => {
    expect(formatCooldown(45)).toBe('45 秒');
  });

  it('整分钟不带秒', () => {
    expect(formatCooldown(120)).toBe('2 分');
  });

  it('跨分钟拆分', () => {
    expect(formatCooldown(92)).toBe('1 分 32 秒');
  });
});
