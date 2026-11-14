import { SpecLibrary } from '@/features/template-library/SpecLibrary';
import { PageHeader } from '@/shared/ui';

/**
 * 模板库：填表，不定义模板。
 *
 * 左边选已发布的模板，右边把槽位填满 → 生成。模板本身怎么定，回「模板规格」改。
 * 两栏常驻是刻意的：换模板只换右栏内容，不用来回跳页，填到一半也不会丢上下文。
 */
export default function LibraryPage() {
  return (
    <>
      <PageHeader title="模板库" />
      <SpecLibrary />
    </>
  );
}
