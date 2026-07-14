import { Injectable } from '@nestjs/common';
import { RuntimeConfig } from '../../../../platform/config/runtime-config';
import { IamSessionConfig } from '../../application/ports/security.ports';

/*
 * 窄配置 Adapter 只向会话用例暴露两个已验证阈值。
 * Application 不会感知 HTTP、数据库或 Secret 配置结构。
 */
@Injectable()
export class RuntimeIamSessionConfig extends IamSessionConfig {
  readonly sessionTtlSeconds: number;
  readonly refreshReuseGraceSeconds: number;

  constructor(config: RuntimeConfig) {
    super();
    this.sessionTtlSeconds = config.session.ttlSeconds;
    this.refreshReuseGraceSeconds = config.session.refreshReuseGraceSeconds;
  }
}
