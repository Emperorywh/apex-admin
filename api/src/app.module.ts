import { MiddlewareConsumer, Module, NestModule } from '@nestjs/common';
import { RuntimeConfigModule } from './platform/config/runtime-config.module';
import { RequestIdMiddleware } from './platform/http/request-id.middleware';
import { ObservabilityModule } from './platform/observability/observability.module';
import { IamModule } from './modules/iam/iam.module';
import { APP_INTERCEPTOR } from '@nestjs/core';
import { HttpRequestLoggingInterceptor } from './platform/logging/http-request-logging.interceptor';

/*
 * AppModule 是 Runtime Composition Root，只组装业务模块与平台能力。
 * 业务规则、环境读取和数据库访问都不会放入此处。
 */
@Module({
  imports: [RuntimeConfigModule, IamModule, ObservabilityModule],
  providers: [
    { provide: APP_INTERCEPTOR, useClass: HttpRequestLoggingInterceptor },
  ],
})
export class AppModule implements NestModule {
  configure(consumer: MiddlewareConsumer): void {
    consumer.apply(RequestIdMiddleware).forRoutes('*');
  }
}
