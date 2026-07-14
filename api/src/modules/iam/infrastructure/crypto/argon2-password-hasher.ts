import { Injectable } from '@nestjs/common';
import { argon2id, hash, verify } from 'argon2';
import { PasswordHash } from '../../domain/account/password';
import { IamErrors } from '../../application/errors/iam.error';
import {
  PasswordHasher,
  PasswordHashingConfig,
} from '../../application/ports/security.ports';
import { BoundedSemaphore, SemaphoreQueueFullError } from './bounded-semaphore';

/*
 * Argon2id Adapter 统一承担 hash、verify 与全局并发保护。
 * 编码串保留算法和成本，未来可在登录后显式设计参数升级流程。
 */
@Injectable()
export class Argon2PasswordHasher extends PasswordHasher {
  private readonly semaphore: BoundedSemaphore;

  constructor(private readonly config: PasswordHashingConfig) {
    super();
    this.semaphore = new BoundedSemaphore(
      config.maxConcurrency,
      config.maxConcurrency,
    );
  }

  async hash(password: string): Promise<PasswordHash> {
    try {
      const encoded = await this.semaphore.run(() =>
        hash(password, {
          type: argon2id,
          memoryCost: this.config.memoryKiB,
          timeCost: this.config.timeCost,
          parallelism: this.config.parallelism,
        }),
      );
      return PasswordHash.fromArgon2id(encoded);
    } catch (error) {
      if (error instanceof SemaphoreQueueFullError) throw IamErrors.rateLimitExceeded(1);
      throw error;
    }
  }

  async verify(passwordHash: PasswordHash, password: string): Promise<boolean> {
    try {
      return await this.semaphore.run(() => verify(passwordHash.value, password));
    } catch (error) {
      if (error instanceof SemaphoreQueueFullError) throw IamErrors.rateLimitExceeded(1);
      throw error;
    }
  }
}
