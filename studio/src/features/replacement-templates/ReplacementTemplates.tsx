import { ReplacementBatch } from './ReplacementBatch';
import { ReplacementConfig } from './ReplacementConfig';
import { ReplacementDetailModal } from './ReplacementDetailModal';
import { ReplacementFeedback } from './ReplacementFeedback';
import { ReplacementReviewModal } from './ReplacementReviewModal';
import { ReplacementSourceLibrary } from './ReplacementSourceLibrary';
import { ReplacementTaskHistory } from './ReplacementTaskHistory';
import { useReplacementWorkbench } from './useReplacementWorkbench';
import './replacement-templates.css';

export function ReplacementTemplates() {
  const model = useReplacementWorkbench();
  return <div className="replacement-library">
    {model.detailId && model.catalog.data && !model.currentDetail && !model.catalog.error &&
      <p role="alert" className="rounded-lg bg-panel-soft p-3 text-sm text-danger">未找到此替换档案，请确认任务编号和当前登录账号。</p>}
    <ol aria-label="创作步骤" className="replacement-steps">
      <li aria-current={!model.sourceId ? 'step' : undefined}>1. 选择原片</li>
      <li aria-current={!!model.sourceId && !model.review ? 'step' : undefined}>2. 点击主图选择目标</li>
      <li aria-current={model.review ? 'step' : undefined}>3. 确认替换</li>
      <li>4. 在任务记录中查看、下载和评价</li>
    </ol>
    <ReplacementFeedback model={model} />
    <div className="replacement-layout">
      <ReplacementSourceLibrary model={model} />
      <ReplacementConfig model={model} />
    </div>
    <input ref={model.uploadInput} type="file" hidden aria-label="替换模板上传素材" onChange={model.handleUpload} />
    <ReplacementBatch model={model} />
    <ReplacementTaskHistory model={model} />
    <ReplacementReviewModal model={model} />
    <ReplacementDetailModal model={model} />
  </div>;
}
