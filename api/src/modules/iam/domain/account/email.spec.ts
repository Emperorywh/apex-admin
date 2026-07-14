import { Email } from './email';

/*
 * Email 测试固定 canonical 规则与恢复边界。
 * 这些用例不启动 NestJS，也不连接数据库。
 */
describe('Email', () => {
  it('会 trim 并转为小写唯一表示', () => {
    expect(Email.create('  Alice@Apex.Local ').value).toBe('alice@apex.local');
  });

  it.each(['alice', 'alice@localhost', '@apex.local', 'alice @apex.local'])(
    '拒绝非法邮箱 %s',
    (value) => expect(() => Email.create(value)).toThrow('邮箱格式无效'),
  );

  it('拒绝超过 320 字符的邮箱', () => {
    expect(() => Email.create(`${'a'.repeat(310)}@apex.local`)).toThrow();
  });

  it('恢复时拒绝非 canonical 持久值', () => {
    expect(() => Email.restore('Alice@apex.local')).toThrow('不是规范值');
  });
});
