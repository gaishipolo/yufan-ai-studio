import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * 合并 className，后写的 Tailwind 类覆盖前面的冲突项。
 *
 * ## 为什么必须先教 tailwind-merge 认得本项目的字号
 *
 * tailwind-merge 只认识 Tailwind **默认**的字号名（xs/sm/base/lg…）。
 * 本项目的字阶是自定义的（micro/meta/body/lead/title/display/h1/stat），
 * tailwind-merge 认不出来，就会把 `text-body` 当成**颜色类** ——
 * 于是 `cn('text-body text-ink')` 被判定为两个颜色冲突，
 * 后面的颜色吃掉前面的字号（反过来也一样）。
 *
 * 真实后果（全站级，而且编译期不报错、测试也看不出来）：
 *
 * - 11px 的 chip 实际渲染成 13px（字号被颜色吃掉）；
 * - 34px 的统计数字掉回正文大小；
 * - 黑色主按钮上的 `text-white` 被吃掉，文字变成墨色 ——
 *   在近黑底上等于消失。
 *
 * 只有不走 `cn()` 的静态 className（比如页头 h1）侥幸躲过，
 * 于是同一个界面里「该是 19px 的标题」和「该是 13px 的正文」长得一样，
 * 排版层级根本没建立起来。
 *
 * 这里把自定义字号登记进 tailwind-merge：字号归字号组，颜色归颜色组，
 * 谁也不吃谁。
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        { text: ['micro', 'meta', 'body', 'lead', 'title', 'display', 'h1', 'stat'] },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
