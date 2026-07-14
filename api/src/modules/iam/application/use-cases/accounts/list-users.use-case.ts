import { Injectable } from '@nestjs/common';
import { UserPage, UserPageCursor } from '../../contracts/read-models';
import { UserRepository } from '../../ports/persistence.ports';

/*
 * 列表用例只接收已经严格解码的 keyset cursor。
 * pageSize+1 与排序稳定性由 Infrastructure Read Repository 实现。
 */
@Injectable()
export class ListUsersUseCase {
  constructor(private readonly users: UserRepository) {}

  execute(input: { pageSize: number; cursor: UserPageCursor | null }): Promise<UserPage> {
    return this.users.list(input);
  }
}
