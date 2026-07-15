import { ApiProperty } from '@nestjs/swagger';
import { ACCESS_TOKEN_TTL_POLICY } from '../../../application/contracts/access-token-lifetime';
import { UserRole, UserStatus } from '../../../domain/account/user';
import { PermissionCode } from '../../../domain/authorization/authorization';

/*
 * IAM 响应 DTO 是运行时返回值与 OpenAPI schema 之间的显式边界。
 * Controller 返回的匿名对象必须满足这些类型，Swagger 则通过装饰器读取完整字段元数据。
 */
export class AuthUserDto {
  @ApiProperty({ format: 'uuid' })
  readonly id!: string;

  @ApiProperty({ format: 'email' })
  readonly email!: string;

  @ApiProperty({ enum: UserRole, enumName: 'UserRole' })
  readonly role!: UserRole;

  @ApiProperty({ enum: UserStatus, enumName: 'UserStatus' })
  readonly status!: UserStatus;
}

export class UserDto extends AuthUserDto {
  @ApiProperty({ format: 'date-time' })
  readonly createdAt!: string;

  @ApiProperty({ format: 'date-time' })
  readonly updatedAt!: string;
}

export class AuthTokenDto {
  @ApiProperty({ description: '用于 Bearer 身份认证的短期访问令牌' })
  readonly accessToken!: string;

  @ApiProperty({ enum: ['Bearer'] })
  readonly tokenType!: 'Bearer';

  @ApiProperty({
    minimum: ACCESS_TOKEN_TTL_POLICY.minimumSeconds,
    maximum: ACCESS_TOKEN_TTL_POLICY.maximumSeconds,
    example: ACCESS_TOKEN_TTL_POLICY.defaultSeconds,
  })
  readonly expiresIn!: number;

  @ApiProperty({ type: () => AuthUserDto })
  readonly user!: AuthUserDto;
}

export class AuthResponseDto {
  @ApiProperty({ type: () => AuthTokenDto })
  readonly data!: AuthTokenDto;
}

export class AuthorizationSnapshotDto {
  @ApiProperty({ enum: UserRole, enumName: 'UserRole' })
  readonly tokenRole!: UserRole;

  @ApiProperty({
    enum: PermissionCode,
    enumName: 'PermissionCode',
    isArray: true,
    uniqueItems: true,
  })
  readonly permissions!: readonly PermissionCode[];

  @ApiProperty({ format: 'date-time' })
  readonly expiresAt!: string;

  @ApiProperty({
    description: '令牌角色是否已经落后于数据库中的当前角色',
  })
  readonly stale!: boolean;
}

export class CurrentUserDto {
  @ApiProperty({ type: () => AuthUserDto })
  readonly user!: AuthUserDto;

  @ApiProperty({ type: () => AuthorizationSnapshotDto })
  readonly authorization!: AuthorizationSnapshotDto;
}

export class CurrentUserResponseDto {
  @ApiProperty({ type: () => CurrentUserDto })
  readonly data!: CurrentUserDto;
}

export class UserResponseDto {
  @ApiProperty({ type: () => UserDto })
  readonly data!: UserDto;
}

export class UserPageMetaDto {
  @ApiProperty({ nullable: true, type: String })
  readonly nextCursor!: string | null;

  @ApiProperty()
  readonly hasMore!: boolean;
}

export class UserPageResponseDto {
  @ApiProperty({ type: () => [UserDto] })
  readonly data!: readonly UserDto[];

  @ApiProperty({ type: () => UserPageMetaDto })
  readonly meta!: UserPageMetaDto;
}
