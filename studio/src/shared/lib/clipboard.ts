/**
 * 复制到剪贴板。**返回成功与否，不抛异常**。
 *
 * `navigator.clipboard` 在 http 页面、无用户手势、iframe 里都可能直接 reject，
 * 而"复制失败"不应该让整个操作（例如跳转到生图页）中断——调用方的正确做法是
 * 记一次失败、继续做正事，然后在提示里说清"没复制上"。所以这里统一吞掉异常。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard?.writeText(text);
    return true;
  } catch {
    return false;
  }
}
