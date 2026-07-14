import { UserRole, UserStatus } from '../../domain/account/user';
import { User } from '../../domain/account/user';

/*
 * 应用读模型只暴露 Presentation 真正需要的字段。
 * Prisma 记录与密码哈希不会越过 Infrastructure 边界。
 */
export interface UserReadModel {
  readonly id: string;
  readonly email: string;
  readonly role: UserRole;
  readonly status: UserStatus;
  readonly createdAt: Date;
  readonly updatedAt: Date;
}

export interface UserAuthorizationSnapshot {
  readonly id: string;
  readonly role: UserRole;
  readonly status: UserStatus;
}

export interface UserPageCursor {
  readonly createdAt: Date;
  readonly id: string;
}

export interface UserPage {
  readonly items: readonly UserReadModel[];
  readonly nextCursor: UserPageCursor | null;
  readonly hasMore: boolean;
}

/*
 * 聚合到应用读模型的转换位于 Application，避免 Controller 读取领域内部状态。
 * Infrastructure 的 Prisma Mapper 与该协议投影保持职责分离。
 */
export function toUserReadModel(user: User): UserReadModel {
  const state = user.snapshot;
  return {
    id: state.id.value,
    email: state.email.value,
    role: state.role,
    status: state.status,
    createdAt: state.createdAt,
    updatedAt: state.updatedAt,
  };
}
