import { useState } from 'react';
import { Button } from '@/shared/ui';
import { useArchiveImport } from '@/entities/asset/api/archive.queries';
import { ARCHIVE_LABELS, type ArchiveCategory } from '@/entities/asset/model/archive.types';

interface ArchiveImportFormProps {
  category: ArchiveCategory;
  /** 上传批次在途时不能再登记，反之亦然：两个入口共用一个互斥域。 */
  disabled?: boolean;
}

/**
 * 从 inbox 登记已有文件。
 *
 * 反馈留在表单里：toast 只活 2.2 秒，用户还没来得及对照路径就消失了，
 * 而"哪条路径失败了"正是他下一步要改的东西。成功才清空输入，失败保留路径。
 */
export function ArchiveImportForm({category, disabled = false}: ArchiveImportFormProps) {
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  const importing = useArchiveImport();
  const label = ARCHIVE_LABELS[category];
  return <form className="space-y-2 rounded-xl border border-line p-3" onSubmit={event => {
    event.preventDefault();
    const value = path.trim();
    if (!value || importing.isPending || disabled) return;
    setError(''); setDone('');
    importing.mutate({relativePath: value, category}, {
      onSuccess: () => {setPath(''); setDone(`已登记并复制到${label}`);},
      onError: reason => setError(reason.message),
    });
  }}>
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor="archive-import-path" className="flex min-w-64 flex-1 items-center gap-2 text-xs text-ink-soft">
        从 inbox 登记
        <input id="archive-import-path" value={path} required disabled={importing.isPending || disabled}
          onChange={event => setPath(event.target.value)} placeholder="相对于 inbox 的路径，例如 人物/参考图.png"
          className="min-w-0 flex-1 rounded-lg border border-line bg-panel p-2" />
      </label>
      <Button type="submit" loading={importing.isPending} disabled={importing.isPending || disabled}>登记到{label}</Button>
    </div>
    <p className="text-xs text-ink-mute">先把文件放入归档盘的 inbox，再填写相对路径；inbox 中的原件保留。</p>
    {disabled && <p className="text-xs text-ink-mute">上传批次进行中，结束后可登记。</p>}
    {error && <p role="alert" className="text-xs text-danger">登记失败：{error}</p>}
    {done && <p role="status" className="text-xs text-ok">{done}</p>}
  </form>;
}
