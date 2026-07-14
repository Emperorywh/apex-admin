import { Injectable } from '@nestjs/common';
import { UserReadModel } from '../../contracts/read-models';
import { IamErrors } from '../../errors/iam.error';
import { UserRepository } from '../../ports/persistence.ports';

/*
 * 用户详情查询使用专用读模型，不还原无关 Session 聚合。
 * 管理端不存在语义固定映射为 USER_NOT_FOUND。
 */
@Injectable()
export class GetUserUseCase {
  constructor(private readonly users: UserRepository) {}

  async execute(userId: string): Promise<UserReadModel> {
    const user = await this.users.findReadModelById(userId);
    if (!user) throw IamErrors.userNotFound();
    return user;
  }
}
