import { Prisma } from '@prisma/client';
import { IamErrors } from '../../../application/errors/iam.error';

/*
 * Prisma 错误只在持久化边界转换为稳定应用错误。
 * 约束名、SQL 与驱动堆栈不会进入 Presentation。
 */
export function mapUserPersistenceError(error: unknown): never {
  if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2002') {
    throw IamErrors.userEmailAlreadyUsed();
  }
  throw error;
}

export function mapConcurrentPersistenceError(error: unknown): never {
  if (isRetryableTransactionError(error)) throw IamErrors.concurrentModification();
  if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2002') {
    throw IamErrors.concurrentModification();
  }
  throw error;
}

export function isRetryableTransactionError(error: unknown): boolean {
  if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2034') {
    return true;
  }
  if (typeof error !== 'object' || error === null) return false;
  const code = (error as { readonly code?: unknown }).code;
  return code === '40001' || code === '40P01' || code === '55P03';
}
