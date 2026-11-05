import { ShieldAlert } from 'lucide-react';
import { Button } from './Button';
import { EmptyState } from './EmptyState';

/**
 * 403 展示组件：不依赖 router / 请求层，任何地方都能渲染
 * （路由守卫、弹窗内、测试里）
 */
export function ForbiddenState({
  permissions,
  onBack,
}: {
  /** 缺失的权限点，便于开发期定位是配错了还是没授到权 */
  permissions?: string[];
  onBack?: () => void;
}) {
  return (
    <EmptyState
      className="min-h-[320px]"
      icon={<ShieldAlert size={34} />}
      title="没有访问权限"
      description={
        permissions?.length
          ? `当前账号缺少权限：${permissions.join('、')}`
          : '当前账号无权访问该页面，请联系管理员分配权限。'
      }
      action={
        <Button size="sm" variant="secondary" onClick={onBack ?? (() => window.history.back())}>
          返回上一页
        </Button>
      }
    />
  );
}
