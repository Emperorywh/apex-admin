import { Injectable } from '@nestjs/common';
import { RuntimeConfig } from '../../../../platform/config/runtime-config';
import { PasswordHashingConfig } from '../../application/ports/security.ports';

/*
 * Runtime 密码哈希 Adapter 只暴露 Argon2 和并发阈值。
 * Seed 进程用自身 schema 构建同一窄契约，不需要 Runtime HTTP 配置。
 */
@Injectable()
export class RuntimePasswordHashingConfig extends PasswordHashingConfig {
  readonly memoryKiB: number;
  readonly timeCost: number;
  readonly parallelism: number;
  readonly maxConcurrency: number;

  constructor(config: RuntimeConfig) {
    super();
    this.memoryKiB = config.password.memoryKiB;
    this.timeCost = config.password.timeCost;
    this.parallelism = config.password.parallelism;
    this.maxConcurrency = config.password.maxConcurrency;
  }
}
