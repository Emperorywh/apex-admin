import { Transform } from 'class-transformer';
import { IsEmail, IsString, MaxLength, MinLength } from 'class-validator';
import { trimEmailInput } from './canonical-email.transform';

/*
 * Auth DTO 只验证 HTTP 字段形状与合理传输上限。
 * 密码 NFC、15–128 code point 与 blocklist 仍由领域/应用层负责。
 */
export class LoginRequestDto {
  @Transform(trimEmailInput)
  @IsEmail()
  @MaxLength(320)
  email!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(512)
  password!: string;
}
