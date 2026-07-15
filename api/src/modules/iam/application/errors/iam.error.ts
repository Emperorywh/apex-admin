/*
 * 应用错误目录是 Domain、Infrastructure 与 HTTP 之间的稳定契约。
 * 每个错误只携带公开 code，协议状态与标题集中由 HTTP Filter 映射。
 */
export const IAM_ERROR_CODES = [
  'ACCESS_TOKEN_INVALID',
  'LOGIN_ACCOUNT_NOT_FOUND',
  'INVALID_CREDENTIALS',
  'USER_DISABLED',
  'REFRESH_TOKEN_INVALID',
  'REFRESH_TOKEN_REPLAY',
  'REFRESH_TOKEN_STALE',
  'INSUFFICIENT_PRIVILEGE',
  'USER_NOT_FOUND',
  'USER_EMAIL_ALREADY_USED',
  'PASSWORD_NOT_ALLOWED',
  'LAST_SUPER_ADMIN',
  'CONCURRENT_MODIFICATION',
  'RATE_LIMIT_EXCEEDED',
] as const;

export type IamErrorCode = (typeof IAM_ERROR_CODES)[number];

export class IamError extends Error {
  constructor(
    readonly code: IamErrorCode,
    message: string,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = 'IamError';
  }
}

export const IamErrors = {
  accessTokenInvalid: () => new IamError('ACCESS_TOKEN_INVALID', 'Access Token 无效或已过期'),
  loginAccountNotFound: () => new IamError('LOGIN_ACCOUNT_NOT_FOUND', '登录账号不存在'),
  invalidCredentials: () => new IamError('INVALID_CREDENTIALS', '密码错误'),
  userDisabled: () => new IamError('USER_DISABLED', '用户已禁用'),
  refreshTokenInvalid: () => new IamError('REFRESH_TOKEN_INVALID', 'Refresh Token 无效'),
  refreshTokenReplay: () => new IamError('REFRESH_TOKEN_REPLAY', '检测到 Refresh Token 重放'),
  refreshTokenStale: () => new IamError('REFRESH_TOKEN_STALE', 'Refresh Token 已被并发请求轮换'),
  insufficientPrivilege: () => new IamError('INSUFFICIENT_PRIVILEGE', '权限不足'),
  userNotFound: () => new IamError('USER_NOT_FOUND', '用户不存在'),
  userEmailAlreadyUsed: () => new IamError('USER_EMAIL_ALREADY_USED', '邮箱已被使用'),
  passwordNotAllowed: () => new IamError('PASSWORD_NOT_ALLOWED', '密码不符合安全策略'),
  lastSuperAdmin: () => new IamError('LAST_SUPER_ADMIN', '不能移除最后一个活跃超级管理员'),
  concurrentModification: () => new IamError('CONCURRENT_MODIFICATION', '检测到并发修改冲突'),
  rateLimitExceeded: (retryAfterSeconds: number) =>
    new IamError('RATE_LIMIT_EXCEEDED', '请求过于频繁', retryAfterSeconds),
} as const;
