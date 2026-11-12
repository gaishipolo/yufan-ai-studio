# 模板提示词编辑与 @ 引用迭代方案

> 执行方式：使用 writing-plans 制定计划，使用 executing-plans 在当前会话逐项实施；按用户要求，不使用子代理。

**Goal:** 在 `/templates` 编辑已封装功能的固定提示词，通过 `@` 选择该模板参考图，原地保存且不增加任何素材描述。

**Architecture:** 在现有 `TemplatePromptDialog` 内提供独立编辑组件；复用媒体用途解析与 PATCH 提示词接口。正文以纯文本为唯一真相，选择候选项仅将光标处的 `@查询` 替换为现有 `{{Mixed N}}`。保留所有其他字符与既有媒体顺序。

**Tech Stack:** React / TypeScript / React Query / Vitest；FastAPI / Pydantic / pytest。

## 已确认约束

- 画布测试通过的提示词必须原样保存与使用，不追加图片描述、用途、文件名或自动生成文案。
- 仅用户主动编辑及插入引用时修改正文；既有范围、场景参数与渠道引用转换继续按原有规则处理。
- 引用对象为模板已有媒体槽位；支持当前素材包按用途显示正面图、三视图等，不跨包新增文件，不重排输入。
- 删除正文引用不删除实际输入图片；沿用辅助图完整附带的既有行为。
- 有管理权限者可编辑；普通使用者仍可预览。保存影响后续准备任务，已有任务快照不变。
- 原工作区其他未提交修改保持不动。当前分支 `codex/template-prompt-editor`；本次在当前工作区开发以服务本机预览。

## 迭代 1：原文保存与编辑入口

目标：卡片预览可进入编辑模式，修改保存到原模板，刷新后保留，包括首尾空格、换行。

Files:
- `api/src/modules/template/video_template_schema.py`：仅 prompt 字段关闭去空白。
- `api/src/modules/template/schema.py`：任务 prompt 出参精确保留。
- `api/src/modules/template/video_template_store.py`：更新原文校验与注释。
- `studio/src/entities/video-template/api/videoTemplate.api.ts`、`videoTemplate.queries.ts`：PATCH 与刷新模板缓存。
- `studio/src/features/replacement-templates/TemplatePromptDialog.tsx`：管理权限编辑入口与模式切换。

- [x] 先增加原文保存和编辑交互测试，运行并确认失败原因。
- [x] 实现字段级 `StringConstraints(strip_whitespace=False)`，空白正文仍拒绝，按原文计算 SHA256。
- [x] 接入 `api.patch<VideoTemplateOut>(.../prompt, {prompt})`；成功更新缓存，失败保留草稿。
- [x] 覆盖取消编辑、无权限、保存失败、重复保存防护与编辑功能切换。

## 迭代 2：@ 引用与即时预览

目标：输入 `@` 显示可搜索的模板媒体候选，选择正面图或三视图仅插入引用标记，可核对最终原文与图片。

Files:
- 新建 `studio/src/features/replacement-templates/TemplatePromptEditor.tsx`：纯文本编辑、光标插入、候选选择、错误与保存。
- 新建 `studio/src/features/replacement-templates/promptMentions.ts`：查询识别、精确插入与引用合法性校验。
- 复用 `templatePromptMedia.ts` 和 `PromptMediaView.tsx`。

- [x] 测试在中文句中插入、选区替换、重复引用、键盘选择、中文输入法组合输入与 Escape 关闭候选。
- [x] 实现搜索、缩略图、用途标签（仅用于界面）与引用标记展示；不将描述拼入正文。
- [x] 未选择图片槽位保留编号，不能把后续图片移到前面；禁止编辑器插入模板媒体数量之外的引用。
- [x] 预览使用编辑草稿，保留参数原文，不用当前场景描述替换编辑内容。

## 迭代 3：一致性与回归

目标：保存 → 读取 → 编译 → 实际请求路径的正文一致；既有预览、任务准备与渠道行为正常。

- [x] pytest 覆盖原文逐字保存/读取/摘要、空白拒绝、原片和图片引用校验；固定模板编译包含 usage 元数据时不附加说明。
- [x] Vitest 覆盖保存的请求正文与用户输入相等、@ 引用只替换所选文本、失败保留草稿、取消不提交。
- [x] 执行前端 typecheck、lint、test、build；执行后端相关模板/契约回归与 lint。
- [x] 根据 OpenAPI 守卫检查是否需要由 `app export-openapi` 同步生成物（不手改生成文件）。
- [x] 自行审阅 diff，记录检查结果与已知限制，更新本计划勾选状态。

## 验证命令

```powershell
# studio
npm.cmd test -- src/features/replacement-templates
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
# api，使用本项目 .venv Python
.venv/Scripts/python.exe -m pytest tests/test_video_template_prompt.py tests/test_video_template_binding.py tests/test_openapi_contract.py -q --basetemp=var/pytest-prompt-editor
.venv/Scripts/python.exe -m ruff check src/modules/template/video_template_schema.py src/modules/template/schema.py src/modules/template/video_template_store.py tests/test_video_template_prompt.py
```

## 实施结果（2026-11-12）

- 三个迭代均已完成：预览弹窗内编辑原模板；@ 搜索、缩略图、键盘及选区替换；保存、原文一致性和回归验证。
- 新增 `VerbatimText` 仅用于模板提示词与任务提示词出参，保留首尾空白；摘要按相同原文计算。其他字符串字段的去空白行为不变。
- 产品与场景的编辑候选按模板完整用途对齐，换素材包后不会以偶然的包内排序冒充模板槽位；多人模板继续按已有身份槽位预览。
- `npm.cmd run verify`：typecheck、lint、647 项测试、生产构建全部通过。
- 后端 8 个相关测试文件：80 passed；变更文件 ruff 通过；OpenAPI 契约守卫通过，无需改动生成文件。
- 隔离浏览器页面使用真实组件和内存 HTTP 适配器，完成 @三视图 → 点击候选 → 保存 → 比较请求正文与保存原文；首尾空格/换行一致，图片描述未带入。临时验收文件已删除。
- 真实工作台未登录联调：自动审批拒绝使用仓库示例管理员账号（本轮未明确授权使用该账号），没有重试或绕过；没有修改真实模板或提交生成。
- 自行完成差异审阅，未使用子代理。保留用户原有未提交修改，未提交、推送或合并。
- 本机启动脚本没有 `--reload`，后端需重启后应用 prompt 字段原文保留修正；前端开发服务器自动更新。
- 当前范围为修改正文对已有槽位的引用；跨包新增参考图片、删除实际输入图片和媒体重排不在本轮范围内。