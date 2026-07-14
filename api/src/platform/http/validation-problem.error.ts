/*
 * ValidationPipe 将字段错误归一为稳定路径和约束码。
 * Problem Details Filter 只对该错误附加 errors 数组。
 */
export interface ValidationFieldError {
  readonly path: string;
  readonly code: string;
}

export class ValidationProblemError extends Error {
  constructor(readonly errors: readonly ValidationFieldError[]) {
    super('请求字段验证失败');
    this.name = 'ValidationProblemError';
  }
}
