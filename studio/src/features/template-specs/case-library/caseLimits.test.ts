/** 案例表单的输入上限必须与后端契约一致。
 *
 * 后端 `TemplateCaseCreateIn` / `TemplateCaseUpdateIn` 的上限是唯一权威
 * （`api/src/modules/template_pack/case_schema.py`）。前端写窄了用户敲到一半
 * 就被截断、又看不到原因；写宽了则要到提交时才被 400 打回。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const STUDIO_ROOT = join(__dirname, '../../../..');
const API_CASE_SCHEMA = join(
  STUDIO_ROOT, '..', 'api', 'src', 'modules', 'template_pack', 'case_schema.py',
);

function backendLimit(field: 'name' | 'description'): number {
  const source = readFileSync(API_CASE_SCHEMA, 'utf8');
  const match = source.match(new RegExp(`\\b${field}: str[^\\n]*max_length=(\\d+)`));
  expect(match, `后端 case_schema.py 里找不到 ${field} 的 max_length`).not.toBeNull();
  return Number(match![1]);
}

describe.each([
  'PublishCaseDialog.tsx',
  'CaseEditorDialog.tsx',
])('%s 的案例字段上限', (file) => {
  const source = readFileSync(join(__dirname, file), 'utf8');

  it('名称上限与后端一致', () => {
    expect(source).toContain(`maxLength={${backendLimit('name')}}`);
  });

  it('描述上限与后端一致', () => {
    expect(source).toContain(`maxLength={${backendLimit('description')}}`);
  });
});
