import { api } from '@/shared/api/http';
import { createRestResource } from '@/shared/api/rest';
import type {
  CreateUserPayload,
  UpdateUserPayload,
  User,
  UserListParams,
  UserListResult,
} from '../model/types';

/**
 * 用户与身份接口（前缀 `/identity`，后端 `modules/identity/router.py`）。
 *
 * 这里**只**用通用工厂拿 list / detail：新建、改资料、分配角色、重置密码的入参
 * 与 `User` 出参形状不同（密码、roleIds 都不是 User 的字段），因此单独声明各自类型，
 * 不用 `Partial<User>` 蒙混过去——那会让「传错字段也不报错」。
 *
 * 注意两处与直觉不同、已按后端实际行为校准的细节：
 * - 重置密码的字段是 `newPassword`（不是 `password`）
 * - `DELETE /users/{id}` 是「停用」而非物理删除，返回更新后的 UserOut
 */
const base = '/identity/users';
const resource = createRestResource<User, UserListResult, UserListParams>(base);

export const userApi = {
  list: resource.list,

  create: (payload: CreateUserPayload) => api.post<User>(base, payload),
  update: (id: string, payload: UpdateUserPayload) => api.patch<User>(`${base}/${id}`, payload),

  /** 整组替换：传空数组即清空角色 */
  setRoles: (id: string, roleIds: string[]) => api.put<User>(`${base}/${id}/roles`, { roleIds }),

  /** 管理员重置密码：后端字段为 `newPassword` */
  resetPassword: (id: string, newPassword: string) =>
    api.post<null>(`${base}/${id}/reset-password`, { newPassword }),

  /** 停用（保留历史归属）：后端用 DELETE 表达，返回停用后的用户 */
  deactivate: (id: string) => api.delete<User>(`${base}/${id}`),
};

/* 这里曾经还有一个 `identityApi`（me / updateProfile / changePassword），
   与 `entities/session/api/session.api.ts` 声明的是同一批接口，零调用。
   删掉而不是留着：它的名字比 `sessionApi` 更像"身份接口"，下一个要加
   「改邮箱」的人最可能 import 它，然后发现入参类型是另一套；
   更麻烦的是后端改字段时得改两处，改一处不会被任何检查发现。

   边界写死在这里：`entities/user` 只管**管理员视角的别人**，
   当事人视角的「自己」（/identity/me 系列）只有 `entities/session` 一份。 */
