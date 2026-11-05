import { useNavigate } from 'react-router-dom';
import { Compass } from 'lucide-react';
import { HOME_PATH } from '@/shared/config/app';
import { Button, EmptyState } from '@/shared/ui';

export default function NotFoundPage() {
  const navigate = useNavigate();

  return (
    <EmptyState
      className="min-h-[360px]"
      icon={<Compass size={34} />}
      title="页面不存在"
      description="链接可能已过期，或者你输入的地址有误。"
      action={
        <Button size="sm" variant="primary" onClick={() => navigate(HOME_PATH)}>
          回到首页
        </Button>
      }
    />
  );
}
