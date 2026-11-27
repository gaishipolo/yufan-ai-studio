/**
 * 表单输入的**长度上限**（与后端契约一致）。
 *
 * 前端以前各写一个数字：写窄了用户敲到一半被截断，写宽了要到提交/发布才 422。
 * 这里只放「输入框需要拦」的几个；数值由 `formLimits.test.ts` 对着
 * `api/docs/openapi.json` 钉住，改后端后必须重生成契约，否则测试先红。
 */
export const FORM_LIMITS = {
  /** `TemplateSpecIn.name` */
  specName: 160,
  /** `TemplateSpecIn.prompt` / `randomPrompt`（存草稿上限） */
  specPrompt: 20000,
  /** `SpecGroupIn.label` */
  specGroupLabel: 80,
  /** `SpecSlotIn.label` */
  slotLabel: 80,
  /** `SpecSlotIn.filledText` / `emptyText` */
  slotText: 2000,
  /** `GenerateImageIn.prompt`（生图端点，不是视频任务的 7000） */
  imagePrompt: 2000,
} as const;