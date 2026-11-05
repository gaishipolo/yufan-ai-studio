import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  PASSWORD_MIN_LENGTH,
  USERNAME_PATTERN,
  checkAvatarUrl,
  checkEmail,
  checkPassword,
} from './validation';

/**
 * 这些规则原本散落在 3 个弹窗 + 2 个 mock 里各写一遍，
 * 所以测试也放在规则旁边：改一处就能验证全部调用方。
 */
describe('checkPassword', () => {
  it('短于最小长度时报错，并把角色名带进文案', () => {
    expect(checkPassword('a'.repeat(PASSWORD_MIN_LENGTH - 1), '新密码')).toBe(
      `新密码至少 ${PASSWORD_MIN_LENGTH} 位`,
    );
  });

  it('纯数字即使是长串也不通过', () => {
    expect(checkPassword('1234567890123', '初始密码')).toBe('初始密码不能是纯数字');
  });

  it('恰好等于最小长度即可通过（边界不能写成 >）', () => {
    expect(checkPassword('abcd1234ef')).toBeNull();
  });

  /* 后端 policy.py 里 `len(set(value)) < 4` 会 422：前端不拦的话，
     用户按「至少 10 位」的提示输完，最后一步才被拒 */
  it('重复字符凑长度的口令不通过', () => {
    expect(checkPassword('aaaaaaaaa1', '新密码')).toBe('新密码过于简单（不要用重复字符凑长度）');
    // 3 种字符同样不过（abc 循环），4 种字符就放过——边界与后端一致
    expect(checkPassword('abcabcabca')).toContain('过于简单');
    expect(checkPassword('abcdabcdab')).toBeNull();
  });

  it('种子账号的口令仍然合法（加了规则不能把演示账号锁在门外）', () => {
    for (const password of ['admin@12345', 'editor@12345', 'viewer@12345']) {
      expect(checkPassword(password), password).toBeNull();
    }
  });

  it('默认角色名是「密码」，不传 label 也不会拼出奇怪的句子', () => {
    expect(checkPassword('123')).toBe(`密码至少 ${PASSWORD_MIN_LENGTH} 位`);
  });
});

describe('checkEmail', () => {
  it('留空与 null 都算「不填写」', () => {
    expect(checkEmail('')).toBeNull();
    expect(checkEmail('   ')).toBeNull();
    expect(checkEmail(null)).toBeNull();
    expect(checkEmail(undefined)).toBeNull();
  });

  it('挡掉缺域名后缀的地址', () => {
    expect(checkEmail('a@b')).toBe('邮箱格式不正确');
    expect(checkEmail('a b@c.com')).toBe('邮箱格式不正确');
    expect(checkEmail('a@b.com')).toBeNull();
  });
});

describe('checkAvatarUrl', () => {
  it('留空表示不设置头像', () => {
    expect(checkAvatarUrl('')).toBeNull();
    expect(checkAvatarUrl(null)).toBeNull();
  });

  it('接受 http(s) 与站内相对路径', () => {
    expect(checkAvatarUrl('https://cdn.example.com/a.png')).toBeNull();
    expect(checkAvatarUrl('/avatars/u_1.png')).toBeNull();
  });

  it('拒绝 javascript: / data: 这类伪协议', () => {
    expect(checkAvatarUrl('javascript:alert(1)')).toMatch(/http/);
    expect(checkAvatarUrl('data:image/svg+xml;base64,AAA')).toMatch(/http/);
  });

  it('超长地址直接拦下，避免把整段 base64 存进库', () => {
    expect(checkAvatarUrl(`https://x/${'a'.repeat(520)}`)).toContain('上限');
  });
});

describe('USERNAME_PATTERN', () => {
  it('与后端一致：3-64 位字母数字与 . _ -', () => {
    expect(USERNAME_PATTERN.test('abc')).toBe(true);
    expect(USERNAME_PATTERN.test('a.b_c-d')).toBe(true);
    expect(USERNAME_PATTERN.test('ab')).toBe(false);
    expect(USERNAME_PATTERN.test('中文名')).toBe(false);
    expect(USERNAME_PATTERN.test('a'.repeat(65))).toBe(false);
  });
});

/**
 * 口令有三个写入口，规则只有一份 —— 但"记得调"这件事以前靠人。
 * 这条用例直接扫源码：改了某个弹窗又把校验丢掉，会在这里红。
 * 新增第四个改口令的入口时，请把文件加进这份名单。
 *
 * 路径用 cwd 拼：`import.meta.url` 在 vitest 里会被 vite 改写成开发服务器地址，
 * `fileURLToPath` 拿到 http 会直接抛错。
 */
describe('口令校验的调用点', () => {
  const CALL_SITES = [
    'src/features/user-form/UserFormModal.tsx',
    'src/features/user-security/ResetPasswordModal.tsx',
    'src/features/auth/ChangePasswordModal.tsx',
  ];

  it.each(CALL_SITES)('%s 提交前调了 checkPassword', (relative) => {
    const source = readFileSync(join(process.cwd(), relative), 'utf8');
    expect(source).toContain('checkPassword(');
  });
});
