/*
 * IAM 公共 API 只导出跨模块稳定契约与 HTTP 元数据装饰器。
 * 用例、仓储和 Infrastructure Adapter 都保持模块私有。
 */
export type { AuthenticatedActor } from './application/contracts/authenticated-actor';
export { ACCESS_TOKEN_TTL_POLICY } from './application/contracts/access-token-lifetime';
export { PermissionCode } from './domain/authorization/authorization';
export { CurrentActor } from './presentation/http/decorators/current-actor.decorator';
export { Public } from './presentation/http/decorators/public.decorator';
export { RequirePermissions } from './presentation/http/decorators/require-permissions.decorator';
