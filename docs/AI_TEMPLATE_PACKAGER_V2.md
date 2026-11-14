# AI 会话调用指南：按用途封装模板（V2 已并入统一模型）

> **2026-11-14：本文描述的 `/template-manifests` 三步接口与「素材包 / 功能」两段结构
> 已随替换链下线。** 现在只有一种模板：`/template-specs`。
> 契约看 `docs/TEMPLATE_SPEC.md` §4 / §6 / §8，实操示例看 `AI_TEMPLATE_PACKAGER.md`。

## 旧模型 → 新模型（照这张表对）

| 旧 | 新 |
|---|---|
| `manifest.video.assetId`（原片） | 一个 `source=asset, binding=fixed` 的 `video` 槽位（`position=0`，key 用 `source`） |
| `manifest.packs[]`（素材包，多图 + 逐图用途） | 先在资产库封成 `kind=pack`（成员带 `position` / `role` / `description`），模板用**一个** `source=pack, binding=fixed` 槽位引用它 |
| `manifest.functions[]`（一功能一卡片） | **每个 function 拆成一条 spec**（key 用 `manifestKey:functionKey`） |
| `manifest.functions[].prompt` | `prompt.editable`（旧模型整段只读，迁过来放 editable 才可维护） |
| `mediaOrder` 里第 2 张起 = `{{Mixed 2}}` | `{{slot:views.1}}`（**逐个翻**，别用 `{{slot:views*}}` —— 整组会把「以某一张为准」改成「全都要」） |
| `scopeOptions` / `wardrobeMode` | `spec.attributes`（`scope` / `wardrobe`） |
| 发布 | `POST /template-specs/{key}/publish`，回执含 `version` 与 `tokensPreview` |

## 三个仍然有效的老约定

1. **顺序即契约**：槽位 `position` 决定 `{{Mixed N}}`，拖一次缩略图就是改编号。
2. **同一对象的多个视图不要摊成多个槽位**：结构归资产库封装，模板只引用 id ——
   否则库改了成员，模板还按旧的算。
3. **发布后不可改**：改模板 = 换版本；历史任务永远看到发布那一刻的原文。

## 素材口径

manifest 时代的一条仍然成立：**只写 asset id**。先上传（`/library/source-videos`、
`/library/assets/upload`），多个视图用 `/library/packs` 封成 `kind=pack`。
模板本身不碰字节 —— 落地发生在 `POST /tasks` 的 `generic_inputs.materialize`。
