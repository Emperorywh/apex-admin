import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { AppModule } from '../app.module';
import { RuntimeConfig } from '../platform/config/runtime-config';
import { setupApplication } from './setup-application';
import { redactSensitiveText } from '../platform/logging/redact-sensitive-text';

/*
 * bootstrap 负责创建应用、安装基础能力并按强类型配置监听。
 * 配置或依赖初始化失败时不吞异常，进程以非零状态退出。
 */
export async function bootstrap(): Promise<void> {
  try {
    const app = await NestFactory.create(AppModule, { bufferLogs: true });
    const config = app.get(RuntimeConfig);
    setupApplication(app, config);
    await app.listen(config.http.port, config.http.host);
  } catch (error) {
    const logger = new Logger('Bootstrap');
    logger.error(
      '应用启动失败',
      redactSensitiveText(error instanceof Error ? error.stack : undefined),
    );
    process.exitCode = 1;
  }
}
