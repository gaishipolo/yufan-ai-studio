/**
 * 生图资源形状直接来自 openapi 生成物，不在前端手抄镜像。
 *
 * 手写版只有 6 个字段，漏掉了 status / progress / outputUrl / errorMessage / model，
 * 结果后端已经返回异步状态了，页面却只能当「同步已完成」渲染。
 * 生成物由 `app export-openapi` 维护，后端加字段这里自动跟随。
 */
import type { GenerateImageIn, ImageJobOut } from '@/shared/api/schema';

export type ImageJob = ImageJobOut;
export type GenerateImagePayload = GenerateImageIn;
