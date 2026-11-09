import { LibraryWorkspace } from '@/features/library/LibraryWorkspace';

/**
 * 资产库 v2 的页面壳。
 *
 * 与 `/assets`（现役素材库）并存：两边的表、权限、接口、存储前缀完全隔离，
 * 切换时只改 `modules/library/module.ts` 里 `load()` 的一行。
 */
export default function LibraryPage() {
  return <LibraryWorkspace />;
}
