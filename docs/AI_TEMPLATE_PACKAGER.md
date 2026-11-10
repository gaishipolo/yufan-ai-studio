# 通过 AI 会话封装视频模板

实现入口：`api/scripts/package_video_template.py`。一份 JSON 配置描述一个视频、一个或多个参考素材包、一个或多个功能；它们落为 `/templates` 中的一张原片卡片及该卡片的功能选项。运行报告中的 `workbenchPath` 是前端相对链接，接在当前前端地址后即可打开。

## 在会话中怎么用

可以直接对 AI 说：

> 按 docs/AI_TEMPLATE_PACKAGER.md 封装模板。视频在 D:/素材/原片.mp4，参考图在 D:/素材/主图.png 和 D:/素材/三视图.png，提示词在 D:/素材/提示词.txt。卡片叫「连衣裙演示」，功能叫「服装替换」，默认私有。请先预检，再完成封装并返回卡片链接。

同一视频有多个功能时，为每项功能给出名称、提示词和使用的参考素材包。没有逐图用途、替换范围或分享意图时，AI 应先查明这些业务信息；不要从文件名擅自推断。素材包可以被多项功能复用。

后续会话应读取本文和已有配置，用相同 key、相同配置和命令恢复；不要重新生成 key 或删除进度文件来绕过冲突。只有明确要求创建新版本时才使用新 key。

## 最短调用

在项目根目录运行，使用项目已有虚拟环境，无需安装新依赖：

```powershell
# 1. 本地预检：不需要登录，不联网，不写进度文件
.\api\.venv\Scripts\python.exe .\api\scripts\package_video_template.py D:\素材\manifest.json

# 2. 只检查后端契约与账号权限
.\api\.venv\Scripts\python.exe .\api\scripts\package_video_template.py D:\素材\manifest.json --check-server

# 3. 创建素材、发布素材包、创建绑定模板，不执行视频生成
.\api\.venv\Scripts\python.exe .\api\scripts\package_video_template.py D:\素材\manifest.json --apply
```

第 2、3 步从环境变量 `YUFAN_API_TOKEN` 读取当前工作台账号的访问令牌。账号需具备 `asset:read`、`asset:create`、`asset:reference:manage`、`template:read`、`template:pack:manage`、`template:pack:publish`；分享还需 `asset:share`。令牌由已授权的登录流程取得，只放进进程环境，不写进配置、进度文件、报告、命令参数或聊天正文。本工具不自动登录、不读取浏览器登录态；账号无权限时不会自动提升权限。

默认 API 为 `http://127.0.0.1:8080/api/v1`，可用 `--api-url` 指定另一部署。先更新并重启后端以加载本次契约：封装器会检查 `videoTemplateId` 和创建接口的 `x-template-key-idempotency: 1`，旧服务会在任何上传前被拒绝。不会自动重启现有服务。

## 配置

复制 [完整的双功能示例](examples/template-package/manifest.json) 和 [提示词文件](examples/template-package/replace.txt) 到素材文件夹，替换视频与图片路径。示例不附带素材，需自行提供 `video.mp4`、`front.png`、`views.png` 才能通过预检。

最小单功能配置：

```json
{
  "key": "dress-v1",
  "name": "连衣裙",
  "video": {"file": "video.mp4", "shared": false},
  "packs": [{
    "key": "dress",
    "name": "目标连衣裙",
    "kind": "product",
    "references": [{"key": "front", "file": "front.png", "role": "front"}],
    "supportedScopes": ["worn"]
  }],
  "functions": [{
    "key": "replace",
    "name": "服装替换",
    "pack": "dress",
    "prompt": "编辑原片 {{Mixed 1}}，将主角服装替换为 {{Mixed 2}} 中的款式。保持其他内容。"
  }]
}
```

- 所有本地相对路径相对于配置文件，而非当前命令目录。支持中文路径，JSON 和提示词使用 UTF-8，可带 BOM。
- `key` 使用字母、数字、下划线、连字符，最多 40 字符。顶层 key 是一次封装的稳定身份；包 key、功能 key 各自在列表内唯一。
- 视频为完整 MP4，最大 300MB。真实时长、音轨和媒体格式由上传 API 探测；原片要求 1–15 秒且有音轨。失败的素材可能已归档，但不会继续发布或创建卡片。
- 参考图支持 PNG/JPG/JPEG/WebP，一个包 1–10 张，不能重复内容。产品用途为 `front/multiview/detail/back/side`，人物为 `front/side/fullbody`，场景为 `main/angle/detail`。产品和人物必须含 `front`，场景必须含 `main`。可用 `requiredRoles` 声明额外必备用途。
- 产品包需声明审核过的 `supportedScopes`：`worn` 上身、`held` 手持、`boxed` 盒内、`screen` 手机详情页。这是现有素材包发布规则。人物、场景包可以省略。
- 功能用 `pack` 引用包 key，处理类型沿用包的 `kind`。同一处理类型可以定义多个功能。
- `prompt` 和 `promptFile` 二选一。`{{Mixed 1}}` 固定为视频，其后默认按包内参考图顺序排列。功能可用 `mediaOrder: ["views", "front"]` 调整顺序，必须完整列出当前包的参考图 key。
- 普通固定提示词保留原文；如果配置 `scopeOptions`，必须同时在提示词中写 `{{ReplacementScope}}`，运行时只替换该参数。无选项时工作台不显示范围控件。报告摘要对应封装原文，含参数时最终运行文案会不同。
- 功能可选 `aspectRatio` 为 `9:16/16:9/1:1`，默认 `9:16`；可选 `duration` 为 2–15 秒整数，不填则沿用服务端按原片时长选档的规则。

## 可见性、重试和输出

参考素材包会发布到授权账号可调用目录。视频默认私有，因此卡片默认在封装账号的「我的原片」可见；`video.shared: true` 才主动分享视频，使其进入「公共原片」。`false` 不会撤回此前已分享的视频，重复上传相同原片时遵循已有分享状态，报告以实际状态为准。

每次执行返回 JSON，包含 `sourceId`、`sourceName`、每项功能的 `videoTemplateId`、`packId`、`packVersion`、`promptSha256`、`mediaOrder`、`workbenchPath`。结果也保存在进度文件的 `report` 中。顶层 `name` 用于各功能模板名称的前缀；卡片标题仍取已归档原片名称，本工具不会改名已有原片。即使远端模板仍保留相同来源指纹，绑定原片、提示词或媒体配置变化也会在复用时拒绝。

默认进度文件在 `api/var/template-packager/`，不进入 Git。可用 `--state D:/素材/封装进度.json` 显式指定。中途失败后原样重跑：上传按账号、分类和内容摘要去重；素材包使用确定性 requestId；模板稳定键由账号、封装 key 和功能 key 派生。同键同内容返回原模板，同键异内容拒绝。进度文件绑定 API 地址、账号和配置/媒体内容摘要，换账号或内容变化会停止。文件夹整体移动不影响内容摘要。

多个步骤不是一个跨系统事务；失败前完成的上传或发布会保留，用于重试恢复，不自动删除。即使进度文件丢失，也可用相同配置从远端恢复已有包和模板；素材上传可能再次传输字节，但仍复用同一归档资产。进程异常退出残留 `.lock` 时，确认旧进程已结束后再移除对应锁文件。

当前视频模板目录是 JSON 文件 + 进程内锁，本 MVP 只承诺单后端进程下的模板创建防重；多 worker/多机部署需先迁移模板存储。并发会话应使用同一进度文件以互斥运行。

## 验证

```powershell
cd api
.\.venv\Scripts\python.exe -m pytest tests/test_template_packager.py tests/test_video_template_idempotency.py tests/test_video_template_binding.py -q -p no:cacheprovider
```

包含真实项目 API 的隔离联调：真实媒体上传与探测、DB 素材包发布、模板创建、重复执行、进度丢失恢复，以及分别准备两项功能并核对实际提示词。测试只到输入准备，不执行视频生成、不调用外部生成服务。
