/**
 * 正文里的素材占位正则与胶囊计数。
 *
 * 从 RichPromptEditor 拆出：fast-refresh 要求组件文件只导出组件，
 * 导出纯函数会让热更新失效（eslint react-refresh/only-export-components）。
 *
 * 两边允许空格（`{{ slot:key }}`）：后端与 `specDraft` 都是容空格版本，
 * 这里写成严格版就会漏掉老草稿里的带空格引用。
 */
export const TOKEN_RE =
  /\{\{\s*(?:slot:[a-z][a-z0-9_]{0,39}|Mixed\s+\d+)(?:\*|\.\d+)?\s*\}\}/g;

/**
 * 正文里应该画成**胶囊**的 token 有几个。
 *
 * 锚点段（默认描述）里的引用**也画成胶囊**，所以这里要一并算上 ——
 * 两边口径一致才不会每次都重绘（重绘会把正在进行的鼠标拖选打断，
 * 选区塌缩 → 框选菜单不出现 → 「取消默认描述」点不到）。
 */
export function expectedCapsuleCount(value: string): number {
  return (value.match(TOKEN_RE) ?? []).length;
}
