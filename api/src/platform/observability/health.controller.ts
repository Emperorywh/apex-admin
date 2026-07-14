import {
  Controller,
  Get,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ApiOkResponse, ApiTags } from '@nestjs/swagger';
import { Public } from '../../modules/iam/public-api';
import { ApiProblemResponses } from '../openapi/api-problem-responses.decorator';
import { DatabaseHealthIndicator } from './database-health.indicator';
import { HealthResponseDto } from './health-response.dto';

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
  @ApiOkResponse({ description: '服务进程存活', type: HealthResponseDto })
  @ApiProblemResponses()
  live(): HealthResponseDto {
    return { data: { status: 'ok' } };
  }

  @Public()
  @Get('ready')
  @ApiOkResponse({ description: '服务及其依赖已经就绪', type: HealthResponseDto })
  @ApiProblemResponses(503)
  async ready(): Promise<HealthResponseDto> {
    if (!(await this.databaseHealth.isReady())) {
      throw new ServiceUnavailableException('服务依赖尚未就绪');
    }
    return { data: { status: 'ready' } };
  }
}
