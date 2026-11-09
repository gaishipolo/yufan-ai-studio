/**
 * 第④段控制条的枚举值。
 *
 * 独立文件而不是和组件放一起：`react-refresh` 要求"一个文件只导出组件"，
 * 否则热更新会整块重挂载（控制条重挂载等于输入焦点、滚动位置全丢）。
 */

/** 视图密度。**只影响渲染，不影响查询**——两套查询的漂移是这个仓付过一次代价的地方。 */
export type Density = 'full' | 'compact';

export const DENSITIES: ReadonlyArray<{value: Density; label: string}> = [
  {value: 'full', label: '卡片'},
  {value: 'compact', label: '列表'},
];

/** 排序白名单，与后端 `archive_service.SORTS` 同源。 */
export const SORTS: ReadonlyArray<{value: string; label: string}> = [
  {value: 'created_desc', label: '最新上传'},
  {value: 'created_asc', label: '最早上传'},
  {value: 'name_asc', label: '按名称'},
];
