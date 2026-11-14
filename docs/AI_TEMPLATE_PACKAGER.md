# 通过 AI 会话封装模板（已迁移，入口见 `TEMPLATE_SPEC.md`）

> **2026-11-14：本文描述的 `/template-manifests` 三步接口已随替换链下线。**
> 封装入口现在是 `POST /api/v1/template-specs`（草稿）→ `/{key}/publish`（发布），
> 契约与字段含义看 `docs/TEMPLATE_SPEC.md` §4（槽位词汇）/ §6（编号与编译）/ §8（接口）。

## 为什么变了

旧的 manifest 是「一份配置 = 原片 + 若干素材包 + 若干功能」，发布时投影成
`video_templates` 行给替换工作台用。工作台下线之后这套投影没有读者了，而它表达不了
「纯提示词 / 多图 / 图+视频 / 加音频」这些组合。新的模型把「一份模板 = 槽位 + 两段提示词
+ 模型」直接写进 `template_specs`，发布快照就是运行侧唯一事实。

## 现在怎么封装

```
① 素材先进资产库（模板里只存 asset id，不存本地路径）
   POST /library/source-videos?filename=clip.mp4          # 视频原片
   POST /library/assets/upload?filename=front.png&kind=image
   （多视图属于同一对象时先封成「资产库封装」kind=pack，模板只引用它的 id）

② 写一份规格
   POST /template-specs
   {
     "key": "product-swap", "name": "产品替换", "model": "libtv-wan3-replacement",
     "slots": [
       {"key": "source", "type": "video", "binding": "fixed", "assetId": "<原片 id>", "position": 0},
       {"key": "views", "type": "image", "source": "pack", "binding": "fixed",
        "packId": "<封装 id>", "position": 1}
     ],
     "prompt": {"locked": "", "editable": "把 {{slot:source}} 里的人物换成 {{slot:views}} 的同款产品"}
   }

③ 发布并自检
   POST /template-specs/{key}/publish     # 冻结版本，回执含 tokensPreview
   POST /template-specs/{key}/compile     # 用一组真实素材看展开后的编号
```

要点：

- **只写 `{{slot:key}}`**，别手写 `{{Mixed N}}` —— 编号由 `compile` 按 `position` 与实际
  填充量算，写死了少填一张就全体错位（`compile` 会直接 400）。
- **发布即冻结**：改模板 = 新版本，`template_spec_versions` 只插入不更新。
- **执行上限 2000 字**：`compile` 产出要交给 `POST /tasks`，超过会 422，所以在发布与
  编译两处提前拦（编写上限是 20000）。
- 素材校验一律经 `core.ports.library_lookup`，模板不取字节、不查元数据 —— 字节只由
  task 的 `generic_inputs.materialize` 落地。
