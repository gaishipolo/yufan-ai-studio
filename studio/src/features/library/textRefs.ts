import type { LibraryAssetOut } from '@/shared/api/schema';

/** 提示词里的文本引用占位符：{{Text 1}} / {{Text 2}}… 编号从 1 起，对应 textLibraryIds 的顺序 */
const TEXT_TOKEN = /\{\{\s*Text\s+(\d+)\s*\}\}/gi;

/** 是否含有非 {{Text N}} 的模板括号（替换工作台用它拒绝用户手写别的占位符） */
export function hasForeignMustache(value: string): boolean {
  const stripped = value.replace(TEXT_TOKEN, '');
  return stripped.includes('{{') || stripped.includes('}}');
}

export interface RetargetResult {
  text: string;
  /** 被删掉的引用数：越界编号或已解绑。界面据此给提示，不静默丢弃 */
  dropped: number;
}

/**
 * 绑定数组变化后同步改写提示词里的 {{Text N}}。
 *
 * 按 **id** 而不是按位置重编号：删除中间一项时，它后面的引用整体前移一位，
 * 不会出现「{{Text 3}} 却只剩 2 份绑定」这种越界；已解绑的 token 直接删掉，
 * 新增的绑定在末尾追加对应编号。一次 replace 完成，避免逐个替换互相干扰。
 */
export function retargetTextTokens(
  prompt: string,
  oldAssets: LibraryAssetOut[],
  newAssets: LibraryAssetOut[],
): RetargetResult {
  const oldIds = oldAssets.map((asset) => asset.id);
  const newIds = newAssets.map((asset) => asset.id);
  let dropped = 0;

  let text = prompt.replace(TEXT_TOKEN, (_match, digits: string) => {
    const id = oldIds[Number(digits) - 1];
    if (!id) {
      dropped += 1;
      return '';
    }
    const index = newIds.indexOf(id);
    if (index < 0) {
      dropped += 1;
      return '';
    }
    return `{{Text ${index + 1}}}`;
  });

  for (const [index, id] of newIds.entries()) {
    if (oldIds.includes(id)) continue;
    const token = `{{Text ${index + 1}}}`;
    text = text ? `${text.replace(/\s+$/, '')} ${token}` : token;
  }

  return { text, dropped };
}
