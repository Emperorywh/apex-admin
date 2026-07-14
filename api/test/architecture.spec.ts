import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

/*
 * 架构测试只读扫描源码 import，固定 SPEC-0001 的分层与模块边界。
 * 它不依赖运行时 DI 图，违反依赖方向会直接让 CI 失败。
 */
const sourceRoot = join(__dirname, '..', 'src');
const files = collectTypeScriptFiles(sourceRoot);

describe('源码架构边界', () => {
  it('Domain 不导入 NestJS、Prisma、Presentation 或 Infrastructure', () => {
    expectViolations(
      files.filter((file) => file.includes(`${join('domain', '')}`)),
      /from ['"](?:@nestjs|@prisma)|from ['"][^'"]*(?:presentation|infrastructure)/,
    );
  });

  it('Application 不导入 Prisma 或 HTTP DTO', () => {
    expectViolations(
      files.filter((file) => file.includes(`${join('application', '')}`)),
      /from ['"](?:@prisma|[^'"]*presentation\/http)/,
    );
  });

  it('Presentation 不导入 Prisma Adapter', () => {
    expectViolations(
      files.filter((file) => file.includes(`${join('presentation', '')}`)),
      /from ['"](?:@prisma|[^'"]*infrastructure\/persistence)/,
    );
  });

  it('IAM 外部只通过 public-api 或 Composition Root 导入模块契约', () => {
    const violations = files
      .filter((file) => !file.includes(join('modules', 'iam')))
      .flatMap((file) => {
        const source = readFileSync(file, 'utf8');
        const internalImport = /from ['"][^'"]*modules\/iam\/(?!public-api|iam\.module)[^'"]+['"]/g;
        return [...source.matchAll(internalImport)].map(() => relative(sourceRoot, file));
      });
    expect(violations).toEqual([]);
  });

  it('禁止 forwardRef 隐藏循环依赖', () => {
    expectViolations(files, /\bforwardRef\s*\(/);
  });
});

function collectTypeScriptFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) return collectTypeScriptFiles(path);
    return path.endsWith('.ts') && !path.endsWith('.spec.ts') ? [path] : [];
  });
}

function expectViolations(candidates: readonly string[], pattern: RegExp): void {
  const violations = candidates
    .filter((file) => pattern.test(readFileSync(file, 'utf8')))
    .map((file) => relative(sourceRoot, file));
  expect(violations).toEqual([]);
}
