/**
 * 触发浏览器保存 Blob。
 *
 * 对象 URL 不能在 click 之后立刻回收：部分浏览器要到下一轮事件循环才开始读取，
 * 提前 revoke 会得到一个 0 字节文件，而这种失败既不报错也不出现在任何日志里。
 * 因此延迟取 1 秒而不是 0 毫秒——`setTimeout(0)` 仍然是同一次点击之后最近的宏任务，
 * 对大文件不足以覆盖读取耗时。
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
