"""P1 验收：新模型与旧模板对同一组素材给出同一套编号（等价性守卫）。

旧 `video_templates` 把顺序存在 `media_order`：下标 +1 就是 `{{Mixed N}}`，
每项自带 role 与 asset id。新 spec 把顺序存在槽位 `position`，编号由
`compile_spec` 现场算。同一批素材、同一顺序，两者的「编号 → 素材」必须逐项相等
—— 否则把老模板迁到新模型会**悄悄换掉用户熟悉的编号**：页面上提示词还是
`{{Mixed 2}}`，指向的却已经是另一张图，而这种错不会报错，只会生成不对。

这条用例是 P3 迁移脚本的验收依据：迁移只要让两种构造在本用例下给出同一结果，
就不会改语义。

**为什么三个视觉槽各自一张**：旧模板的 `media_order` 把一组三视图记成三个下标，
一槽一张后对应的就是三个各填一张的槽位。编号仍由 position 算出，与旧链路的
"下标 +1" 逐项相等。这里直接构造 core 对象调 `compile_spec` 验证，不依赖写入 API。
"""

from __future__ import annotations

from core.template_spec import FilledAsset, FilledSlot, InputSlot, TemplateSpec, compile_spec

MODEL = "libtv-wan3-replacement"


def _legacy(video: str, images: list[str]) -> list[tuple[str, str | None, str]]:
    """旧模板 `media_order` 的**冻结快照**：`[(token, asset_id, role)]`。

    这批常量来自 `cb9122d` 之前的 `video_templates.media_order`（旧链路已下线，
    表也删了，所以语义只能以常量的形式留在这里）。它的用途只有一个：给新模型
    一个不随实现漂移的对照物 —— 同一批素材、同一顺序，编号 → 素材必须逐项相等，
    否则迁移会**悄悄换掉用户熟悉的编号**：提示词里还是 `{{Mixed 2}}`，指向的却
    已经是另一张图，这种错不报错，只生成错。
    """
    ordered = [
        ("{{Mixed 1}}", video, "source_video"),
        ("{{Mixed 2}}", images[0], "front"),
        ("{{Mixed 3}}", images[1], "multiview"),
        ("{{Mixed 4}}", images[2], "detail"),
    ]
    return ordered


def test_compile_reproduces_legacy_media_order() -> None:
    """`compile` 的编号与素材序列必须和旧 `media_order` 一致。

    一槽一张后，旧模板里"一个多值槽放三张"改成"三个各一张的槽位"，编号
    1/2/3/4 仍与旧 `media_order` 逐项相等 —— 这正是迁移要保证的东西。
    """
    video = "v-source"
    images = ["i-front", "i-multiview", "i-detail"]
    legacy = _legacy(video, images)

    spec = TemplateSpec(
        key="equiv",
        name="等价性规格",
        model=MODEL,
        slots=(
            InputSlot(
                key="source", type="video", binding="fixed", asset_id=video, position=0,
                label="原片",
            ),
            InputSlot(
                key="front", type="image", binding="user", position=1, label="正面",
            ),
            InputSlot(
                key="multiview", type="image", binding="user", position=2, label="三视图",
            ),
            InputSlot(
                key="detail", type="image", binding="user", position=3, label="细节",
            ),
        ),
        prompt=(
            "把 {{slot:source}} 换成 {{slot:front}}、"
            "{{slot:multiview}}、{{slot:detail}}"
        ),
    )
    fills = {
        "source": FilledSlot(slot_key="source", assets=(FilledAsset(video, "video"),)),
        "front": FilledSlot(slot_key="front", assets=(FilledAsset(images[0], "image"),)),
        "multiview": FilledSlot(
            slot_key="multiview", assets=(FilledAsset(images[1], "image"),)
        ),
        "detail": FilledSlot(slot_key="detail", assets=(FilledAsset(images[2], "image"),)),
    }
    compiled = compile_spec(spec, fills)

    # 旧：编号就是 `media_order` 的下标 +1；新：编号由 position + 每槽一张算。
    assert list(compiled.reference_ids) == [asset for _token, asset, _role in legacy]
    assert compiled.prompt == "把 {{Mixed 1}} 换成 {{Mixed 2}}、{{Mixed 3}}、{{Mixed 4}}"
    assert compiled.slot_tokens["source"] == ("{{Mixed 1}}",)
    assert [compiled.slot_tokens[key] for key in ("front", "multiview", "detail")] == [
        ("{{Mixed 2}}",), ("{{Mixed 3}}",), ("{{Mixed 4}}",)
    ]
