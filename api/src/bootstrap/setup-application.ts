import {
  INestApplication,
  ValidationError,
  ValidationPipe,
  VersioningType,
} from '@nestjs/common';
import cookieParser from 'cookie-parser';
import helmet from 'helmet';
import { RuntimeConfig } from '../platform/config/runtime-config';
import { ValidationProblemError } from '../platform/http/validation-problem.error';

/*
 * 跨模块 HTTP 能力只在唯一启动路径安装，确保请求经过同一安全基线。
 * 这里不包含角色、Session 或其他业务条件。
 */
export function setupApplication(app: INestApplication, config: RuntimeConfig): void {
  app.enableShutdownHooks();
  app.enableVersioning({ type: VersioningType.URI });
  app.use(helmet());
  app.use(cookieParser());
  app.enableCors({
    credentials: true,
    origin: (
      origin: string | undefined,
      callback: (error: Error | null, allow?: boolean) => void,
    ) => {
      if (!origin || config.http.corsOrigins.includes(origin)) {
        callback(null, true);
        return;
      }
      callback(null, false);
    },
  });
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      transformOptions: { enableImplicitConversion: false },
      exceptionFactory: (errors) =>
        new ValidationProblemError(flattenValidationErrors(errors)),
    }),
  );

  const express = app.getHttpAdapter().getInstance() as {
    set(setting: string, value: number): void;
  };
  express.set('trust proxy', config.http.trustProxyHops);
}

/*
 * 嵌套 DTO 错误被展平为稳定字段路径与 class-validator 约束码。
 * 原始值与请求体不会进入错误响应或日志。
 */
function flattenValidationErrors(
  errors: readonly ValidationError[],
  parentPath = '',
): readonly { path: string; code: string }[] {
  return errors.flatMap((error) => {
    const path = parentPath ? `${parentPath}.${error.property}` : error.property;
    const ownErrors = Object.keys(error.constraints ?? {}).map((code) => ({ path, code }));
    return [...ownErrors, ...flattenValidationErrors(error.children ?? [], path)];
  });
}
