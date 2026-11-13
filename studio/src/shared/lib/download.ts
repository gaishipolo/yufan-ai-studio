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

/**
 * 从已经现签好的产物地址触发下载（视频成片 / 成图 / 替换成片）。
 *
 * 这些地址可能是同源 `/static/...`，也可能是对象存储的预签名直链或供应商 CDN。
 * 不用 XHR 取 blob：跨域时 `fetch` 会被 CORS 挡住，而 `<a>`/`<img>` 本来就允许
 * 跨源。同源时 `download` 生效直接存盘；跨源时浏览器忽略 `download`、打开新标签，
 * 用户仍能看到产物——比「点了没反应」强，且不依赖任何桶级 CORS 配置。
 */
export function downloadUrl(url: string, filename: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}
