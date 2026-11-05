import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, expectTypeOf, it } from 'vitest';
import type { MeOut, RoleBriefOut, UserOut } from '@/shared/api/schema';
import type { Me, RoleBrief, User } from './types';

/** 用 cwd 拼路径：`import.meta.url` 在 vitest 里会被 vite 改写成开发服务器地址 */
const TYPES_SOURCE = join(process.cwd(), 'src/entities/user/model/types.ts');

/* ------------------------------------------------------------------
 * 契约类型的「反向」检查
 *
 * 这些断言在运行时什么都不做（`expectTypeOf` 只存在于类型层），
 * 全部价值来自 `tsc -b`：谁把 `types.ts` 改回手抄的 interface，
 * 或者后端加字段后前端没跟上，这里就会红。
 *
 * 为什么需要它：`User` / `Me` 曾经是手抄的，与生成物的可选性对不上，
 * 于是两边**永远无法互相赋值**，编译器也就永远没机会比对 ——
 * 后端某天真的不发 `isActive` 时，`can()` 与列表筛选会悄悄读 undefined。
 * ------------------------------------------------------------------ */

describe('用户类型必须从生成物派生', () => {
  it('User 可以赋给 UserOut：只收窄可选性，没有凭空造字段', () => {
    expectTypeOf<User>().toMatchTypeOf<UserOut>();
  });

  it('生成物的字段一个都没漏（防止手抄时少写一个）', () => {
    expectTypeOf<Exclude<keyof UserOut, keyof User>>().toEqualTypeOf<never>();
  });

  it('Me 是 User 的超集，也多出 permissions', () => {
    expectTypeOf<Me>().toMatchTypeOf<User>();
    expectTypeOf<Me>().toMatchTypeOf<MeOut>();
    expectTypeOf<Me['permissions']>().toEqualTypeOf<string[]>();
  });

  it('RoleBrief 没有漏字段（isSuperuser 被收窄为必填）', () => {
    expectTypeOf<Exclude<keyof RoleBriefOut, keyof RoleBrief>>().toEqualTypeOf<never>();
    expectTypeOf<RoleBrief['isSuperuser']>().toEqualTypeOf<boolean>();
  });

  /* 上面几条只在"仍然是派生"时才成立。这条直接看源码，把"退回手抄"这件事也挡住 ——
     手抄版只要字段写全，类型断言依然会过，可它会在后端加字段时静默落后。 */
  it('types.ts 确实 import 了生成物，而不是另写一份 interface', () => {
    const source = readFileSync(TYPES_SOURCE, 'utf8');

    expect(source).toContain("from '@/shared/api/schema'");
    expect(source).not.toMatch(/^export interface User\b/m);
    expect(source).not.toMatch(/^export interface Me\b/m);
  });
});
