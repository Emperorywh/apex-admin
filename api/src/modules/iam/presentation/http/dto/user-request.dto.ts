import { Transform } from 'class-transformer';
import {
  IsEmail,
  IsEnum,
  IsInt,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';
import { UserRole } from '../../../domain/account/user';
import { trimEmailInput } from './canonical-email.transform';

/*
 * 用户管理 DTO 按业务动作拆分，只暴露该端点允许提交的字段。
 * 对象级角色授权与唯一性不通过自定义 Validator 查询数据库。
 */
export class CreateUserRequestDto {
  @Transform(trimEmailInput)
  @IsEmail()
  @MaxLength(320)
  email!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(512)
  password!: string;

  @IsEnum(UserRole)
  role!: UserRole;
}

export class ChangeUserRoleRequestDto {
  @IsEnum(UserRole)
  role!: UserRole;
}

export class ListUsersQueryDto {
  @IsOptional()
  @Transform(({ value }) => Number(value))
  @IsInt()
  @Min(1)
  @Max(100)
  pageSize?: number;

  @IsOptional()
  @IsString()
  @MaxLength(512)
  cursor?: string;
}
