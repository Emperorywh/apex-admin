import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';

/*
 * Problem Details DTO 是所有 HTTP 模块共享的错误传输契约。
 * 它只描述协议字段，不引用任何业务模块的错误类型或状态映射。
 */
export class ProblemFieldErrorDto {
  @ApiProperty({ example: 'email' })
  readonly path!: string;

  @ApiProperty({ example: 'isEmail' })
  readonly code!: string;
}

export class ProblemDetailsDto {
  @ApiProperty({
    format: 'uri',
    example: 'https://apex.example.com/problems/validation-failed',
  })
  readonly type!: string;

  @ApiProperty({ example: '请求字段验证失败' })
  readonly title!: string;

  @ApiProperty({ example: 400 })
  readonly status!: number;

  @ApiProperty({ example: 'VALIDATION_FAILED' })
  readonly code!: string;

  @ApiProperty({ example: '019f6061-0c80-7c61-b475-d70ac0829dff' })
  readonly traceId!: string;

  @ApiPropertyOptional({ type: () => [ProblemFieldErrorDto] })
  readonly errors?: readonly ProblemFieldErrorDto[];
}
