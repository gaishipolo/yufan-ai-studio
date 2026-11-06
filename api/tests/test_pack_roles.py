"""封装角色：旧包自动补用途、保存不静默改角色、发布前必须补齐必备用途与范围。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from PIL import Image
from pydantic import TypeAdapter
from tests_archive_helpers import archive

from core.exceptions import BadRequest
from core.media import MediaCategory
from core.pack_roles import BASE_ROLE, PACK_ROLES, PACK_SCOPES
from infra.archive.local import get_archive_settings
from modules.template.pack_service import PackService
from modules.template.replacement_repository import ReplacementRepository
from modules.template.replacement_schema import PackDraftIn

Kind = Literal["product", "character", "scene"]


def _register(tmp_path: Path, owner: str, kind: Kind, count: int) -> list[str]:
    category = TypeAdapter(MediaCategory).validate_python(kind)
    ids: list[str] = []
    for index in range(count):
        source = tmp_path / f"ref-{index}.png"
        Image.new("RGB", (16, 16), "blue").save(source)
        ids.append(archive.register(source, f"{kind}-{index}.png", category, owner)["id"])
    return ids


def _payload(kind: Kind, ids: list[str], **extra: Any) -> PackDraftIn:
    values: dict[str, Any] = {
        "requestId": uuid4(),
        "kind": kind,
        "name": "测试对象",
        "referenceIds": ids,
        "coverReferenceId": ids[0] if ids else None,
    }
    values.update(extra)
    return PackDraftIn.model_validate(values)


def test_legacy_pack_gets_base_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    repo = ReplacementRepository()
    repo.storage.write(
        tmp_path / "catalog/packs/pack.json",
        {
            "id": "pack",
            "ownerId": "owner",
            "kind": "product",
            "name": "旧包",
            "referenceIds": ["a", "b", "c"],
        },
    )
    pack = repo.packs("owner")[0]
    assert [binding.role for binding in pack.bindings] == ["front", "front", "front"]
    assert pack.required_roles == ["front"]


def test_save_keeps_roles_and_reports_gaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "product", 2)
    saved = PackService().save(
        _payload(
            "product",
            ids,
            bindings=[{"role": "front"}, {"role": "multiview"}],
            requiredRoles=["detail"],
        ),
        "owner",
    )
    assert [binding.role for binding in saved.bindings] == ["front", "multiview"]
    assert saved.gaps == ["缺少产品细节图", "产品替换范围"]


def test_save_rejects_unknown_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "character", 1)
    with pytest.raises(BadRequest, match="图片用途无效"):
        PackService().save(_payload("character", ids, bindings=[{"role": "multiview"}]), "owner")


def test_save_rejects_role_count_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "character", 2)
    with pytest.raises(BadRequest, match="一一对应"):
        PackService().save(_payload("character", ids, bindings=[{"role": "front"}]), "owner")


def test_publish_requires_required_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "product", 2)
    service = PackService()
    saved = service.save(
        _payload(
            "product",
            ids,
            bindings=[{"role": "front"}, {"role": "detail"}],
            requiredRoles=["multiview"],
            supportedScopes=["worn"],
        ),
        "owner",
    )
    with pytest.raises(BadRequest) as failure:
        service.publish(saved.id, "owner", saved.revision)
    assert "多视结构图" in str(failure.value)


def test_publish_requires_product_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "product", 1)
    service = PackService()
    saved = service.save(_payload("product", ids), "owner")
    with pytest.raises(BadRequest, match="产品替换范围"):
        service.publish(saved.id, "owner", saved.revision)


def test_publish_complete_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "product", 3)
    service = PackService()
    saved = service.save(
        _payload(
            "product",
            ids,
            bindings=[{"role": "front"}, {"role": "multiview"}, {"role": "detail"}],
            requiredRoles=["multiview", "detail"],
            supportedScopes=["worn", "held"],
        ),
        "owner",
    )
    assert saved.gaps == []
    published = service.publish(saved.id, "owner", saved.revision)
    assert published.status == "published"
    assert published.version == 1
    assert [pack.id for pack in service.available()] == [saved.id]


def test_frontend_role_table_matches_backend() -> None:
    """前端为了首屏渲染保留了一份用途表；两边不一致就是提示词和 UI 各说一套。"""
    path = Path(__file__).parents[2] / "studio/src/entities/template/model/packRoles.ts"
    if not path.is_file():
        pytest.skip("前端副本不在当前检出中")
    ts = path.read_text(encoding="utf-8")
    for kind, roles in PACK_ROLES.items():
        assert kind in ts
        for role, label in roles.items():
            assert f"{role}: '{label}'" in ts, f"{kind}.{role} 的中文名与后端不一致"
    for scope, label in PACK_SCOPES.items():
        assert f"{scope}: '{label}'" in ts, f"范围 {scope} 的中文名与后端不一致"
    for kind, role in BASE_ROLE.items():
        assert f"{kind}: '{role}'" in ts, f"{kind} 的基准用途与后端不一致"


def test_edit_draft_uses_same_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "character", 1)
    service = PackService()
    saved = service.save(_payload("character", ids), "owner")
    edited = service.save(
        _payload(
            "character",
            ids,
            requestId=UUID(saved.id),
            expectedRevision=saved.revision,
            name="改名后",
        ),
        "owner",
    )
    assert edited.id == saved.id
    assert edited.name == "改名后"
    assert edited.revision == saved.revision + 1


def test_pack_usage_keeps_description_without_double_period() -> None:
    """封装里写的补充说明要进提示词，但收尾句号不能和编译器的句号叠成「。。」。"""
    from modules.template.replacement_schema import ReplacementPack
    from modules.template.replacement_service import _pack_usage

    pack = ReplacementPack(
        id="p",
        kind="product",
        name="3030",
        referenceIds=["a", "b"],
        bindings=[
            {"role": "front", "description": "完整产品主图，确定同款同色外观。"},
            {"role": "multiview", "description": "同一产品的多视拼图。"},
        ],
    )
    usage = _pack_usage(pack, "product")
    assert usage["a"].startswith("以产品主图确定同款同色")
    assert "完整产品主图，确定同款同色外观" in usage["a"]
    assert not usage["a"].endswith(("。", "；"))
    assert not usage["b"].endswith(("。", "；"))


def test_character_prompt_includes_pack_bindings() -> None:
    """人物模板少了这段，封装里写的"同一人多视拼图、不复制排版"就整段丢失。"""
    path = (
        Path(__file__).parents[1]
        / "src/modules/template/replacement_runtime/prompts/character.template.zh.txt"
    )
    assert "{{reference_bindings}}" in path.read_text(encoding="utf-8")


def test_scope_check_rejects_outside_pack() -> None:
    """运营不能临时扩大替换范围：只接受包里审核过的位置。"""
    from modules.template.replacement_schema import PrepareReplacement, ReplacementPack
    from modules.template.replacement_service import _check_scope

    pack = ReplacementPack(
        id="p",
        kind="product",
        name="3030",
        referenceIds=["a"],
        supportedScopes=["worn", "held"],
    )
    ok = PrepareReplacement(
        request_id=uuid4(), kind="product", sourceId="s", replacementScope="人物上身穿着"
    )
    _check_scope(pack, ok)
    beyond = ok.model_copy(update={"replacement_scope": "屏幕商品"})
    with pytest.raises(BadRequest, match="替换范围超出"):
        _check_scope(pack, beyond)


def test_repeat_save_with_same_revision_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一次提交因超时重试时，必须返回同一份草稿，而不是「草稿已变化」。"""
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    ids = _register(tmp_path, "owner", "product", 2)
    payload = _payload(
        "product",
        ids,
        requestId=uuid4(),
        bindings=[
            {"role": "front", "description": "主图"},
            {"role": "multiview", "description": "多视"},
        ],
    )
    first = PackService().save(payload, "owner")
    again = PackService().save(payload, "owner")
    assert again.id == first.id
    assert again.revision == first.revision


def test_product_prepare_requires_explicit_scope() -> None:
    """默认范围会悄悄承诺包外位置：必须由调用方按包内审核结果显式给出。"""
    from modules.template.replacement_schema import PrepareReplacement, ReplacementPack
    from modules.template.replacement_service import _check_scope

    pack = ReplacementPack(
        id="p", kind="product", name="3030", referenceIds=["a"], supportedScopes=["worn"]
    )
    blank = PrepareReplacement(request_id=uuid4(), kind="product", sourceId="s")
    assert blank.replacement_scope == ""
    with pytest.raises(BadRequest, match="请选择产品替换位置"):
        _check_scope(pack, blank)
