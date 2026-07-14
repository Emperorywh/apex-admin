import { Injectable } from '@nestjs/common';
import { DatabaseClient } from './database-client';

/*
 * 数据库就绪探针封装最小只读查询，并吞掉具体驱动异常。
 * Observability 只依赖该稳定平台契约，不直接注入 Prisma Client。
 */
@Injectable()
export class DatabaseReadinessProbe {
  constructor(private readonly database: DatabaseClient) {}

  async check(): Promise<boolean> {
    try {
      await this.database.$queryRaw`SELECT 1`;
      return true;
    } catch {
      return false;
    }
  }
}
