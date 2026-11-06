/**
 * 用户资料与口令的校验规则 —— 唯一真相。
 *
 * 为什么要收成一处：
 * 1. `PASSWORD_MIN_LENGTH` 原本在 `UserFormModal`、`ResetPasswordModal`、
 *    `entities/session/model/types`、`modules/auth/mock.ts` 各写了一个 `10`。
 *    后端把策略改成 12 位时必然漏改一处，于是「建号页说 10 位、重置页说 12 位」。
 * 2. 邮箱正则原本在 `UserFormModal` 与 `ProfileModal` 各写一遍，且写法不同，
 *    同一串地址在一个入口能过、另一个入口报错。
 *
 * 这里只放**纯函数**：不读环境、不发请求。规则以后端为准，
 * 前端这一层只负责「少一次往返」，永远不能替代服务端校验。
 *
 * ## 为什么**不**调用 `GET /identity/password-policy`（2026-11-06 拍板）
 *
 * 那个接口存在且匿名可访问，前端一行都没调 —— 这是**决定**，不是遗漏（FE5-02）：
 * 调它要付一次冷启动请求，还得为「接口挂了」再准备一份兜底常量，换来的只是文案自动跟随。
 *
 * 代价是「后端改数字、前端不知道」。所以一致性改由测试守：
 * `validation.test.ts` 末尾解析后端 `api/src/modules/identity/policy.py`，
 * 逐条比对下面的常量。后端把 10 改成 12，前端测试立刻红 —— 这满足
 * `docs/CODE_STYLE.md` 对「服务端策略不要硬编码」的要求（调接口**或**源码比对，二选一）。
 *
 * 已知偏差（故意保留）：后端 `PASSWORD_MAX_LENGTH = 128`（防 scrypt 输入被当 DoS），
 * 前端不拦超长，交给后端 422。要拦的话在 `checkPassword` 里加一行，顺手把
 * 「最长 128 位」也写进三个弹窗的 hint。
 */

/**
 * 口令规则 —— 后端子集，用来少一次往返，**不是**安全边界。
 *
 * 三条与后端 `modules/identity/policy.py::check_password_strength` 对齐：
 *   1. 长度 ≥ `PASSWORD_MIN_LENGTH`
 *   2. 不能全是数字
 *   3. 不同字符数 ≥ `PASSWORD_MIN_UNIQUE_CHARS`（`aaaaaaaaa1` 这类凑长度的口令）
 *
 * 后端将来若再加一条（例如「不能含用户名」），这里**不需要**同步也能正确工作：
 * 前端放行的口令仍会被 422 拒绝，代价只是一次往返。反过来，前端比后端更严才是 bug
 * —— 用户按提示改了还是过不去。所以改这里的前提永远是「后端也这么判」。
 */

/** 与后端 `USERNAME_PATTERN` 一致：3-64 位字母、数字或 . _ -，入库前转小写 */
export const USERNAME_PATTERN = /^[a-zA-Z0-9_.-]{3,64}$/;

/** 宽松的邮箱形态校验：只挡一眼错的输入，真实可达性由后续验证邮件负责 */
export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;

/** 与后端 `PASSWORD_MIN_LENGTH` 一致 */
export const PASSWORD_MIN_LENGTH = 10;

/** 与后端 `PASSWORD_MIN_UNIQUE_CHARS` 一致：重复字符凑长度的口令要拒 */
export const PASSWORD_MIN_UNIQUE_CHARS = 4;

/** 合法的头像地址：http(s) 绝对地址，或站内以 `/` 开头的相对路径 */
const AVATAR_URL_PATTERN = /^(https?:\/\/|\/)/i;

/**
 * 口令校验。返回错误文案；合法返回 `null`。
 *
 * 口令有**三个写入口**，都必须先过这里，漏一个就回到「同一个口令在 A 页能过、
 * 在 B 页报错」：
 *   1. `features/user-form/UserFormModal`（管理员建号，初始密码）
 *   2. `features/user-security/ResetPasswordModal`（管理员重置）
 *   3. `features/auth/ChangePasswordModal`（本人改密）
 * `validation.test.ts` 有一条用例直接扫这三个文件，确认它们都调了本函数 ——
 * 新增第四处时请一并加进那份名单，别让"忘了校验"重新变成可能。
 *
 * 为什么不包成 `<PasswordField>`：校验发生在**提交时**且要按角色给文案
 * （「初始密码」/「新密码」），塞进输入框组件只能管到"单字段格式"，
 * 管不到"两次输入是否一致"和"不能与旧密码相同"，反而让人以为它管全了。
 *
 * @param value 待校验口令
 * @param label 用于文案的角色名，如「初始密码」「新密码」
 */
export function checkPassword(value: string, label = '密码'): string | null {
  if (value.length < PASSWORD_MIN_LENGTH) return `${label}至少 ${PASSWORD_MIN_LENGTH} 位`;
  if (/^\d+$/.test(value)) return `${label}不能是纯数字`;
  if (new Set(value).size < PASSWORD_MIN_UNIQUE_CHARS) {
    return `${label}过于简单（不要用重复字符凑长度）`;
  }
  return null;
}

/**
 * 邮箱校验：留空或 `null`（PATCH 里表示「清空」）都视为合法。
 */
export function checkEmail(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim();
  if (!trimmed) return null;
  return EMAIL_PATTERN.test(trimmed) ? null : '邮箱格式不正确';
}

/** 头像地址校验：留空或 `null` 视为「不设置」 */
export function checkAvatarUrl(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim();
  if (!trimmed) return null;
  if (trimmed.length > 512) return '头像地址过长（上限 512 字符）';
  return AVATAR_URL_PATTERN.test(trimmed) ? null : '头像地址需以 http(s):// 或 / 开头';
}
