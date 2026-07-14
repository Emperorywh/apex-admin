import {
  Body,
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Patch,
  Post,
  Query,
  Req,
  Res,
} from '@nestjs/common';
import type { Response } from 'express';
import type { AuthenticatedActor } from '../../application/contracts/authenticated-actor';
import { ChangeUserRoleUseCase } from '../../application/use-cases/accounts/change-user-role.use-case';
import { CreateUserUseCase } from '../../application/use-cases/accounts/create-user.use-case';
import { DisableUserUseCase } from '../../application/use-cases/accounts/disable-user.use-case';
import { EnableUserUseCase } from '../../application/use-cases/accounts/enable-user.use-case';
import { GetUserUseCase } from '../../application/use-cases/accounts/get-user.use-case';
import { ListUsersUseCase } from '../../application/use-cases/accounts/list-users.use-case';
import { PermissionCode } from '../../domain/authorization/authorization';
import { CurrentActor } from './decorators/current-actor.decorator';
import { RequirePermissions } from './decorators/require-permissions.decorator';
import {
  ChangeUserRoleRequestDto,
  CreateUserRequestDto,
  ListUsersQueryDto,
} from './dto/user-request.dto';
import { UserIdPipe } from './user-id.pipe';
import { UserPageCursorCodec } from './user-page-cursor.codec';
import { presentUser } from './user.presenter';
import type { IamRequestContext } from './iam-request-context';

/*
 * Users Controller 为每个管理动作调用一个明确用例。
 * 路由权限由 Guard 粗筛，对象级 Policy 始终在用例内再次执行。
 */
@Controller({ path: 'users', version: '1' })
export class UsersController {
  constructor(
    private readonly createUser: CreateUserUseCase,
    private readonly getUser: GetUserUseCase,
    private readonly listUsers: ListUsersUseCase,
    private readonly changeRole: ChangeUserRoleUseCase,
    private readonly disableUser: DisableUserUseCase,
    private readonly enableUser: EnableUserUseCase,
    private readonly cursorCodec: UserPageCursorCodec,
  ) {}

  @Post()
  @RequirePermissions(PermissionCode.USER_CREATE)
  async create(
    @Body() body: CreateUserRequestDto,
    @CurrentActor() actor: AuthenticatedActor,
    @Req() request: IamRequestContext,
    @Res({ passthrough: true }) response: Response,
  ) {
    const user = await this.createUser.execute({
      actor,
      email: body.email,
      password: body.password,
      role: body.role,
      correlationId: request.traceId,
    });
    response.location(`/v1/users/${user.id}`);
    return { data: presentUser(user) };
  }

  @Get()
  @RequirePermissions(PermissionCode.USER_READ)
  async list(@Query() query: ListUsersQueryDto) {
    const page = await this.listUsers.execute({
      pageSize: query.pageSize ?? 20,
      cursor: this.cursorCodec.decode(query.cursor),
    });
    return {
      data: page.items.map(presentUser),
      meta: {
        nextCursor: page.nextCursor ? this.cursorCodec.encode(page.nextCursor) : null,
        hasMore: page.hasMore,
      },
    };
  }

  @Get(':id')
  @RequirePermissions(PermissionCode.USER_READ)
  async detail(@Param('id', UserIdPipe) userId: string) {
    return { data: presentUser(await this.getUser.execute(userId)) };
  }

  @Patch(':id/role')
  @RequirePermissions(PermissionCode.USER_ROLE_ASSIGN)
  async updateRole(
    @Param('id', UserIdPipe) userId: string,
    @Body() body: ChangeUserRoleRequestDto,
    @CurrentActor() actor: AuthenticatedActor,
    @Req() request: IamRequestContext,
  ) {
    const user = await this.changeRole.execute({
      actor,
      targetUserId: userId,
      nextRole: body.role,
      correlationId: request.traceId,
    });
    return { data: presentUser(user) };
  }

  @Post(':id/disable')
  @HttpCode(HttpStatus.NO_CONTENT)
  @RequirePermissions(PermissionCode.USER_STATUS_CHANGE)
  async disable(
    @Param('id', UserIdPipe) userId: string,
    @CurrentActor() actor: AuthenticatedActor,
    @Req() request: IamRequestContext,
  ): Promise<void> {
    await this.disableUser.execute({
      actor,
      targetUserId: userId,
      correlationId: request.traceId,
    });
  }

  @Post(':id/enable')
  @HttpCode(HttpStatus.NO_CONTENT)
  @RequirePermissions(PermissionCode.USER_STATUS_CHANGE)
  async enable(
    @Param('id', UserIdPipe) userId: string,
    @CurrentActor() actor: AuthenticatedActor,
    @Req() request: IamRequestContext,
  ): Promise<void> {
    await this.enableUser.execute({
      actor,
      targetUserId: userId,
      correlationId: request.traceId,
    });
  }
}
