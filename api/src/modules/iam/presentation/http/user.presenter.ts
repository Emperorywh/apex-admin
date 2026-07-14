import { UserReadModel } from '../../application/contracts/read-models';

/*
 * Presenter 显式选择可公开用户字段并统一日期序列化。
 * passwordHash、内部记录形状与 Prisma 字段不会被偶然透传。
 */
export function presentUser(user: UserReadModel) {
  return {
    id: user.id,
    email: user.email,
    role: user.role,
    status: user.status,
    createdAt: user.createdAt.toISOString(),
    updatedAt: user.updatedAt.toISOString(),
  };
}

export function presentAuthUser(user: UserReadModel) {
  return {
    id: user.id,
    email: user.email,
    role: user.role,
    status: user.status,
  };
}
