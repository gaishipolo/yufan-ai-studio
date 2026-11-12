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
 *
 * 三种情况**原地保留**，不动用户写好的位置：
 * 1. 编号此前没有绑定过（用户先手写 `{{Text 1}}` 再挑素材）——只要新顺序里
 *    还有这一位，它就自动指向新绑的那份，没必要清掉再追加到末尾；
 * 2. 已经有对应编号的 token 存在时，新绑定不再重复追加；
 * 3. **解绑一份、同时在同一位绑上新的**：token 留在原位指向新绑的那份，
 *    不被摘掉再补到句尾（否则「把 {{Text 1}} 的卖点」会变成
 *    「把  的卖点 {{Text 1}}」）。
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
    const slot = Number(digits);
    const id = oldIds[slot - 1];
    if (!id) {
      // 该位此前没有绑定：用户手写的占位符，范围仍合法就留着
      if (slot >= 1 && slot <= newIds.length) return `{{Text ${slot}}}`;
      dropped += 1;
      return '';
    }
    // **这一位被新绑定的素材顶上了** → token 原地保留，不动位置。
    //
    // 场景：解绑 A、同时绑定 B，B 落在同一个位置（列表里第 1 位）。
    // 按 id 重编号的话，A 被删 → token 被摘掉 → 末尾的追加循环再把 B 的
    // `{{Text 1}}` 补到句尾，「把 {{Text 1}} 的卖点」变成「把  的卖点 {{Text 1}}」，
    // 与文件头「原地保留」的承诺不符。
    //
    // 判据是「**新位是不是新加的**」：只有新加的资产才享有原地保留。
    // 若新位上是别的旧资产（用户换序），那一位仍是老位置的重编号，
    // 按 id 走；否则会出现两个 token 指向同一份文案。
    const newcomer = newIds[slot - 1];
    if (newcomer && !oldIds.includes(newcomer)) {
      return `{{Text ${slot}}}`;
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
    // 提示词里已经有这一位（手写或上一步重编号来的）就不再追加
    if (new RegExp(`\\{\\{\\s*Text\\s+${index + 1}\\s*\\}\\}`, 'i').test(text)) continue;
    const token = `{{Text ${index + 1}}}`;
    text = text ? `${text.replace(/\s+$/, '')} ${token}` : token;
  }

  return { text, dropped };
}
