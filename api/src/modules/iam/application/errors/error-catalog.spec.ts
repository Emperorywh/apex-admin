import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { IAM_ERROR_CODES } from './iam.error';

/*
 * 错误目录测试约束代码事实来源、定稿规格与 OpenAPI 同步。
 * VALIDATION/INTERNAL 是平台错误，其余代码必须来自 IAM_ERROR_CODES。
 */
describe('稳定错误码目录', () => {
  const specification = readFileSync(
    join(process.cwd(), 'docs', 'SPEC_0001_auth-rbac.md'),
    'utf8',
  );
  const openApi = readFileSync(join(process.cwd(), 'docs', 'openapi.yaml'), 'utf8');
  const codes = ['VALIDATION_FAILED', ...IAM_ERROR_CODES, 'INTERNAL_SERVER_ERROR'];

  it.each(codes)('%s 同时存在于规格和 OpenAPI', (code) => {
    expect(specification).toContain(`\`${code}\``);
    expect(openApi).toContain(`- ${code}`);
  });

  it('IAM 错误码没有重复项', () => {
    expect(new Set(IAM_ERROR_CODES).size).toBe(IAM_ERROR_CODES.length);
  });
});
