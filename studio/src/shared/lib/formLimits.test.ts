/** 前端输入上限必须与后端契约一致。 */
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {describe, expect, it} from 'vitest';
import {FORM_LIMITS} from './formLimits';

const STUDIO_ROOT = join(__dirname, '../../..');
const OPENAPI = join(STUDIO_ROOT, '..', 'api', 'docs', 'openapi.json');

type Schema = {properties?: Record<string, {maxLength?: number}>};

const contract = JSON.parse(readFileSync(OPENAPI, 'utf8')) as {
  components: {schemas: Record<string, Schema>};
};

function backendLimit(schema: string, field: string): number {
  const limit = contract.components.schemas[schema]?.properties?.[field]?.maxLength;
  expect(limit, `openapi.json 的 ${schema}.${field} 没有 maxLength`).toBeTypeOf('number');
  return limit as number;
}

describe('表单输入上限', () => {
  it.each([
    ['TemplateSpecIn', 'name', FORM_LIMITS.specName],
    ['TemplateSpecIn', 'prompt', FORM_LIMITS.specPrompt],
    ['SpecGroupIn', 'label', FORM_LIMITS.specGroupLabel],
    ['SpecSlotIn', 'label', FORM_LIMITS.slotLabel],
    ['SpecSlotIn', 'filledText', FORM_LIMITS.slotText],
    ['SpecSlotIn', 'emptyText', FORM_LIMITS.slotText],
    ['GenerateImageIn', 'prompt', FORM_LIMITS.imagePrompt],
  ] as const)('%s.%s 与契约一致', (schema, field, expected) => {
    expect(backendLimit(schema, field)).toBe(expected);
  });
});