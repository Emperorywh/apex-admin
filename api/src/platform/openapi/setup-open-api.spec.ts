import {
  Controller,
  Get,
  type INestApplication,
  VersioningType,
} from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { SwaggerModule } from '@nestjs/swagger';
import helmet from 'helmet';
import request from 'supertest';
import type { App } from 'supertest/types';
import {
  getOpenApiUrl,
  OPEN_API_JSON_PATH,
  OPEN_API_PATH,
  OPEN_API_YAML_PATH,
  setupOpenApi,
} from './setup-open-api';

/*
 * 测试锁定 Swagger 的挂载契约和启动日志地址转换规则。
 * 测试不启动 HTTP 服务，避免把平台配置验证耦合到数据库或浏览器。
 */
describe('OpenAPI 平台配置', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('挂载 UI、JSON 和 YAML，并使用延迟文档工厂', () => {
    const app = {} as INestApplication;
    const setup = jest.spyOn(SwaggerModule, 'setup').mockImplementation(() => undefined);

    setupOpenApi(app);

    expect(setup).toHaveBeenCalledWith(
      OPEN_API_PATH,
      app,
      expect.any(Function),
      expect.objectContaining({
        jsonDocumentUrl: OPEN_API_JSON_PATH,
        yamlDocumentUrl: OPEN_API_YAML_PATH,
        raw: ['json', 'yaml'],
      }),
    );
  });

  it.each([
    ['http://0.0.0.0:3000', 'http://localhost:3000/docs'],
    ['http://[::]:3000', 'http://localhost:3000/docs'],
    ['https://api.example.com', 'https://api.example.com/docs'],
  ])('将监听地址 %s 转换为可访问文档地址', (applicationUrl, expected) => {
    expect(getOpenApiUrl(applicationUrl)).toBe(expected);
  });

  it('HTTP 适配器同时提供 Swagger UI 与 OpenAPI 文档', async () => {
    const module = await Test.createTestingModule({
      controllers: [OpenApiProbeController],
    }).compile();
    const app: INestApplication<App> = module.createNestApplication();
    app.enableVersioning({ type: VersioningType.URI });
    app.use(helmet());
    setupOpenApi(app);
    await app.init();

    try {
      await request(app.getHttpServer())
        .get(`/${OPEN_API_PATH}/`)
        .expect(200)
        .expect('content-type', /text\/html/);
      await request(app.getHttpServer())
        .get(`/${OPEN_API_JSON_PATH}`)
        .expect(200)
        .expect(({ body }) => {
          expect(readPaths(body)).toHaveProperty('/v1/open-api-probe');
        });
    } finally {
      await app.close();
    }
  });
});

/*
 * 探针控制器只用于验证路由元数据能生成 OpenAPI path。
 * 它不引入业务模块，使文档端点测试保持快速且无外部依赖。
 */
@Controller({ path: 'open-api-probe', version: '1' })
class OpenApiProbeController {
  @Get()
  read(): Readonly<{ status: string }> {
    return { status: 'ok' };
  }
}

/*
 * Supertest 的 body 类型为 any，仅在这个边界缩窄为可验证的文档形状。
 * 测试不对实现内部做不安全的属性访问。
 */
function readPaths(body: unknown): Readonly<Record<string, unknown>> {
  if (typeof body !== 'object' || body === null || !('paths' in body)) {
    throw new Error('OpenAPI 文档缺少 paths');
  }

  const paths = body.paths;
  if (typeof paths !== 'object' || paths === null) {
    throw new Error('OpenAPI paths 形状无效');
  }

  return paths as Readonly<Record<string, unknown>>;
}
