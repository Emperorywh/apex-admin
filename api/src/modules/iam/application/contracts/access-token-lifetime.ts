/*
 * Access Token 有效期策略属于 IAM 应用层稳定契约。
 * 运行时配置与 HTTP 契约共同依赖该策略，避免各层重复维护时间边界。
 */
export const ACCESS_TOKEN_TTL_POLICY = Object.freeze({
  minimumSeconds: 5 * 60,
  maximumSeconds: 24 * 60 * 60,
  defaultSeconds: 24 * 60 * 60,
});
