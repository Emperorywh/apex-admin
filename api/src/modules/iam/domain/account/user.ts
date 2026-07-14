import { DomainInvariantError } from '../errors/domain-invariant.error';
import { Email } from './email';
import { UserId } from './identifiers';
import { PasswordHash } from './password';

/*
 * User 聚合只管理单账户状态迁移。
 * 跨记录的最后超管与 Session 批量吊销由 Application 事务编排。
 */
export enum UserRole {
  SUPER_ADMIN = 'SUPER_ADMIN',
  ADMIN = 'ADMIN',
  OPERATOR = 'OPERATOR',
  VIEWER = 'VIEWER',
}

export enum UserStatus {
  ACTIVE = 'ACTIVE',
  DISABLED = 'DISABLED',
}

export interface UserState {
  readonly id: UserId;
  readonly email: Email;
  readonly passwordHash: PasswordHash;
  readonly role: UserRole;
  readonly status: UserStatus;
  readonly createdAt: Date;
  readonly updatedAt: Date;
}

export class User {
  private constructor(private state: UserState) {}

  static create(input: {
    id: UserId;
    email: Email;
    passwordHash: PasswordHash;
    role: UserRole;
    now: Date;
  }): User {
    return new User({
      ...input,
      status: UserStatus.ACTIVE,
      createdAt: input.now,
      updatedAt: input.now,
    });
  }

  static restore(state: UserState): User {
    return new User(state);
  }

  get snapshot(): UserState {
    return { ...this.state };
  }

  changeRole(nextRole: UserRole, now: Date): boolean {
    if (this.state.role === nextRole) return false;
    this.state = { ...this.state, role: nextRole, updatedAt: now };
    return true;
  }

  disable(now: Date): boolean {
    if (this.state.status === UserStatus.DISABLED) return false;
    this.state = { ...this.state, status: UserStatus.DISABLED, updatedAt: now };
    return true;
  }

  enable(now: Date): boolean {
    if (this.state.status === UserStatus.ACTIVE) return false;
    this.state = { ...this.state, status: UserStatus.ACTIVE, updatedAt: now };
    return true;
  }

  assertActive(): void {
    if (this.state.status !== UserStatus.ACTIVE) {
      throw new DomainInvariantError('USER_NOT_ACTIVE', '用户当前不是启用状态');
    }
  }
}
