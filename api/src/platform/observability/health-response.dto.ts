import { ApiProperty } from '@nestjs/swagger';

/*
 * 健康检查响应使用独立 DTO，避免以匿名对象丢失运行时反射信息。
 * 状态值保持封闭集合，使监控客户端能够从 OpenAPI 推导合法结果。
 */
export class HealthStatusDto {
  @ApiProperty({ enum: ['ok', 'ready'] })
  readonly status!: 'ok' | 'ready';
}

export class HealthResponseDto {
  @ApiProperty({ type: () => HealthStatusDto })
  readonly data!: HealthStatusDto;
}
