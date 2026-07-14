import { Module } from '@nestjs/common';
import { DatabaseModule } from '../database/database.module';
import { DatabaseHealthIndicator } from './database-health.indicator';
import { HealthController } from './health.controller';

/*
 * Observability Module 聚合健康检查入口与依赖探针。
 * 它不承载 IAM 业务查询或运行期可变状态。
 */
@Module({
  imports: [DatabaseModule],
  controllers: [HealthController],
  providers: [DatabaseHealthIndicator],
})
export class ObservabilityModule {}
