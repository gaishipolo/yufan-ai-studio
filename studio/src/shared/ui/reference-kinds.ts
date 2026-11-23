/**
 * 参考素材的**类型元数据**：界面上的称呼、量词、可上传格式。
 *
 * ## 为什么单独一个文件
 *
 * `ReferenceImages.tsx` 必须只导出组件 —— 同一文件再导出常量会让 React Fast
 * Refresh 失效（`react-refresh/only-export-components`），改一行界面就整页重挂、
 * 表单里填了一半的内容全丢。而这份元数据**调用点也要用**（页面的「最多 5 张图」
 * 提示、拖拽时的拒绝文案），不能藏在组件文件里。
 *
 * ## 为什么 `accept` 与后端白名单同源
 *
 * 文件选择器放开的格式必须是后端真的收的：放开 `.gif` 会让用户选中一张
 * 上传成功的图，到提交时才被「格式不支持」拒掉。后端的真相是
 * `api/src/infra/media_probe.py` 的 `EXTENSIONS`，这里只列参考素材用得上的几种。
 */

export interface ReferenceKindMeta {
  /** 完整称呼（「图」「音频」「视频」），拼提示文案用 */
  label: string;
  /** 缩略图角标上的一个字（「图1」「音2」），越短越好 */
  short: string;
  /** 量词（「5 张图」「3 段音频」） */
  unit: string;
  /** 文件选择器认的 MIME */
  accept: string[];
}

export const REFERENCE_KIND_META: Record<string, ReferenceKindMeta> = {
  image: {
    label: '图',
    short: '图',
    unit: '张',
    // 只收供应商认的三种：png / jpeg / webp（与后端白名单一致）
    accept: ['image/png', 'image/jpeg', 'image/webp'],
  },
  video: { label: '视频', short: '视', unit: '段', accept: ['video/mp4'] },
  audio: { label: '音频', short: '音', unit: '段', accept: ['audio/mpeg', 'audio/wav'] },
};

/** 没声明类型的素材一律当图片（生图页只有图片）。 */
export function kindOf(item: { kind?: string }): string {
  return item.kind ?? 'image';
}

/** 未知类型不抛错，按原样显示 —— 新增一类时界面不至于整块崩掉。 */
export function metaOf(kind: string): ReferenceKindMeta {
  return REFERENCE_KIND_META[kind] ?? { label: kind, short: kind, unit: '个', accept: [] };
}
