import { TransformFnParams } from 'class-transformer';

/*
 * HTTP 邮箱字段统一只做 trim，大小写规范化仍由 Email 值对象负责。
 * 密码 DTO 不复用该转换，确保密码首尾空格保持原样。
 */
export function trimEmailInput(parameters: TransformFnParams): unknown {
  const value = parameters.value as unknown;
  return typeof value === 'string' ? value.trim() : value;
}
