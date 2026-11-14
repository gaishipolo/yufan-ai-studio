# 通过 AI 会话封装视频模板

封装 = 一份 manifest 配置 + 一次发布。2026-11-14 起入口是
`POST /api/v1/template-manifests` 系列接口（配置存库、发布时投影成工作台卡片），
离线脚本 `api/scripts/package_video_template.py` 已下线。

## 前置：素材先进资产库

manifest 里只写 `assetId`，不再接受本地文件路径。先把原片与参考图上传：

```bash
curl -X POST "http://127.0.0.1:8080/api/v1/library/source-videos?filename=clip.mp4" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
  --data-binary @clip.mp4
curl -X POST "http://127.0.0.1:8080/api/v1/library/assets/upload?filename=front.png&kind=image" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
  --data-binary @front.png
```

两个响应里的 `data.id` 就是 manifest 要的 `video.assetId` 与 `references[].assetId`。

## 三步发布

```bash
# 1. 预检：纯规则 + 素材存在性，不落库；返回配置指纹
curl -X POST http://127.0.0.1:8080/api/v1/template-manifests/validate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @manifest.json

# 2. 保存草稿（幂等：同 key 同指纹返回原配置）
curl -X POST http://127.0.0.1:8080/api/v1/template-manifests \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @manifest.json

# 3. 发布：写素材包 + 视频模板，返回每张卡片的 templateId 与 workbenchPath
curl -X POST http://127.0.0.1:8080/api/v1/template-manifests/<key>/publish \
  -H "Authorization: Bearer $TOKEN"
```

需要 `template_pack:read` / `template_pack:create` / `template_pack:publish`；
投影出来的素材包与模板另需 `template:pack:manage` / `template:pack:publish`。

## manifest 形状

```json
{
  "key": "dress-demo",
  "name": "连衣裙演示",
  "video": {"assetId": "<source-video-id>", "shared": false},
  "packs": [
    {
      "key": "dress",
      "name": "连衣裙",
      "kind": "product",
      "supportedScopes": ["worn"],
      "references": [{"key": "front", "assetId": "<image-id>", "role": "front"}]
    }
  ],
  "functions": [
    {"key": "replace", "name": "产品替换", "pack": "dress",
     "prompt": "对 {{Mixed 1}} 使用 {{Mixed 2}}。"}
  ]
}
```

- `packs` 可以为空（例如只做「文字换场景」）；`functions[].pack` 指向包 key。
- **复用已发布素材包**：`{"key": "reuse", "kind": "product", "packId": "<id>"}`，
  `packId` 取自 `GET /templates/replacement-packs/available`（**运行素材包** id，
  不是配置行 id）；复用包不能覆盖名称 / 用途 / 范围。
- `functions[].bindings` 与 `mediaOrder` 二选一：给了 bindings 就按用途绑定编号，
  不给则由 `mediaOrder`（或素材包原序）决定 `{{Mixed N}}`。
- 文字换场景：`functions[].kind = "scene"` + `scenePromptDefault`，不提供 `pack`、
  `references`、`scopeOptions`。

严格模式：未声明的键直接 422，不会静默忽略。

## 幂等与版本

- 同 `key` + 同指纹重复保存 → 返回原配置，不新增行。
- 同 `key` 不同内容 → 409；**已发布的配置不可再改**，要改就换 `key` 发新版本。
- 发布回执里的 `publishedPackId` / `publishedTemplateId` 指向**投影出来的运行行**，
  重跑时拿它们比对远端即可；停用只下架、不清空历史。

## 恢复会话

把这份 manifest 与 `key` 交给 AI 即可继续：用同一个 `key` 和同样的内容重发
`/validate` → `POST` → `/publish`，不需要进度文件（幂等边界在数据库上）。
