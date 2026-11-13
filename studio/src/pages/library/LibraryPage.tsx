import { LibraryWorkspace } from '@/features/library/LibraryWorkspace';

/**
 * 资产库的页面壳。
 *
 * v1 素材库（`/assets`、`assets` 表）已于 2026-11-13 彻底下线，这是唯一入口。
 */
export default function LibraryPage() {
  return <LibraryWorkspace />;
}
