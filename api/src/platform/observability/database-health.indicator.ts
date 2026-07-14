import { Injectable } from '@nestjs/common';
import { DatabaseReadinessProbe } from '../database/database-readiness.probe';

/*
 * Readiness 数据库检查封装在平台基础设施中。
 * Controller 不注入 Prisma，也不把数据库异常细节返回客户端。
 */
@Injectable()
export class DatabaseHealthIndicator {
  constructor(private readonly database: DatabaseReadinessProbe) {}

  async isReady(): Promise<boolean> {
    return this.database.check();
  }
}
