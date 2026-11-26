/**
 * 图片插槽预设：全局共用的一组「加槽位模板」，跟着人走、不跟着模板走。
 *
 * 解决的痛点：作者封装模板时，每加一个可选图片槽位都要手写一遍「选中时文案」
 * （`参考 {{Mixed 3}} 中的场景`）。这些话其实高度雷同，把名称和文案配一次，
 * 以后从「加槽位 → 图片 → 二级选项」点一下就带出来。
 *
 * ## 为什么占位符是 `{{slot:key}}`
 *
 * 后端 `compile.py::_expand_filled` 明确**推荐**在 `filled_text` 里写自引用：
 * 编译期按槽位实际位置展开成 `{{Mixed N}}`。也就是说配置页根本不需要知道
 * 新槽位会排第几 —— 写 `{{slot:key}}`，加槽位时把 `key` 换成新槽位的真实 key
 * 就够了。这样比直接算 `{{Mixed N}}` 更稳：槽位拖动排序后编号会变，`key` 不会。
 *
 * `key` 在这里是**保留字**（占位符），不是某个真实槽位的名字。
 *
 * ## 只有单张、没有整组
 *
 * 「选中时文案」只属于**可选**槽位（必填不许带文案，后端 422）；而当前槽位
 * 恒为单张（`maxCount` 恒 1，没有「全部 / 第 N 张」）。所以只有 `{{slot:key}}`
 * 一种写法，不存在整组引用。
 */
import { toast } from '@/shared/lib/toast';
import type { SpecSlotIn } from '@/shared/api/schema';

export interface ImageSlotPreset {
  id: string;
  name: string;
  /** 选中时文案，用 `{{slot:key}}` / `{{slot:key*}}` 指代这个插槽自己。 */
  filledText: string;
}

const STORAGE_KEY = 'sf_image_slot_presets';

/** 文案里的「这个插槽的素材」占位符，配置页用「插入素材引用」按钮写进去。 */
export const SELF_REF = '{{slot:key}}';

/** 首次使用给的示例。用户可以删改；删空后不再自动灌回来。 */
export const DEFAULT_PRESETS: readonly ImageSlotPreset[] = [
  { id: 'preset-front', name: '正面图', filledText: `产品正面主参考 ${SELF_REF}，锁定真实配色与可见细节` },
  { id: 'preset-views', name: '三视图', filledText: `同款产品的补充视角参考 ${SELF_REF}，仅补充其他视角实际可见的结构` },
  { id: 'preset-role', name: '角色参考', filledText: `人物身份与外貌以 ${SELF_REF} 为准，只参考角色外貌` },
  { id: 'preset-scene', name: '场景参考', filledText: `空间环境与光线以 ${SELF_REF} 为准` },
  { id: 'preset-outfit', name: '服饰参考', filledText: `服装款式与配饰以 ${SELF_REF} 为准` },
];

function isPreset(value: unknown): value is ImageSlotPreset {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.id === 'string' &&
    typeof item.name === 'string' &&
    typeof item.filledText === 'string'
  );
}

/** 读预设。坏数据整条丢掉而不是让配置页崩掉 —— 本地存储是用户可手改的。 */
export function loadPresets(): ImageSlotPreset[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_PRESETS.map((preset) => ({...preset}));
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_PRESETS.map((preset) => ({...preset}));
    return parsed.filter(isPreset).map((preset) => ({
      id: preset.id,
      name: preset.name,
      filledText: preset.filledText,
    }));
  } catch {
    return DEFAULT_PRESETS.map((preset) => ({...preset}));
  }
}

/** 存预设。浏览器禁用存储时给提示，别让作者以为改了其实没落盘。 */
export function savePresets(presets: ImageSlotPreset[]): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(presets));
    return true;
  } catch {
    toast('预设没能保存到浏览器，刷新后会恢复上一版', 'error');
    return false;
  }
}

export function newPresetId(): string {
  return `preset-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 重复插入同一预设时，给新槽位取一个**能和已有槽位区分**的名字。
 *
 * 第一个叫「角色」，第二个「角色 2」，第三个「角色 3」……取**最小可用**序号，
 * 删掉中间一个再插不会和现存的撞名（与 `nextSlotKey` 同一套思路）。
 *
 * 不禁止重复插入：作者确实可能需要两个角色槽位；要解决的只是菜单里
 * 出现两项完全一样的「角色」、作者分不出选哪个。
 */
export function nextPresetLabel(name: string, slots: SpecSlotIn[]): string {
  const base = name.trim();
  const used = new Set(slots.map((slot) => (slot.label ?? '').trim()));
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(`${base} ${index}`)) index += 1;
  return `${base} ${index}`;
}

/** 把预设的文案落到某个具体槽位上：`{{slot:key}}` → `{{slot:image_3}}`。 */
export function filledTextOf(preset: ImageSlotPreset, slotKey: string): string {
  return preset.filledText.replaceAll(SELF_REF, `{{slot:${slotKey}}}`);
}
