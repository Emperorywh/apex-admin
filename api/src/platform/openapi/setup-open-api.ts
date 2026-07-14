import type { INestApplication } from '@nestjs/common';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';

export const OPEN_API_PATH = 'docs';
export const OPEN_API_JSON_PATH = 'docs/openapi.json';
export const OPEN_API_YAML_PATH = 'docs/openapi.yaml';

const WILDCARD_LISTEN_HOSTS = new Set(['0.0.0.0', '[::]']);

/*
 * OpenAPI 是平台层 HTTP 能力，只读取 Nest 路由元数据，不依赖任何业务用例。
 * 文档端点集中在此处定义，避免启动入口、业务模块和测试分别拼接路径。
 */
export function setupOpenApi(app: INestApplication): void {
  const documentOptions = new DocumentBuilder()
    .setTitle('Apex Admin API')
    .setDescription('Apex Admin 后台管理系统 HTTP API')
    .setVersion('1.0.0')
    .addBearerAuth(
      {
        type: 'http',
        scheme: 'bearer',
        bearerFormat: 'JWT',
        description: '请输入登录接口签发的 access token',
      },
      'access-token',
    )
    .addCookieAuth(
      'refresh_token',
      {
        type: 'apiKey',
        in: 'cookie',
        description: '登录或刷新接口写入的 HttpOnly refresh token',
      },
      'refresh-token',
    )
    .build();

  const documentFactory = () =>
    SwaggerModule.createDocument(app, documentOptions, {
      autoTagControllers: false,
      operationIdFactory: (controllerKey, methodKey) =>
        `${controllerKey.replace(/Controller$/, '')}.${methodKey}`,
    });

  SwaggerModule.setup(OPEN_API_PATH, app, documentFactory, {
    customSiteTitle: 'Apex Admin API 文档',
    jsonDocumentUrl: OPEN_API_JSON_PATH,
    yamlDocumentUrl: OPEN_API_YAML_PATH,
    raw: ['json', 'yaml'],
    swaggerOptions: {
      persistAuthorization: true,
    },
  });
}

/*
 * 监听通配地址用于接收流量，但不是适合复制到浏览器的访问地址。
 * 启动日志仅将通配主机转换为 localhost，其他显式主机保持原样。
 */
export function getOpenApiUrl(applicationUrl: string): string {
  const baseUrl = applicationUrl.endsWith('/') ? applicationUrl : `${applicationUrl}/`;
  const openApiUrl = new URL(OPEN_API_PATH, baseUrl);

  if (WILDCARD_LISTEN_HOSTS.has(openApiUrl.hostname)) {
    openApiUrl.hostname = 'localhost';
  }

  return openApiUrl.toString();
}
