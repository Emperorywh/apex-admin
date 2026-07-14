import { SetMetadata } from '@nestjs/common';

/*
 * 可信 Origin 边界通过显式路由元数据声明，不依赖 URL 字符串匹配。
 * Public 不会覆盖该元数据，因此认证写端点仍保持 CSRF 防护。
 */
export const TRUSTED_ORIGIN_METADATA = Symbol('iam.trusted-origin');

export const RequireTrustedOrigin = () => SetMetadata(TRUSTED_ORIGIN_METADATA, true);
