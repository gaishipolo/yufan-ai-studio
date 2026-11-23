"""LibTV 多模型接入（Wan 3.0 + Seedance 2.5 VIP / 2.5 Flash / 2.0 VIP）契约测试。"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from core.exceptions import BadRequest
from infra.ai.base import Capability, GenerationRequest
from infra.archive import reset_archive_storage
from infra.archive.local import get_archive_settings
from modules.model_driver.generic_inputs import materialize
from modules.model_driver.libtv.models import LIBTV_PROFILES, resolve_libtv_profile


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(get_archive_settings(), "root", str(root))
    monkeypatch.setattr(settings, "archive_driver", "local")
    monkeypatch.setattr(settings, "task_runs_root", "")
    reset_archive_storage()
    try:
        yield root
    finally:
        reset_archive_storage()

BRIDGE = Path(__file__).resolve().parents[1] / "src/modules/model_driver/libtv/runtime/bridge.mjs"
SUBMIT = (
    Path(__file__).resolve().parents[1]
    / "src/modules/model_driver/libtv/runtime/vendor/libtv/workbench/submit.mjs"
)


def test_catalog_exposes_all_four_libtv_models_with_480p_default_for_seedance(
    client: TestClient, api: str
) -> None:
    response = client.get(f"{api}/models", params={"capability": "video"})
    assert response.status_code == 200, response.text
    by_id = {item["id"]: item for item in response.json()["data"]["items"]}

    assert "libtv-wan3-replacement" not in by_id, "旧 id 应只保留为解析别名，不再重复挂在公开目录里"
    for expected in (
        "libtv-wan3.0",
        "libtv-seedance-2.5",
        "libtv-seedance-2.5-flash",
        "libtv-seedance-2.0",
    ):
        assert expected in by_id

    wan = by_id["libtv-wan3.0"]
    assert wan["paramSchema"]["resolution"]["default"] == "720P"
    assert wan["paramSchema"]["resolution"]["options"] == ["480P", "720P", "1080P"]
    assert wan["paramSchema"]["durationSec"]["min"] == 2
    assert wan["paramSchema"]["durationSec"]["max"] == 30

    s25 = by_id["libtv-seedance-2.5"]
    assert s25["paramSchema"]["resolution"]["default"] == "480p"
    assert s25["paramSchema"]["resolution"]["options"] == ["480p", "720p", "1080p", "4k"]
    assert s25["paramSchema"]["durationSec"]["min"] == 4
    assert s25["paramSchema"]["durationSec"]["max"] == 30

    flash = by_id["libtv-seedance-2.5-flash"]
    assert flash["paramSchema"]["resolution"]["default"] == "480p"
    assert flash["paramSchema"]["resolution"]["options"] == ["480p", "720p"]
    assert flash["paramSchema"]["durationSec"]["min"] == 4
    assert flash["paramSchema"]["durationSec"]["max"] == 30

    s20 = by_id["libtv-seedance-2.0"]
    assert s20["paramSchema"]["resolution"]["default"] == "480p"
    assert s20["paramSchema"]["resolution"]["options"] == ["480p", "720p", "1080p", "4k"]
    assert s20["paramSchema"]["durationSec"]["min"] == 4
    assert s20["paramSchema"]["durationSec"]["max"] == 15


def test_legacy_libtv_id_and_label_still_resolve_to_wan3(client: TestClient, api: str) -> None:
    """历史任务与外部调用方传 `libtv-wan3-replacement` 仍能落到 `libtv-wan3.0`。"""
    resp = client.post(
        f"{api}/tasks",
        json={"prompt": "兼容旧模型 id", "model": "libtv-wan3-replacement", "durationSec": 5},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["modelId"] == "libtv-wan3.0"


def test_seedance_task_creation_respects_resolution_and_duration_bounds(
    client: TestClient, api: str
) -> None:
    ok = client.post(
        f"{api}/tasks",
        json={"prompt": "Seedance 2.5 默认 480p", "model": "libtv-seedance-2.5", "durationSec": 6},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["data"]["modelId"] == "libtv-seedance-2.5"

    bad_flash_res = client.post(
        f"{api}/tasks",
        json={
            "prompt": "Flash 不支持 1080p",
            "model": "libtv-seedance-2.5-flash",
            "params": {"resolution": "1080p"},
        },
    )
    assert bad_flash_res.status_code == 400

    bad_s20_duration = client.post(
        f"{api}/tasks",
        json={"prompt": "2.0 上限 15s", "model": "libtv-seedance-2.0", "durationSec": 20},
    )
    assert bad_s20_duration.status_code == 400


def test_seedance_20_rejects_over_nine_images_at_acceptance_time(
    client: TestClient, api: str
) -> None:
    from library_support import upload_image

    asset_ids = [
        upload_image(client, api, {}, name=f"ref-{i}.png", color=f"#{i + 1:02x}8080")["id"]
        for i in range(10)
    ]
    rejected = client.post(
        f"{api}/tasks",
        json={
            "prompt": "超过 9 张图应在受理期直接 400",
            "model": "libtv-seedance-2.0",
            "durationSec": 5,
            "referenceLibraryIds": asset_ids,
        },
    )
    assert rejected.status_code == 400
    assert "9" in rejected.text


@pytest.mark.parametrize(
    ("model_id", "expected_resolution", "explicit_resolution"),
    [
        ("libtv-wan3.0", "720P", "1080p"),
        ("libtv-seedance-2.5", "480p", "720P"),
        ("libtv-seedance-2.5-flash", "480p", "720p"),
        ("libtv-seedance-2.0", "480p", "4K"),
    ],
)
def test_materialize_writes_model_default_and_normalized_resolution(
    archive_root: Path,
    model_id: str,
    expected_resolution: str,
    explicit_resolution: str,
) -> None:
    from infra.archive.runs import runs_root

    default_run = uuid.uuid4().hex
    asyncio.run(
        materialize(
            GenerationRequest(
                capability=Capability.VIDEO,
                model=model_id,
                prompt="测试默认分辨率",
                params={"durationSec": 5, "aspectRatio": "9:16"},
                request_id=default_run,
            )
        )
    )
    run_meta = json.loads((runs_root() / default_run / "run.json").read_text(encoding="utf-8"))
    assert run_meta["model"] == model_id
    assert run_meta["resolution"] == expected_resolution

    explicit_run = uuid.uuid4().hex
    asyncio.run(
        materialize(
            GenerationRequest(
                capability=Capability.VIDEO,
                model=model_id,
                prompt="测试显式分辨率大小写归一化",
                params={"durationSec": 5, "aspectRatio": "9:16", "resolution": explicit_resolution},
                request_id=explicit_run,
            )
        )
    )
    explicit_meta = json.loads(
        (runs_root() / explicit_run / "run.json").read_text(encoding="utf-8")
    )
    profile = resolve_libtv_profile(model_id)
    assert profile is not None
    assert explicit_meta["resolution"] in profile.resolutions
    assert explicit_meta["resolution"].lower() == explicit_resolution.lower()


def test_materialize_rejects_seedance_20_over_nine_images(archive_root: Path) -> None:
    refs = [
        {"id": f"img-{i}", "key": f"k/{i}.png", "kind": "image", "name": f"{i}.png"}
        for i in range(10)
    ]
    with pytest.raises(BadRequest, match="最多支持 9 张参考图"):
        asyncio.run(
            materialize(
                GenerationRequest(
                    capability=Capability.VIDEO,
                    model="libtv-seedance-2.0",
                    prompt="十张图应超限",
                    params={"durationSec": 5, "aspectRatio": "9:16", "referenceKeys": refs},
                    request_id=uuid.uuid4().hex,
                )
            )
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 验证桥接层脚本")
def test_node_bridge_builds_and_validates_all_four_models() -> None:
    script = f"""
    import {{
      resolveLibtvVideoModel,
      buildLibtvModelParams,
      validateLibtvRequest,
    }} from {json.dumps(BRIDGE.as_uri())};

    const cases = [
      ['libtv-wan3.0', 'wanx3.0', 'Wan', '720P', false],
      ['libtv-wan3-replacement', 'wanx3.0', 'Wan', '720P', false],
      ['libtv-seedance-2.5', 'star-video2.5', 'star-video2.5', '480p', true],
      ['libtv-seedance-2.5-flash', 'star-video2.5-flash', 'star-video2.5', '480p', true],
      ['libtv-seedance-2.0', 'star-video2', 'star-video2', '480p', true],
    ];
    for (const [catalogId, upstream, provider, defRes, isSeedance] of cases) {{
      const spec = resolveLibtvVideoModel(catalogId);
      if (spec.model !== upstream || spec.provider !== provider) {{
        throw Error(`Mismatch for ${{catalogId}}: ${{JSON.stringify(spec)}}`);
      }}
      const params = buildLibtvModelParams(spec, {{}}, {{}});
      if (params.resolution !== defRes) {{
        throw Error(`Default resolution mismatch for ${{catalogId}}: ${{params.resolution}}`);
      }}
      if (isSeedance) {{
        if ('extendPrompt' in params) throw Error('Seedance must not send extendPrompt');
        if (
          params.enablePortraitCheck !== 1 ||
          params.search_enabled !== 1 ||
          params.autoCompliance !== 1 ||
          params.portraitGridRetry !== 0
        ) {{
          throw Error(`Missing Seedance flags for ${{catalogId}}`);
        }}
      }} else {{
        if (params.extendPrompt !== 0 || 'search_enabled' in params) {{
          throw Error(`Unexpected Wan flags for ${{catalogId}}`);
        }}
      }}
      validateLibtvRequest({{
        provider: spec.provider,
        model: spec.model,
        taskType: 'video',
        params: {{
          prompt: 'ok',
          model: spec.model,
          modeType: 'mixed2video',
          count: 1,
          duration: 5,
          ...params,
          infiniteSwitch: 1,
        }},
      }}, {{infiniteSwitch: 1}});
    }}

    let flashRejected = false;
    try {{
      const flash = resolveLibtvVideoModel('libtv-seedance-2.5-flash');
      buildLibtvModelParams(flash, {{resolution: '1080p'}}, {{}});
    }} catch {{
      flashRejected = true;
    }}
    if (!flashRejected) throw Error('Flash must reject 1080p');

    console.log(JSON.stringify({{ok: true, submitPath: {json.dumps(str(SUBMIT))}}}));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-600:]
    assert json.loads(completed.stdout)["ok"] is True
    assert len(LIBTV_PROFILES) == 4


def test_vendor_submit_models_match_single_source_models_json() -> None:
    """`submit.mjs` 独立复制到工作区运行，故内联模型表；这里守住它与 `models.json` 同口径。"""
    import re

    source = SUBMIT.read_text(encoding="utf-8")
    match = re.search(r"const WORKBENCH_MODELS = (\{.*?\n\});", source, re.S)
    assert match is not None
    # 转成 JSON 解析（单引号键与字符串），校验四个模型的 provider / 分辨率 / 时长上下限一致
    raw_obj = match.group(1)
    parsed = subprocess.run(
        ["node", "--input-type=module", "-e", f"console.log(JSON.stringify(({raw_obj})))"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    workbench = json.loads(parsed.stdout)
    expected = {
        p.upstream_model: {
            "provider": p.upstream_provider,
            "resolutions": list(p.resolutions),
            "min": p.min_duration,
            "max": p.max_duration,
        }
        for p in LIBTV_PROFILES
    }
    assert workbench == expected
