/**
 * 点一张素材之后的选中列表。四条规则，全部是"点了要有反应"：
 *
 * - 已选 → 取消（任何槽位都一样，不然选错了没法改）；
 * - 单选槽位（`max<=1`）→ 替换成它，不累加；
 * - 多选槽位没满 → 追加；
 * - 多选槽位已满 → **原样返回**（调用方据此禁用其余图块，别让用户点了没反应）。
 *
 * 放在独立文件而不是组件里：`SlotMediaPicker.tsx` 只导出组件，Fast Refresh 才
 * 不会被这条常量/函数打断（`react-refresh/only-export-components`）。
 */
export function pickWithin(selected: string[], assetId: string, max: number): string[] {
  if (selected.includes(assetId)) return selected.filter(id => id !== assetId);
  if (max <= 1) return [assetId];
  if (selected.length >= max) return selected;
  return [...selected, assetId];
}
