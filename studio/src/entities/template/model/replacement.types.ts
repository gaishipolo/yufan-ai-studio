import type { PrepareReplacement, ReplacementRunOut } from '@/shared/api/schema';
export type ReplacementKind = PrepareReplacement['kind'];
export type ReplacementRun = ReplacementRunOut;
export type ReplacementInput = PrepareReplacement;
export function replacementKind(value: string | null): ReplacementKind {
  return value === 'product' || value === 'scene' ? value : 'character';
}
