import 'dotenv/config';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '@prisma/client';

async function main() {
  const prisma = new PrismaClient({
    adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL }),
  });
  try {
    await prisma.$connect();
    const result = await prisma.$queryRaw`SELECT current_database() AS db, version() AS version`;
    console.log('✓ 数据库连接成功');
    console.log(result);
  } catch (error) {
    console.error('✗ 数据库连接失败:', error);
    process.exitCode = 1;
  } finally {
    await prisma.$disconnect();
  }
}

main();
