import { Injectable, OnApplicationShutdown, OnModuleInit } from '@nestjs/common';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '@prisma/client';
import { RuntimeConfig } from '../config/runtime-config';

/*
 * DatabaseClient 是 Runtime 进程唯一的 Prisma Client 与连接池入口。
 * 连接参数来自已验证配置，业务用例不能直接实例化或注入它。
 */
@Injectable()
export class DatabaseClient
  extends PrismaClient
  implements OnModuleInit, OnApplicationShutdown
{
  constructor(config: RuntimeConfig) {
    const adapter = new PrismaPg({
      connectionString: config.database.url,
      max: config.database.poolMax,
      connectionTimeoutMillis: config.database.connectTimeoutMs,
      idleTimeoutMillis: config.database.idleTimeoutMs,
      statement_timeout: config.database.statementTimeoutMs,
      idle_in_transaction_session_timeout:
        config.database.idleInTransactionTimeoutMs,
    });
    super({ adapter });
  }

  async onModuleInit(): Promise<void> {
    await this.$connect();
  }

  async onApplicationShutdown(): Promise<void> {
    await this.$disconnect();
  }
}
