"""改固定提示词：只能原地改，`{{Mixed N}}` 引用按新原文重算。

四条底线：
1. 新原文点名了更靠后的槽位时，`bindings` 向后扩展、`inPrompt` 跟着翻，
   没被引用的旧槽位保留但降为「仅插入接口」；
2. 越界引用、只引用图片不引用原片、一个图片都不点名，都要当场拒绝；
3. 不再引用 `{{ReplacementScope}}` 时清掉 `scopeOptions`——
   固定提示词是逐字提交的，留着范围选择器只会点了没反应；
4. 提示词与摘要一起换，`updatedAt` 前移，其余字段（素材包、档位、媒体 asset）不动。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from test_video_template_binding import NOW, compile_spec, record

from core.exceptions import BadRequest, NotFound
from modules.template import video_template_store as store


def catalog(path: Path, **overrides: object) -> None:
    path.write_text(
        json.dumps({"version": 1, "items": [record(**overrides)]}, ensure_ascii=False),
        encoding="utf-8",
    )


def bound(**rules: object) -> dict:
    base = {
        "promptMode": "fixed_no_compilation",
        "referenceBindings": {"{{Mixed 1}}": "source_video", "{{Mixed 2}}": "multiview"},
    }
    base.update(rules)
    return base


@pytest.mark.parametrize("prompt", [
    "{{ScenePrompt}}\n保留原片人物与动作。",
    "编辑 {{Mixed 1}}，{{ScenePrompt}}，保持人物不变。",
])
def test_update_scene_prompt_uses_scene_validation(tmp_path, monkeypatch, prompt):
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(store, "CATALOG_PATH", path)
    catalog(path, defaultKind="scene", packId=None,
            prompt="{{ScenePrompt}}\n保留人物。",
            mediaOrder=[{"token": "{{Mixed 1}}", "role": "source_video", "type": "video"}],
            rules={"inputMode": "video_scene_text", "scenePromptDefault": "海边花园"})
    updated = store.update_prompt("template-1", prompt)
    assert updated.prompt == prompt
    assert updated.prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()
    assert updated.rules["scenePromptDefault"] == "海边花园"
    assert len(updated.media_order) == 1
    assert updated.media_order[0].in_prompt == ("{{Mixed 1}}" in prompt)
    with pytest.raises(BadRequest):
        store.update_prompt("template-1", "编辑 {{Mixed 1}}，缺少场景参数。")
    assert store.get_out("template-1").prompt == prompt


def test_prompt_change_extends_bindings_and_flips_in_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", rules=bound())
    new = "编辑 {{Mixed 1}}，把产品换成 {{Mixed 2}} 与 {{Mixed 3}} 中的目标款。"
    updated = store.update_prompt("template-1", new)

    assert updated.prompt == new
    assert updated.prompt_sha256 == hashlib.sha256(new.encode("utf-8")).hexdigest()
    assert updated.rules["referenceBindings"] == {
        "{{Mixed 1}}": "source_video",
        "{{Mixed 2}}": "multiview",
        "{{Mixed 3}}": "multiview",
    }
    assert updated.rules["promptTokenAllowlist"] == [
        "{{Mixed 1}}",
        "{{Mixed 2}}",
        "{{Mixed 3}}",
    ]
    # 第 3 位本来没被引用，现在点名了；第 4 位仍未引用
    assert [item.in_prompt for item in updated.media_order] == [True, True, True, False]
    assert updated.pack_id == record()["packId"]
    assert updated.generation == record()["generation"]
    assert updated.updated_at > datetime.fromisoformat(NOW.replace("Z", "+00:00"))


def test_unreferenced_slot_keeps_bindings_but_drops_from_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(
        tmp_path / "catalog.json",
        rules=bound(
            referenceBindings={
                "{{Mixed 1}}": "source_video",
                "{{Mixed 2}}": "multiview",
                "{{Mixed 3}}": "multiview",
            }
        ),
    )
    updated = store.update_prompt("template-1", "只改 {{Mixed 1}}，参考 {{Mixed 2}}。")
    # 不缩短：{{Mixed 3}} 仍会完整插入接口，只是不再被点名
    assert updated.rules["referenceBindings"] == {
        "{{Mixed 1}}": "source_video",
        "{{Mixed 2}}": "multiview",
        "{{Mixed 3}}": "multiview",
    }
    assert [item.in_prompt for item in updated.media_order] == [True, True, False, False]


def test_prompt_change_clears_scope_options_once_placeholder_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    path = tmp_path / "catalog.json"
    catalog(
        path,
        rules=bound(scopeOptions=["手持展示产品", "盒内可见产品"]),
        prompt="编辑 {{Mixed 1}}，范围 {{ReplacementScope}}，参考 {{Mixed 2}}。",
    )
    kept = store.update_prompt(
        "template-1", "编辑 {{Mixed 1}}，范围 {{ReplacementScope}}。参考 {{Mixed 2}}。"
    )
    assert kept.rules["scopeOptions"] == ["手持展示产品", "盒内可见产品"]

    dropped = store.update_prompt("template-1", "编辑 {{Mixed 1}}，参考 {{Mixed 2}}。")
    assert "scopeOptions" not in dropped.rules


def test_prompt_change_rejects_bad_references(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", rules=bound())
    with pytest.raises(BadRequest, match="超出模板媒体数量"):
        store.update_prompt("template-1", "编辑 {{Mixed 1}} 与 {{Mixed 9}}。")
    with pytest.raises(BadRequest, match="必须引用原片槽位"):
        store.update_prompt("template-1", "只改 {{Mixed 2}}。")
    with pytest.raises(BadRequest, match="至少点名一张参考图"):
        store.update_prompt("template-1", "只改 {{Mixed 1}}。")
    # 模板没声明可选范围，却在原文里引用范围参数：运行时会拿到空值，当场拒绝
    with pytest.raises(BadRequest, match="没有声明可选范围"):
        store.update_prompt(
            "template-1", "编辑 {{Mixed 1}}，范围 {{ReplacementScope}}，参考 {{Mixed 2}}。"
        )
    with pytest.raises(BadRequest, match="非法占位符"):
        store.update_prompt("template-1", "编辑 {{Mixed 1}} 与 {{Product}}。")
    with pytest.raises(NotFound):
        store.update_prompt("missing", "编辑 {{Mixed 1}}，参考 {{Mixed 2}}。")


def test_prompt_is_preserved_verbatim_before_hashing(tmp_path, monkeypatch):
    """画布测试通过的原文，包括空格与换行，保存和读取均不得改写。"""
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", rules=bound())
    raw = "  编辑 {{Mixed 1}}，参考 {{Mixed 2}}。\n\n"
    updated = store.update_prompt("template-1", raw)
    expected = raw
    assert updated.prompt == expected
    assert updated.prompt_sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    stored = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))["items"][0]
    assert stored["prompt"] == expected
    assert stored["promptSha256"] == updated.prompt_sha256
    assert store.get_out("template-1").prompt == raw


def test_prompt_request_preserves_whitespace():
    from modules.template.schema import ReplacementRunOut
    from modules.template.video_template_schema import VideoTemplateDraftIn, VideoTemplatePromptIn

    raw = " \n编辑 {{Mixed 1}}，参考 {{Mixed 2}}。\n\n "
    assert VideoTemplatePromptIn(prompt=raw).prompt == raw
    assert VideoTemplateDraftIn(name="测试", prompt=raw).prompt == raw
    run = ReplacementRunOut(
        id="run", kind="product", target="测试产品", status="prepared", created_at=NOW, prompt=raw
    )
    assert run.prompt == raw


def test_whitespace_only_update_does_not_change_template(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", rules=bound())
    previous = store.get_out("template-1")
    with pytest.raises(BadRequest, match="不能为空"):
        store.update_prompt("template-1", " \n\t ")
    assert store.get_out("template-1") == previous


def test_saved_prompt_compiles_verbatim_without_image_descriptions(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CATALOG_PATH", tmp_path / "catalog.json")
    catalog(tmp_path / "catalog.json", rules=bound())
    raw = " \n编辑 {{Mixed 1}}，参考 {{Mixed 2}} 和 {{Mixed 3}}。\n\n "
    store.update_prompt("template-1", raw)
    saved = store.get_out("template-1")
    usage = "图片说明只供内部存档，不应自动加入固定提示词"
    result = compile_spec(saved.prompt, usage=usage)
    assert result.returncode == 0, result.stderr
    compiled = json.loads(result.stdout)
    assert compiled["prompt"] == raw
    assert usage not in compiled["prompt"]
    assert compiled["bindings"][1]["usage"] == usage
