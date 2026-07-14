import { Module } from '@nestjs/common';
import { RuntimeConfigModule } from '../config/runtime-config.module';
import { DatabaseClient } from './database-client';
import { DatabaseTransactionConfig } from './database-transaction-config';
import { DatabaseReadinessProbe } from './database-readiness.probe';

/*
 * 数据库模块只管理基础设施生命周期与技术配置。
 * 它不是全局模块，使用方必须显式声明依赖。
 */
@Module({
  imports: [RuntimeConfigModule],
  providers: [DatabaseClient, DatabaseTransactionConfig, DatabaseReadinessProbe],
  exports: [DatabaseClient, DatabaseTransactionConfig, DatabaseReadinessProbe],
})
export class DatabaseModule {}
