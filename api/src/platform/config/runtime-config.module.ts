import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { loadRuntimeConfig, RuntimeConfig } from './runtime-config';

/*
 * ConfigModule 负责唯一一次环境读取与启动失败关闭。
 * 其余模块通过 RuntimeConfig 类令牌获取强类型只读配置。
 */
const RUNTIME_CONFIG_KEY = 'APEX_RUNTIME_CONFIG';

@Module({
  imports: [
    ConfigModule.forRoot({
      cache: true,
      isGlobal: false,
      validate: (environment) => ({
        ...environment,
        [RUNTIME_CONFIG_KEY]: loadRuntimeConfig(environment),
      }),
    }),
  ],
  providers: [
    {
      provide: RuntimeConfig,
      inject: [ConfigService],
      useFactory: (config: ConfigService): RuntimeConfig =>
        config.getOrThrow<RuntimeConfig>(RUNTIME_CONFIG_KEY),
    },
  ],
  exports: [RuntimeConfig],
})
export class RuntimeConfigModule {}
