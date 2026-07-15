import { SetMetadata } from '@nestjs/common';

/*
 * Public 只跳过 access 身份认证与路由权限检查。
 * DTO、限流和日志链路仍然照常执行，避免公开路由绕过通用请求处理。
 */
export const PUBLIC_ROUTE_METADATA = Symbol('iam.public-route');

export const Public = () => SetMetadata(PUBLIC_ROUTE_METADATA, true);
