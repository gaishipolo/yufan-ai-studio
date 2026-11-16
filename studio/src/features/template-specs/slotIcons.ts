import { FileText, Film, Image as ImageIcon, Music } from 'lucide-react';
import type { SlotType } from './specDraft';

/**
 * 槽位类型 → 图标。
 *
 * 放在这里而不是各写一份：槽位清单和提示词框都要画同一个图标，
 * 两处各写一遍，改类型映射时必有一处漏掉（表现就是「清单上是成片图标、
 * 提示词里是图片图标」）。
 *
 * 用图标而不是文字 chip 的理由：文字 chip（"成片"/"图片"/"音频"）在 14px
 * 的行里跟展示名挤成一团，图标一眼就分得出，而且省地方。
 */
export const SLOT_TYPE_ICONS = {
  video: Film,
  image: ImageIcon,
  audio: Music,
  text: FileText,
} as const;

export type SlotTypeIcon = (typeof SLOT_TYPE_ICONS)[SlotType];
