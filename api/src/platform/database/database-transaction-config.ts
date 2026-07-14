import { Injectable } from '@nestjs/common';
import { RuntimeConfig } from '../config/runtime-config';

/*
 * 事务等待、执行和 PostgreSQL 锁超时是不同控制面。
 * 该对象集中派生受校验的数值，避免各仓储自行决定魔法阈值。
 */
@Injectable()
export class DatabaseTransactionConfig {
  readonly maxWaitMs: number;
  readonly timeoutMs: number;
  readonly statementTimeoutMs: number;
  readonly lockTimeoutMs: number;

  constructor(config: RuntimeConfig) {
    this.maxWaitMs = config.database.transactionMaxWaitMs;
    this.timeoutMs = config.database.transactionTimeoutMs;
    this.statementTimeoutMs = config.database.statementTimeoutMs;
    this.lockTimeoutMs = config.database.lockTimeoutMs;
  }
}
