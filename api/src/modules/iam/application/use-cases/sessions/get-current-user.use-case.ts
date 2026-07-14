import { Injectable } from '@nestjs/common';
import { UserStatus } from '../../../domain/account/user';
import { AuthenticatedActor } from '../../contracts/authenticated-actor';
import { UserReadModel } from '../../contracts/read-models';
import { IamErrors } from '../../errors/iam.error';
import { UserRepository } from '../../ports/persistence.ports';

/*
 * /me 同时报告数据库当前账户与 access token 授权快照。
 * stale 被显式建模，客户端不会误以为两者永远同步。
 */
@Injectable()
export class GetCurrentUserUseCase {
  constructor(private readonly users: UserRepository) {}

  async execute(actor: AuthenticatedActor): Promise<{
    user: UserReadModel;
    authorization: {
      tokenRole: AuthenticatedActor['role'];
      permissions: AuthenticatedActor['permissions'];
      expiresAt: Date;
      stale: boolean;
    };
  }> {
    const user = await this.users.findReadModelById(actor.id);
    if (!user || user.status === UserStatus.DISABLED) throw IamErrors.userDisabled();
    return {
      user,
      authorization: {
        tokenRole: actor.role,
        permissions: actor.permissions,
        expiresAt: actor.expiresAt,
        stale: user.role !== actor.role,
      },
    };
  }
}
