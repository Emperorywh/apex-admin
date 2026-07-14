import {
  Controller,
  Get,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { Public } from '../../modules/iam/public-api';
import { DatabaseHealthIndicator } from './database-health.indicator';

/*
 * Liveness 只报告进程可响应，Readiness 额外验证数据库依赖。
 * 两个端点显式公开，但仍经过请求 ID、CORS 与全局错误处理；OpenAPI 中不声明认证。
 */
@ApiTags('健康检查')
@Controller({ path: 'health', version: '1' })
export class HealthController {
  constructor(private readonly databaseHealth: DatabaseHealthIndicator) {}

  @Public()
  @Get('live')
  live() {
    return { data: { status: 'ok' } };
  }

  @Public()
  @Get('ready')
  async ready() {
    if (!(await this.databaseHealth.isReady())) {
      throw new ServiceUnavailableException('服务依赖尚未就绪');
    }
    return { data: { status: 'ready' } };
  }
}
