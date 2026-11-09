import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChangePasswordModal } from './ChangePasswordModal';

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  endSession: vi.fn(),
}));

vi.mock('@/entities/session/api/session.queries', () => ({
  useChangePassword: () => ({ mutate: mocks.mutate, isPending: false }),
}));

vi.mock('@/shared/access/end-session', () => ({
  endSession: mocks.endSession,
}));

/** 能过 checkPassword 的口令：≥10 位、非纯数字、不同字符 ≥4 */
const NEW_PASSWORD = 'abcd1234ef';

/* `userEvent.type` 逐字符触发：单跑这个文件约 0.7s，但 57 个文件并行时
   会被调度拖到 5–12s，Vitest 默认 5s 会假失败。只放宽本文件的超时，
   不改全局配置 —— 这些用例验的是「提交什么、清不清场」，不是输入速度。 */
const TIMEOUT = 10_000;

function fill(oldPassword: string, next: string, confirm: string) {
  return async () => {
    await userEvent.type(screen.getByLabelText(/当前密码/), oldPassword);
    await userEvent.type(screen.getByLabelText(/^新密码/), next);
    await userEvent.type(screen.getByLabelText(/确认新密码/), confirm);
    await userEvent.click(screen.getByRole('button', { name: '更新密码' }));
  };
}

describe('ChangePasswordModal', () => {
  beforeEach(() => {
    mocks.mutate.mockReset();
    mocks.endSession.mockReset();
  });

  /* 后端 `change_password` 在改密后紧跟 `sessions.revoke_all(reason="password_change")`
     （R10-03），当前这台也在吊销名单里。所以前端必须主动结束会话 ——
     以前这里什么都不做，用户会先看到"成功"，几百毫秒后被某个请求 401 踢下线，
     登录页还说"可能已在别处被重置"，听起来像被入侵。 */
  it('改密成功 → 主动结束会话，原因记为 password', async () => {
    mocks.mutate.mockImplementation((_payload, options) => options?.onSuccess?.());
    const onClose = vi.fn();
    render(<ChangePasswordModal open onClose={onClose} />);

    await fill('old-pass-1234', NEW_PASSWORD, NEW_PASSWORD)();

    expect(mocks.mutate).toHaveBeenCalledWith(
      { oldPassword: 'old-pass-1234', newPassword: NEW_PASSWORD },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(mocks.endSession).toHaveBeenCalledWith('password');
    // 先关弹窗再清场：清场会触发守卫重定向，留着弹窗只会闪一下
    expect(onClose).toHaveBeenCalled();
  }, TIMEOUT);

  it('两次输入不一致时既不发请求也不结束会话', async () => {
    render(<ChangePasswordModal open onClose={vi.fn()} />);

    await fill('old-pass-1234', NEW_PASSWORD, 'abcd1234eg')();

    expect(mocks.mutate).not.toHaveBeenCalled();
    expect(mocks.endSession).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('两次输入的新密码不一致');
  }, TIMEOUT);

  it('新口令不满足策略时拦在前端（少一次往返）', async () => {
    render(<ChangePasswordModal open onClose={vi.fn()} />);

    await fill('old-pass-1234', '1234567890123', '1234567890123')();

    expect(mocks.mutate).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('不能是纯数字');
  }, TIMEOUT);
});
