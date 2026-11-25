import { Navigate } from 'react-router-dom';
import { useSubject } from '@/shared/access/use-access';
import { firstAccessiblePath } from '@/shared/registry';
import { useRegistry } from '@/shared/registry/use-registry';

/**
 * 根路由 `/`（点品牌名、手输根地址）的落地页。
 *
 * 必须是组件而不是 router 里的静态 `<Navigate>`：目标取决于当前用户的权限，
 * 而 router 在模块加载期就构造好了，那时还没有身份。渲染时读 subject 才能
 * 与登录页用同一套判据（firstAccessiblePath），避免「登录后跳对页、点品牌名
 * 又被弹回无权访问的首页」。
 *
 * 放在 RequireAuth 之内：只有已登录用户才会走到这里。
 */
export function HomeRedirect() {
  const subject = useSubject();
  const registry = useRegistry();
  return <Navigate to={firstAccessiblePath(subject, registry)} replace />;
}
