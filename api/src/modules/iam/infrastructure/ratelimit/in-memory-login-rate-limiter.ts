import { createHash } from 'node:crypto';
import { Injectable } from '@nestjs/common';
import { RuntimeConfig } from '../../../../platform/config/runtime-config';
import { IamErrors } from '../../application/errors/iam.error';
import { LoginRateLimiter } from '../../application/ports/security.ports';

/*
 * 单副本内存限流器使用固定窗口、TTL 清理与最大 key 数控制内存。
 * 邮箱 key 只保存带命名空间的摘要，不暴露 canonical email。
 */
interface RateEntry {
  count: number;
  readonly resetAt: number;
}

const MAX_TRACKED_KEYS = 20_000;
const EMAIL_KEY_NAMESPACE = 'apex-admin:login-email:v1\0';

@Injectable()
export class InMemoryLoginRateLimiter extends LoginRateLimiter {
  private readonly ipEntries = new Map<string, RateEntry>();
  private readonly emailEntries = new Map<string, RateEntry>();

  constructor(private readonly config: RuntimeConfig) {
    super();
  }

  consume(ipAddress: string, canonicalEmail: string, now: Date): void {
    const nowMs = now.getTime();
    this.cleanupExpired(nowMs);
    const emailKey = createHash('sha256')
      .update(EMAIL_KEY_NAMESPACE)
      .update(canonicalEmail)
      .digest('hex');
    const ipRetry = this.increment(
      this.ipEntries,
      `ip:${ipAddress}`,
      this.config.rateLimit.loginPerIp,
      nowMs,
    );
    const emailRetry = this.increment(
      this.emailEntries,
      `email:${emailKey}`,
      this.config.rateLimit.loginPerEmail,
      nowMs,
    );
    const retryAfter = Math.max(ipRetry, emailRetry);
    if (retryAfter > 0) throw IamErrors.rateLimitExceeded(retryAfter);
  }

  private increment(
    entries: Map<string, RateEntry>,
    key: string,
    limit: number,
    nowMs: number,
  ): number {
    const current = entries.get(key);
    if (!current || current.resetAt <= nowMs) {
      if (entries.size >= MAX_TRACKED_KEYS) {
        return this.config.rateLimit.windowSeconds;
      }
      entries.set(key, {
        count: 1,
        resetAt: nowMs + this.config.rateLimit.windowSeconds * 1000,
      });
      return 0;
    }
    current.count += 1;
    return current.count > limit
      ? Math.max(1, Math.ceil((current.resetAt - nowMs) / 1000))
      : 0;
  }

  private cleanupExpired(nowMs: number): void {
    for (const entries of [this.ipEntries, this.emailEntries]) {
      for (const [key, value] of entries) {
        if (value.resetAt <= nowMs) entries.delete(key);
      }
    }
  }
}
