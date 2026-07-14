import { Prisma, type User as PrismaUserRecord } from '@prisma/client';
import { Email } from '../../../domain/account/email';
import { User } from '../../../domain/account/user';
import { UserRole, UserStatus } from '../../../domain/account/user';
import {
  UserPage,
  UserPageCursor,
  UserReadModel,
} from '../../../application/contracts/read-models';
import { UserRepository } from '../../../application/ports/persistence.ports';
import { PrismaIamMapper } from './prisma-iam.mapper';
import { mapUserPersistenceError } from './prisma-persistence-errors';

/*
 * User 仓储同时支持进程级 Client 与绑定事务的 TransactionClient。
 * 所有写入与锁定都由调用方提供的同一 executor 执行。
 */
type UserExecutor = Pick<
  Prisma.TransactionClient,
  'user' | '$queryRaw'
>;

export class PrismaUserRepository extends UserRepository {
  constructor(private readonly database: UserExecutor) {
    super();
  }

  async findByEmail(email: Email): Promise<User | null> {
    const record = await this.database.user.findUnique({ where: { email: email.value } });
    return record ? PrismaIamMapper.userToDomain(record) : null;
  }

  async findCredentialByEmail(email: Email): Promise<{ user: User } | null> {
    const user = await this.findByEmail(email);
    return user ? { user } : null;
  }

  async findById(id: string): Promise<User | null> {
    const record = await this.database.user.findUnique({ where: { id } });
    return record ? PrismaIamMapper.userToDomain(record) : null;
  }

  async findReadModelById(id: string): Promise<UserReadModel | null> {
    const record = await this.database.user.findUnique({
      where: { id },
      select: {
        id: true,
        email: true,
        role: true,
        status: true,
        createdAt: true,
        updatedAt: true,
      },
    });
    return record ? this.toReadModel(record) : null;
  }

  async findAuthorizationSnapshotById(id: string): Promise<
    import('../../../application/contracts/read-models').UserAuthorizationSnapshot | null
  > {
    const record = await this.database.user.findUnique({
      where: { id },
      select: { id: true, role: true, status: true },
    });
    return record
      ? {
          id: record.id,
          role: record.role as UserRole,
          status: record.status as UserStatus,
        }
      : null;
  }

  async lockById(id: string): Promise<User | null> {
    const locked = await this.database.$queryRaw<readonly { id: string }[]>(
      Prisma.sql`SELECT id FROM users WHERE id = ${id}::uuid FOR UPDATE`,
    );
    if (locked.length === 0) return null;
    return this.findById(id);
  }

  async add(user: User): Promise<void> {
    const state = user.snapshot;
    try {
      await this.database.user.create({
        data: {
          id: state.id.value,
          email: state.email.value,
          passwordHash: state.passwordHash.value,
          role: state.role,
          status: state.status,
          createdAt: state.createdAt,
          updatedAt: state.updatedAt,
        },
      });
    } catch (error) {
      mapUserPersistenceError(error);
    }
  }

  async save(user: User): Promise<void> {
    const state = user.snapshot;
    await this.database.user.update({
      where: { id: state.id.value },
      data: { role: state.role, status: state.status, updatedAt: state.updatedAt },
    });
  }

  countActiveSuperAdmins(): Promise<number> {
    return this.database.user.count({
      where: { role: 'SUPER_ADMIN', status: 'ACTIVE' },
    });
  }

  async list(input: { pageSize: number; cursor: UserPageCursor | null }): Promise<UserPage> {
    const cursorWhere = input.cursor
      ? {
          OR: [
            { createdAt: { lt: input.cursor.createdAt } },
            { createdAt: input.cursor.createdAt, id: { lt: input.cursor.id } },
          ],
        }
      : null;
    const records = await this.database.user.findMany({
      ...(cursorWhere ? { where: cursorWhere } : {}),
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
      take: input.pageSize + 1,
      select: {
        id: true,
        email: true,
        role: true,
        status: true,
        createdAt: true,
        updatedAt: true,
      },
    });
    const hasMore = records.length > input.pageSize;
    const pageRecords = hasMore ? records.slice(0, input.pageSize) : records;
    const last = pageRecords.at(-1);
    return {
      items: pageRecords.map((record) => this.toReadModel(record)),
      nextCursor: hasMore && last ? { createdAt: last.createdAt, id: last.id } : null,
      hasMore,
    };
  }

  private toReadModel(
    record: Pick<
      PrismaUserRecord,
      'id' | 'email' | 'role' | 'status' | 'createdAt' | 'updatedAt'
    >,
  ): UserReadModel {
    return {
      id: record.id,
      email: record.email,
      role: record.role as UserRole,
      status: record.status as UserStatus,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
    };
  }
}
