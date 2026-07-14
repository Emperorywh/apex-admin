/*
 * 集中脱敏处理异常文本中的连接凭证、认证头、Cookie 与密码字段。
 * 日志仍保留错误类型和非敏感定位信息，不记录原始请求体。
 */
const REDACTION_RULES: readonly [RegExp, string][] = [
  [/postgres(?:ql)?:\/\/[^\s@]+@/gi, 'postgresql://[REDACTED]@'],
  [/Bearer\s+[A-Za-z0-9._~-]+/gi, 'Bearer [REDACTED]'],
  [/refresh_token=[^;\s]+/gi, 'refresh_token=[REDACTED]'],
  [/(password|secret)(["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi, '$1$2[REDACTED]'],
];

export function redactSensitiveText(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  return REDACTION_RULES.reduce(
    (redacted, [pattern, replacement]) => redacted.replace(pattern, replacement),
    value,
  );
}
