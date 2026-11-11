import { useCan } from '@/shared/access/use-access';
import { useCreateTag } from '@/entities/library/api/library.queries';
import { toast } from '@/shared/lib/toast';

/** 建成返回新标签 id，没建成返回 `null`。第二个参数给一级 id 就建成二级；
 *  留空建成一级。`MultiSelect.onCreate` 只传第一个参数，可选参数保持兼容。 */
export type CreateTagInline = (name: string, parentId?: string) => Promise<string | null>;

/**
 * 「打标签时发现没有想要的词」的就地新建。
 *
 * 为什么是 `library:create` 而不是 `library:tag:manage`：新建标签对能上传的人
 * 开放，否则新账号永远打不了第一个标签；改名/删除词表才要 `tag:manage`。
 * 这是设计 §权限表定的口径，不是漏判。
 *
 * 没有权限时返回 `undefined`——正好等于「不给 `MultiSelect` 传 `onCreate`」，
 * 下拉里就不会出现那一行输入框。让组件自己判权限的写法会把「有没有权限」
 * 和「长什么样」搅在一起，调用方也没法一眼看出这个入口是可选的。
 *
 * 同名由后端幂等处理（返回已存在的那条），所以用户重复输同一个词等于「选中它」，
 * 不会产生第二条，也不会报错。
 */
export function useCreateTagInline(): CreateTagInline | undefined {
  const canCreate = useCan('library:create');
  const create = useCreateTag();

  if (!canCreate) return undefined;

  return async (name: string, parentId?: string) => {
    try {
      const tag = await create.mutateAsync({ name, parentId });
      return tag.id;
    } catch (error) {
      toast(error instanceof Error ? error.message : '新建标签失败', 'error');
      return null;
    }
  };
}
