import { Prisma } from '@prisma/client';
import {
  RefreshContext,
  RefreshTokenRepository,
} from '../../../application/ports/persistence.ports';
import { RefreshToken } from '../../../domain/session/refresh-token';
import { PrismaIamMapper } from './prisma-iam.mapper';
import { mapConcurrentPersistenceError } from './prisma-persistence-errors';

/*
 * Refresh 锁定查询先用 hash 定位，再严格按 users、sessions、tokens 顺序加行锁。
 * 获锁后重新读取完整状态，初始定位结果绝不作为授权判断依据。
 */
type RefreshExecutor = Pick<
  Prisma.TransactionClient,
  'user' | 'authSession' | 'refreshToken' | '$queryRaw'
>;

interface RefreshLocator {
  readonly userId: string;
  readonly sessionId: string;
  readonly tokenId: string;
}

export class PrismaRefreshTokenRepository extends RefreshTokenRepository {
  constructor(private readonly database: RefreshExecutor) {
    super();
  }

  async add(token: RefreshToken): Promise<void> {
    const state = token.snapshot;
    try {
      await this.database.refreshToken.create({
        data: {
          id: state.id.value,
          sessionId: state.sessionId.value,
          tokenHash: state.tokenHash.value,
          status: state.status,
          rotatedAt: state.rotatedAt,
          revokedAt: state.revokedAt,
          createdAt: state.createdAt,
        },
      });
    } catch (error) {
      mapConcurrentPersistenceError(error);
    }
  }

  async save(token: RefreshToken): Promise<void> {
    const state = token.snapshot;
    await this.database.refreshToken.update({
      where: { id: state.id.value },
      data: {
        status: state.status,
        rotatedAt: state.rotatedAt,
        revokedAt: state.revokedAt,
      },
    });
  }

  async lockContextByTokenHash(tokenHash: string): Promise<RefreshContext | null> {
    const locators = await this.database.$queryRaw<readonly RefreshLocator[]>(
      Prisma.sql`
        SELECT
          u.id AS "userId",
          s.id AS "sessionId",
          t.id AS "tokenId"
        FROM refresh_tokens t
        JOIN auth_sessions s ON s.id = t.session_id
        JOIN users u ON u.id = s.user_id
        WHERE t.token_hash = ${tokenHash}
      `,
    );
    const locator = locators[0];
    if (!locator) return null;

    await this.database.$queryRaw(
      Prisma.sql`SELECT id FROM users WHERE id = ${locator.userId}::uuid FOR UPDATE`,
    );
    await this.database.$queryRaw(
      Prisma.sql`SELECT id FROM auth_sessions WHERE id = ${locator.sessionId}::uuid FOR UPDATE`,
    );
    await this.database.$queryRaw(
      Prisma.sql`SELECT id FROM refresh_tokens WHERE id = ${locator.tokenId}::uuid FOR UPDATE`,
    );

    const [user, session, token] = await Promise.all([
      this.database.user.findUnique({ where: { id: locator.userId } }),
      this.database.authSession.findUnique({ where: { id: locator.sessionId } }),
      this.database.refreshToken.findUnique({ where: { id: locator.tokenId } }),
    ]);
    if (!user || !session || !token) return null;
    return {
      user: PrismaIamMapper.userToDomain(user),
      session: PrismaIamMapper.sessionToDomain(session),
      token: PrismaIamMapper.refreshTokenToDomain(token),
    };
  }
}
