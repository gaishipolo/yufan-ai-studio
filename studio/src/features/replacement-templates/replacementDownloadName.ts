/** 下载名称不含路径和控制字符，调用 ID 区分相同目标的不同成片。 */
export function replacementDownloadName(target: string, runId: string): string {
  const cleaned = Array.from(target).filter(char => char.charCodeAt(0) >= 32).join('');
  return `${cleaned.replace(/[<>:"/\\|?*]/g, '_') || '替换成片'}-${runId}.mp4`;
}
