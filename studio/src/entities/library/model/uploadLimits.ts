/**
 * 上传相关的**唯一**口径：扩展名白名单与体积上限。
 *
 * 真正的闸门在后端 `api/src/infra/media_probe.py` 的 `EXTENSIONS` /
 * `MAX_BYTES` / `MAX_TEXT_BYTES`。前端另抄一份必然会漂：.gif/.svg 能选中
 * 却被服务端拒、浏览器给出空 `file.type` 的合法 .jpg 反而在前端先被拒、
 * 前端 200MB 的上限比后端 300MB 更严 → 合法文件被本地拦下。
 *
 * 所以这里**只写一份**，并把后端常量名标在注释里：改动时两边一起改。
 */

/** 可上传的类型：与后端 `EXTENSIONS` 的可上传子集一一对应 */
export type UploadKind = 'image' | 'audio' | 'text';

/**
 * 扩展名 → 类型。
 *
 * **按扩展名判，不按 `file.type` 判**：后端只认扩展名（`Content-Type` 由客户端
 * 随意填写），而浏览器对 .md/.txt 常常给出空串或 `text/plain`，对 .webp 又可能
 * 给 `application/octet-stream`。
 *
 * 不含 `.mp4`：视频走模板库原片入口（`/library/source-videos`），来源由服务端
 * 写死，前端这条上传接口不给它开口子。
 */
export const UPLOAD_KIND_BY_EXTENSION: Readonly<Record<string, UploadKind>> = {
  '.png': 'image',
  '.jpg': 'image',
  '.jpeg': 'image',
  '.webp': 'image',
  '.mp3': 'audio',
  '.wav': 'audio',
  '.txt': 'text',
  '.md': 'text',
};

/** 上传文件选择器的 accept：与上面的白名单同源 */
export const UPLOAD_ACCEPT = Object.keys(UPLOAD_KIND_BY_EXTENSION).join(',');

/** 文本上限：后端 `MAX_TEXT_BYTES`（提示词素材会被整份读进内存，单独一档） */
export const TEXT_MAX_BYTES = 1024 * 1024;

/** 非文本上限：后端 `MAX_BYTES`（前端不再写一个更小的数，免得拦下合法文件） */
export const MAX_BYTES = 300 * 1024 * 1024;

/** 文件名的小写扩展名（含点），没有扩展名返回空串 */
export function uploadExtension(filename: string): string {
  const name = filename.toLowerCase();
  const at = name.lastIndexOf('.');
  return at > 0 ? name.slice(at) : '';
}

/** 这个文件名能不能上传、能传成哪一类；不在白名单里返回 null */
export function uploadKindOf(filename: string): UploadKind | null {
  return UPLOAD_KIND_BY_EXTENSION[uploadExtension(filename)] ?? null;
}

/**
 * 文本的 UTF-8 字节数。
 *
 * 用 Blob 而不是 `content.length`：后者是**字符数**，中文正文会是字节数的 1/3，
 * 按它判 1MB 上限会放行三倍大的文件。
 */
export function textByteLength(text: string): number {
  return new Blob([text]).size;
}
