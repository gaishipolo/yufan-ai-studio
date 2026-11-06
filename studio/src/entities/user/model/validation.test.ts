import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  PASSWORD_MIN_LENGTH,
  PASSWORD_MIN_UNIQUE_CHARS,
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

/**
 * 与后端口令策略逐条比对 —— **解析后端源码，不调接口**（FE5-02 / FE-33）。
 *
 * 上面那些用例拿本地常量做期望值，所以后端把 10 改成 12 时它们照样全绿；
 * 这条才是真正钉住「唯一真相在后端」的守卫。选源码比对而不是调
 * `GET /identity/password-policy`，是为了不给运行时加一次冷启动请求
 * —— 理由写在 `validation.ts` 文件头。
 *
 * 读不到 `policy.py` 必须**报错**，不能静默跳过：静默跳过的守卫等于没有守卫，
 * 前端单独检出时会一直绿着腐烂（旧 `permission-catalog.test.ts` 踩过这个坑）。
 *
 * 路径用 cwd 拼：`import.meta.url` 在 vitest 里会被 vite 改写成开发服务器地址。
 */
describe('与后端口令策略一致', () => {
  const POLICY_PATH = join(process.cwd(), '..', 'api', 'src', 'modules', 'identity', 'policy.py');

  function backendPolicySource(): string {
    try {
      return readFileSync(POLICY_PATH, 'utf8');
    } catch {
      throw new Error(
        `读不到后端策略文件 ${POLICY_PATH}。这条用例靠解析后端源码守住前端常量，` +
          `必须与 api/ 一起检出；不要改成静默跳过。`,
      );
    }
  }

  /** 解析 `NAME: Final = 123` 形态的常量 */
  function backendConstants(): Record<string, number> {
    const found: Record<string, number> = {};
    for (const [, name, value] of backendPolicySource().matchAll(
      /^(PASSWORD_\w+):\s*Final\s*=\s*(\d+)\s*$/gm,
    )) {
      found[name] = Number(value);
    }
    return found;
  }

  it('三个策略常量都能从 policy.py 解析出来（解析不到要红，不能当作没这条规则）', () => {
    expect(Object.keys(backendConstants()).sort()).toEqual([
      'PASSWORD_MAX_LENGTH',
      'PASSWORD_MIN_LENGTH',
      'PASSWORD_MIN_UNIQUE_CHARS',
    ]);
  });

  it('最小长度与后端一致', () => {
    expect(PASSWORD_MIN_LENGTH).toBe(backendConstants().PASSWORD_MIN_LENGTH);
  });

  it('最少不同字符数与后端一致', () => {
    expect(PASSWORD_MIN_UNIQUE_CHARS).toBe(backendConstants().PASSWORD_MIN_UNIQUE_CHARS);
  });

  /* 前端拦纯数字、后端也拦 —— 两边一致。后端哪天放开这条，
     前端就不该再拒（前端比后端严同样是 bug：用户改了还是过不去），
     所以这里守的是「后端的态度」，不是前端的实现。 */
  it('后端仍然禁止纯数字', () => {
    expect(backendPolicySource()).toMatch(/"forbid_all_numeric":\s*True/);
  });
});
