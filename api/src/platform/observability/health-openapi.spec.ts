import { VersioningType, type INestApplication } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule, type OpenAPIObject } from '@nestjs/swagger';
import { Test } from '@nestjs/testing';
import type { App } from 'supertest/types';
import { HealthController } from './health.controller';

/*
 * 健康检查契约测试扫描真实 Controller，保证平台端点与业务端点使用同一响应文档规则。
 * 数据库探针使用空替身，测试不连接外部服务，也不启动监听端口。
 */
describe('健康检查 OpenAPI 响应契约', () => {
  let app: INestApplication<App>;
  let document: OpenAPIObject;

  beforeAll(async () => {
    const module = await Test.createTestingModule({ controllers: [HealthController] })
      .useMocker(() => ({}))
      .compile();
    app = module.createNestApplication();
    app.enableVersioning({ type: VersioningType.URI });
    await app.init();
    document = SwaggerModule.createDocument(app, new DocumentBuilder().build());
  });

  afterAll(async () => {
    await app.close();
  });

  it.each([
    ['/v1/health/live', '200'],
    ['/v1/health/ready', '200'],
  ] as const)('%s 声明结构化成功响应', (path, status) => {
    expect(readSchema(document, path, status, 'application/json')).toEqual({
      $ref: '#/components/schemas/HealthResponseDto',
    });
  });

  it('readiness 声明 503 Problem Details 响应', () => {
    expect(
      readSchema(document, '/v1/health/ready', '503', 'application/problem+json'),
    ).toEqual({ $ref: '#/components/schemas/ProblemDetailsDto' });
  });
});

/*
 * 此辅助函数只负责收窄 OpenAPI 联合类型并读取响应 schema。
 * 任一层级缺失都会产生包含路径和状态码的明确失败信息。
 */
function readSchema(
  document: OpenAPIObject,
  path: string,
  status: string,
  contentType: string,
) {
  const response = document.paths[path]?.get?.responses[status];
  if (!response || '$ref' in response) {
    throw new Error(`GET ${path} 缺少 ${status} 响应`);
  }
  const schema = response.content?.[contentType]?.schema;
  if (!schema) {
    throw new Error(`GET ${path} 的 ${status} 响应缺少 ${contentType} schema`);
  }
  return schema;
}
