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
 * 下载一个**已经现签好**的产物地址（视频成片 / 成图 / 替换成片）。
 *
 * 这些地址可能是同源 `/static/...`，也可能是对象存储的预签名直链
 * （`http://192.168.77.64:9000/...`）或供应商 CDN。
 *
 * ## 为什么不直接 `<a download href=远程地址>`
 *
 * `download` 属性在**跨源**地址上会被浏览器忽略（HTML 规范如此），
 * 这时如果还写了 `target="_blank"`，表现就是「点了在新标签打开视频」——
 * 用户要的是存盘，拿到的却是一个播放器页面，还得自己右键另存。
 * 原先的注释把这条当成「比点了没反应强」的取舍，可它其实是可以修好的：
 * 产物桶的 CORS 已经放行了站点源（`Access-Control-Allow-Origin` 实测有值）。
 *
 * 所以改成**先取字节、再用 `blob:` URL 存盘**。`blob:` 是同源协议，
 * `download` 一定生效，文件名也由我们说了算（跨源 `<a download>` 连文件名
 * 都会被忽略，存下来是一串 id）。
 *
 * 全程用裸 `fetch` 而不是项目的 `api` 实例：后者会带上 `Authorization`
 * 和自定义头，把「简单请求」变成要预检的请求，而预签名 URL 本来就不需要
 * 任何头（签名在 query 里）。不带 credentials，也避开与
 * `Access-Control-Allow-Credentials` 的互斥。
 *
 * 取不到字节时（桶没配 CORS、网络中断、签名过期）退回让浏览器自己打开 ——
 * 至少能看到产物，而不是点了没反应。
 */
export async function downloadUrl(url: string, filename: string): Promise<void> {
  try {
    const response = await fetch(url, { credentials: 'omit' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    saveBlob(await response.blob(), filename);
  } catch {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }
}
