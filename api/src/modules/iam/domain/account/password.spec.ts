import { NewPassword, normalizePassword } from './password';

/*
 * 密码测试按 Unicode code point 验证边界，并确保空格不被 trim。
 * NFC 规范化是唯一允许的静默表示转换。
 */
describe('NewPassword', () => {
  it('接受 15 和 128 个 Unicode code point', () => {
    expect(NewPassword.create('😀'.repeat(15)).value).toBe('😀'.repeat(15));
    expect(NewPassword.create('界'.repeat(128)).value).toBe('界'.repeat(128));
  });

  it('拒绝边界外长度', () => {
    expect(() => NewPassword.create('a'.repeat(14))).toThrow();
    expect(() => NewPassword.create('a'.repeat(129))).toThrow();
  });

  it('保留前后空格且执行 NFC', () => {
    const raw = ` ${'e\u0301'.repeat(13)} `;
    const value = NewPassword.create(raw).value;
    expect(value.startsWith(' ')).toBe(true);
    expect(value.endsWith(' ')).toBe(true);
    expect(value).toBe(normalizePassword(raw));
  });
});
