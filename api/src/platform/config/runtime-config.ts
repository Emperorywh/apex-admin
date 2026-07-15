import { z } from 'zod';
import { ACCESS_TOKEN_TTL_POLICY } from '../../modules/iam/public-api';

/*
 * Runtime 配置在进程启动时一次性解析为只读对象。
 * 业务模块只依赖这个强类型结果，不接触环境变量字符串。
 */
const boundedInteger = (minimum: number, maximum: number, defaultValue: number) =>
  z.coerce.number().int().min(minimum).max(maximum).default(defaultValue);

const booleanFromEnvironment = z
  .enum(['true', 'false'])
  .transform((value) => value === 'true');

const postgresUrl = z
  .string()
  .url()
  .refine(
    (value) => value.startsWith('postgresql://') || value.startsWith('postgres://'),
    '必须是 PostgreSQL 连接地址',
  );

const strictBase64Secret = z.string().superRefine((value, context) => {
  const base64Pattern = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
  if (!base64Pattern.test(value)) {
    context.addIssue({ code: 'custom', message: '必须是严格 base64 编码' });
    return;
  }

  if (Buffer.from(value, 'base64').byteLength < 32) {
    context.addIssue({ code: 'custom', message: '解码后至少需要 32 字节' });
  }
});

const corsOrigins = z.string().transform((value, context) => {
  const origins = value
    .split(',')
    .map((origin) => origin.trim())
    .filter((origin) => origin.length > 0);

  if (origins.length === 0 || origins.includes('*')) {
    context.addIssue({ code: 'custom', message: '必须提供非通配符 Origin 白名单' });
    return z.NEVER;
  }

  for (const origin of origins) {
    try {
      const url = new URL(origin);
      if (url.origin !== origin || !['http:', 'https:'].includes(url.protocol)) {
        throw new Error('Origin 不能包含路径');
      }
    } catch {
      context.addIssue({ code: 'custom', message: `非法 Origin：${origin}` });
      return z.NEVER;
    }
  }

  return Object.freeze(origins);
});

const runtimeEnvironmentSchema = z
  .object({
    NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
    HTTP_HOST: z.string().min(1).default('0.0.0.0'),
    PORT: boundedInteger(1, 65535, 3000),
    DATABASE_URL: postgresUrl,
    JWT_ACCESS_SECRET_BASE64: strictBase64Secret,
    JWT_ISSUER: z.string().min(1).default('apex-admin'),
    JWT_ACCESS_AUDIENCE: z.string().min(1).default('apex-admin-web'),
    JWT_ACCESS_TTL_SECONDS: boundedInteger(
      ACCESS_TOKEN_TTL_POLICY.minimumSeconds,
      ACCESS_TOKEN_TTL_POLICY.maximumSeconds,
      ACCESS_TOKEN_TTL_POLICY.defaultSeconds,
    ),
    REFRESH_SESSION_TTL_SECONDS: boundedInteger(86400, 604800, 604800),
    REFRESH_REUSE_GRACE_SECONDS: boundedInteger(1, 30, 5),
    ARGON2_MEMORY_KIB: boundedInteger(19456, 1048576, 65536),
    ARGON2_TIME_COST: boundedInteger(2, 20, 3),
    ARGON2_PARALLELISM: boundedInteger(1, 16, 1),
    ARGON2_MAX_CONCURRENCY: boundedInteger(1, 64, 4),
    RATE_LIMIT_LOGIN_PER_IP: boundedInteger(1, 10000, 10),
    RATE_LIMIT_LOGIN_PER_EMAIL: boundedInteger(1, 10000, 5),
    RATE_LIMIT_WINDOW_SECONDS: boundedInteger(1, 3600, 60),
    CORS_ORIGINS: corsOrigins,
    TRUST_PROXY_HOPS: boundedInteger(0, 16, 0),
    COOKIE_SECURE: booleanFromEnvironment.default(false),
    DATABASE_POOL_MAX: boundedInteger(1, 100, 10),
    DATABASE_POOL_CONNECT_TIMEOUT_MS: boundedInteger(100, 60000, 5000),
    DATABASE_POOL_IDLE_TIMEOUT_MS: boundedInteger(1000, 600000, 30000),
    DATABASE_TRANSACTION_MAX_WAIT_MS: boundedInteger(100, 60000, 2000),
    DATABASE_TRANSACTION_TIMEOUT_MS: boundedInteger(100, 120000, 5000),
    DATABASE_STATEMENT_TIMEOUT_MS: boundedInteger(100, 120000, 4000),
    DATABASE_LOCK_TIMEOUT_MS: boundedInteger(100, 60000, 1500),
    DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS: boundedInteger(100, 120000, 5000),
  })
  .superRefine((value, context) => {
    if (value.NODE_ENV === 'production' && !value.COOKIE_SECURE) {
      context.addIssue({
        code: 'custom',
        path: ['COOKIE_SECURE'],
        message: '生产环境必须启用 Secure Cookie',
      });
    }
  });

type RuntimeEnvironment = z.infer<typeof runtimeEnvironmentSchema>;

export class RuntimeConfig {
  readonly environment: RuntimeEnvironment['NODE_ENV'];
  readonly http: Readonly<{
    host: string;
    port: number;
    trustProxyHops: number;
    corsOrigins: readonly string[];
  }>;
  readonly database: Readonly<{
    url: string;
    poolMax: number;
    connectTimeoutMs: number;
    idleTimeoutMs: number;
    transactionMaxWaitMs: number;
    transactionTimeoutMs: number;
    statementTimeoutMs: number;
    lockTimeoutMs: number;
    idleInTransactionTimeoutMs: number;
  }>;
  readonly jwt: Readonly<{
    secret: Buffer;
    issuer: string;
    audience: string;
    accessTtlSeconds: number;
  }>;
  readonly session: Readonly<{
    ttlSeconds: number;
    refreshReuseGraceSeconds: number;
  }>;
  readonly password: Readonly<{
    memoryKiB: number;
    timeCost: number;
    parallelism: number;
    maxConcurrency: number;
  }>;
  readonly rateLimit: Readonly<{
    loginPerIp: number;
    loginPerEmail: number;
    windowSeconds: number;
  }>;
  readonly cookie: Readonly<{ secure: boolean }>;

  constructor(environment: RuntimeEnvironment) {
    this.environment = environment.NODE_ENV;
    this.http = Object.freeze({
      host: environment.HTTP_HOST,
      port: environment.PORT,
      trustProxyHops: environment.TRUST_PROXY_HOPS,
      corsOrigins: environment.CORS_ORIGINS,
    });
    this.database = Object.freeze({
      url: environment.DATABASE_URL,
      poolMax: environment.DATABASE_POOL_MAX,
      connectTimeoutMs: environment.DATABASE_POOL_CONNECT_TIMEOUT_MS,
      idleTimeoutMs: environment.DATABASE_POOL_IDLE_TIMEOUT_MS,
      transactionMaxWaitMs: environment.DATABASE_TRANSACTION_MAX_WAIT_MS,
      transactionTimeoutMs: environment.DATABASE_TRANSACTION_TIMEOUT_MS,
      statementTimeoutMs: environment.DATABASE_STATEMENT_TIMEOUT_MS,
      lockTimeoutMs: environment.DATABASE_LOCK_TIMEOUT_MS,
      idleInTransactionTimeoutMs: environment.DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS,
    });
    this.jwt = Object.freeze({
      secret: Buffer.from(environment.JWT_ACCESS_SECRET_BASE64, 'base64'),
      issuer: environment.JWT_ISSUER,
      audience: environment.JWT_ACCESS_AUDIENCE,
      accessTtlSeconds: environment.JWT_ACCESS_TTL_SECONDS,
    });
    this.session = Object.freeze({
      ttlSeconds: environment.REFRESH_SESSION_TTL_SECONDS,
      refreshReuseGraceSeconds: environment.REFRESH_REUSE_GRACE_SECONDS,
    });
    this.password = Object.freeze({
      memoryKiB: environment.ARGON2_MEMORY_KIB,
      timeCost: environment.ARGON2_TIME_COST,
      parallelism: environment.ARGON2_PARALLELISM,
      maxConcurrency: environment.ARGON2_MAX_CONCURRENCY,
    });
    this.rateLimit = Object.freeze({
      loginPerIp: environment.RATE_LIMIT_LOGIN_PER_IP,
      loginPerEmail: environment.RATE_LIMIT_LOGIN_PER_EMAIL,
      windowSeconds: environment.RATE_LIMIT_WINDOW_SECONDS,
    });
    this.cookie = Object.freeze({ secure: environment.COOKIE_SECURE });
  }
}

export function loadRuntimeConfig(environment: NodeJS.ProcessEnv): RuntimeConfig {
  return new RuntimeConfig(runtimeEnvironmentSchema.parse(environment));
}
