/**
 * 正文里的素材占位计数。
 *
 * 从 RichPromptEditor 拆出：fast-refresh 要求组件文件只导出组件，
 * 导出纯函数会让热更新失效（eslint react-refresh/only-export-components）。
 */
import { tokenPattern } from '@/shared/lib/promptTokens';

/**
 * 正文里应该画成**胶囊**的 token 有几个。
 *
 * 锚点段（默认描述）里的引用**也画成胶囊**，所以这里要一并算上 ——
 * 两边口径一致才不会每次都重绘（重绘会把正在进行的鼠标拖选打断，
 * 选区塌缩 → 框选菜单不出现 → 「取消默认描述」点不到）。
 */
export function expectedCapsuleCount(value: string): number {
  return (value.match(tokenPattern()) ?? []).length;
}
