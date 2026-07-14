import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { AppModule } from '../app.module';
import { RuntimeConfig } from '../platform/config/runtime-config';
import { setupApplication } from './setup-application';
import { redactSensitiveText } from '../platform/logging/redact-sensitive-text';
import { getOpenApiUrl, setupOpenApi } from '../platform/openapi/setup-open-api';

/*
 * bootstrap 负责创建应用、组装平台能力并按强类型配置监听。
 * Swagger 地址只在端口监听成功后输出；任何启动失败都使进程非零退出。
 */
export async function bootstrap(): Promise<void> {
  const logger = new Logger('Bootstrap');

  try {
    const app = await NestFactory.create(AppModule, { bufferLogs: true });
    const config = app.get(RuntimeConfig);
    setupApplication(app, config);
    setupOpenApi(app);
    await app.listen(config.http.port, config.http.host);
    logger.log(`Swagger 文档地址：${getOpenApiUrl(await app.getUrl())}`);
  } catch (error) {
    logger.error(
      '应用启动失败',
      redactSensitiveText(error instanceof Error ? error.stack : undefined),
    );
    process.exitCode = 1;
  }
}
