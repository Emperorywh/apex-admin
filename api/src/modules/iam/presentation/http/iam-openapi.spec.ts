import { VersioningType, type INestApplication } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule, type OpenAPIObject } from '@nestjs/swagger';
import { Test } from '@nestjs/testing';
import type { App } from 'supertest/types';
import { AuthController } from './auth.controller';
import { UsersController } from './users.controller';

/*
 * 契约测试直接扫描真实 Controller 元数据，防止后续新增接口时只声明请求模型。
 * 所有用例依赖都使用空替身，因为 OpenAPI 生成只需要路由与 DTO 元数据，不执行业务逻辑。
 */
describe('IAM OpenAPI 响应契约', () => {
  let app: INestApplication<App>;
  let document: OpenAPIObject;

  beforeAll(async () => {
    const module = await Test.createTestingModule({
      controllers: [AuthController, UsersController],
    })
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
    ['/v1/auth/login', 'post', '200', 'AuthResponseDto'],
    ['/v1/auth/refresh', 'post', '200', 'AuthResponseDto'],
    ['/v1/auth/me', 'get', '200', 'CurrentUserResponseDto'],
    ['/v1/users', 'post', '201', 'UserResponseDto'],
    ['/v1/users', 'get', '200', 'UserPageResponseDto'],
    ['/v1/users/{id}', 'get', '200', 'UserResponseDto'],
    ['/v1/users/{id}/role', 'patch', '200', 'UserResponseDto'],
  ] as const)(
    '%s %s 的成功响应引用 %s schema',
    (path, method, status, schemaName) => {
      expect(readResponseSchema(document, path, method, status, 'application/json')).toEqual({
        $ref: `#/components/schemas/${schemaName}`,
      });
    },
  );

  it.each([
    ['/v1/auth/logout', 'post'],
    ['/v1/users/{id}/disable', 'post'],
    ['/v1/users/{id}/enable', 'post'],
  ] as const)('%s %s 明确声明 204 无响应体', (path, method) => {
    const response = readResponse(document, path, method, '204');
    expect(response).not.toHaveProperty('content');
  });

  it.each([
    ['/v1/auth/login', 'post', '400'],
    ['/v1/auth/me', 'get', '401'],
    ['/v1/users', 'post', '422'],
    ['/v1/users/{id}', 'get', '404'],
    ['/v1/users/{id}/role', 'patch', '409'],
    ['/v1/users/{id}/enable', 'post', '500'],
  ] as const)(
    '%s %s 的错误响应使用 Problem Details',
    (path, method, status) => {
      expect(
        readResponseSchema(
          document,
          path,
          method,
          status,
          'application/problem+json',
        ),
      ).toEqual({ $ref: '#/components/schemas/ProblemDetailsDto' });
    },
  );

  it('响应组件包含用户字段、分页元数据和字段级错误', () => {
    expect(document.components?.schemas).toMatchObject({
      UserDto: {
        properties: {
          id: { type: 'string', format: 'uuid' },
          email: { type: 'string', format: 'email' },
          createdAt: { type: 'string', format: 'date-time' },
          updatedAt: { type: 'string', format: 'date-time' },
        },
      },
      UserPageResponseDto: {
        properties: {
          data: { type: 'array' },
          meta: { $ref: '#/components/schemas/UserPageMetaDto' },
        },
      },
      ProblemDetailsDto: {
        properties: {
          errors: { type: 'array' },
        },
      },
    });
  });
});

type DocumentedMethod = 'get' | 'post' | 'patch';

/*
 * OpenAPI ResponseObject 与 ReferenceObject 在类型上是联合体。
 * 测试辅助函数集中完成存在性和引用形状收窄，使各断言只表达业务契约。
 */
function readResponse(
  document: OpenAPIObject,
  path: string,
  method: DocumentedMethod,
  status: string,
) {
  const operation = document.paths[path]?.[method];
  const response = operation?.responses[status];
  if (!response || '$ref' in response) {
    throw new Error(`${method.toUpperCase()} ${path} 缺少 ${status} 响应`);
  }
  return response;
}

function readResponseSchema(
  document: OpenAPIObject,
  path: string,
  method: DocumentedMethod,
  status: string,
  contentType: string,
) {
  const schema = readResponse(document, path, method, status).content?.[contentType]
    ?.schema;
  if (!schema) {
    throw new Error(
      `${method.toUpperCase()} ${path} 的 ${status} 响应缺少 ${contentType} schema`,
    );
  }
  return schema;
}
