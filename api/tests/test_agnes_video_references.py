"""Agnes 视频的参考素材：内联 data URI、边长守卫、`<Picture N>` 指代。

## 为什么单独一个文件

参考素材这条能力有三块容易悄悄退化的东西，钉在同一个文件里才看得见：

1. **形态**：上游只收公网 URL 或 `data:` URI。我们的存储在内网、给不出公网
   地址，所以**只有内联这一条路** —— 少了它，参考素材就传不出去（而界面
   上还画着上传框，用户只会看到一句「上传了但没用」）。
2. **守卫**：图片单边必须在 256-5760 像素（上游原话是
   `input image side length must be between 256 and 5760 pixels`）。
   受理期按资产库里的宽高判一次、提交前按真实字节再判一次；少一道，用户
   拿到的是一句英文报错，而不是「换一张尺寸合适的图」。
3. **指代**：提示词里的 `{{Mixed N}}` 要翻成上游认得的 `<Picture N>`，
   编号**按类型各自从 1 开始**，与 `images` / `audios` 两个数组一一对应。
"""

from __future__ import annotations

import base64
import io
import json
import wave
from typing import Any

import httpx
import pytest
from PIL import Image

from core.exceptions import BadRequest
from infra.ai.base import Capability, GenerationRequest
from modules.model_driver.drivers.agnes_video import MODEL_ID, AgnesVideoDriver

_BASE = "https://agnes.example"


def _driver(handler: Any, *, api_key: str = "sk-test-key") -> AgnesVideoDriver:
    from modules.model_driver.drivers.agnes_video import AgnesVideoSettings

    settings = AgnesVideoSettings(api_key=api_key, base_url=_BASE, timeout_sec=5.0)
    return AgnesVideoDriver(settings=settings, transport=httpx.MockTransport(handler))


def _request(**overrides: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "capability": Capability.VIDEO,
        "model": MODEL_ID,
        "prompt": "雨后的城市街道，霓虹倒影",
        "params": {"durationSec": 5, "aspectRatio": "16:9"},
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _no_network(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError(f"这一步不该发出请求：{request.url}")


def _png_data_uri(size: tuple[int, int] = (512, 512), color: str = "teal") -> str:
    """一张真实 PNG 的内联形态（尺寸可控，用来打边长边界）。

    颜色可换：驱动对参考素材**去重**，两张一模一样的图算一张 —— 不换颜色
    就造不出「多张参考图」。
    """
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _wav_data_uri(marker: int = 0) -> str:
    """一秒 WAV 的内联形态（上游的 `audios` 数组收它）。

    `marker` 换内容用：驱动对参考素材去重，一模一样的两段算一段。
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(bytes([marker % 256, 0]) * 8000)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:audio/wav;base64,{payload}"


# ------------------------------------------------------------------ 形态
async def test_data_uri_reference_switches_to_reference_mode() -> None:
    """内联 data URI 也要走参考模式 —— 上游受理这种形态（2026-11-22 实测出片）。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    uri = _png_data_uri()
    await _driver(handler).submit(_request(reference_urls=(uri,)))

    assert seen["payload"]["mode"] == "reference"
    assert seen["payload"]["images"] == [uri]
    assert "audios" not in seen["payload"]


async def test_audio_reference_goes_to_the_audios_array() -> None:
    """图片与音频分两个数组收：混在一个数组里，提示词的指代就对不上号。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    uri = _wav_data_uri()
    await _driver(handler).submit(_request(reference_urls=(uri,)))

    assert seen["payload"]["mode"] == "reference"
    assert seen["payload"]["audios"] == [uri]
    assert "images" not in seen["payload"]


async def test_object_key_is_rejected_before_request() -> None:
    """存储 key 不是地址：交上去只会在提交后失败。"""
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(reference_urls=("library/assets/abc.png",))
        )

    assert "公网" in str(exc.value)


# -------------------------------------------------------------- 边长守卫
async def test_image_below_the_minimum_side_is_rejected() -> None:
    """实测：64x64 被上游拒（side length must be between 256 and 5760）。"""
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(reference_urls=(_png_data_uri((64, 64)),))
        )

    assert "边长" in str(exc.value)


async def test_image_above_the_maximum_side_is_rejected() -> None:
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(reference_urls=(_png_data_uri((5800, 300)),))
        )

    assert "边长" in str(exc.value)


async def test_image_at_the_lower_boundary_is_accepted() -> None:
    """边界值必须放行：把 256 也拒掉，用户会以为「怎么 256 还不行」。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    uri = _png_data_uri((256, 256))
    await _driver(handler).submit(_request(reference_urls=(uri,)))

    assert seen["payload"]["images"] == [uri]


# ------------------------------------------------------------------ 张数
async def test_more_than_five_images_is_rejected() -> None:
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(
                reference_urls=tuple(
                    _png_data_uri(color=f"#{index:06x}") for index in range(6)
                )
            )
        )

    assert "5 张" in str(exc.value)


async def test_more_than_three_audios_is_rejected() -> None:
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(
                reference_urls=tuple(_wav_data_uri(marker=index) for index in range(4))
            )
        )

    assert "3 段" in str(exc.value)


# ------------------------------------------------------------------ 指代
async def test_picture_reference_is_translated_for_the_prompt() -> None:
    """`{{Mixed 1}}` → `<Picture 1>`：上游只认尖括号标签。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    await _driver(handler).submit(
        _request(prompt="让 {{Mixed 1}} 里的角色动起来", reference_urls=(_png_data_uri(),))
    )

    assert seen["payload"]["prompt"] == "让 <Picture 1> 里的角色动起来"


async def test_picture_and_audio_are_numbered_per_type() -> None:
    """图片与音频各自从 1 开始：说「第 2 张图」指的是图片数组的第 2 个。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    await _driver(handler).submit(
        _request(
            prompt="{{Mixed 1}} 与 {{Mixed 2}} 与 {{Mixed 3}}",
            reference_urls=(
                _png_data_uri(color="red"),
                _png_data_uri(color="blue"),
                _wav_data_uri(marker=7),
            ),
        )
    )

    assert seen["payload"]["prompt"] == "<Picture 1> 与 <Picture 2> 与 <Audio 1>"


# -------------------------------------------------------------- 目录声明
def test_declared_reference_capacity_matches_upstream() -> None:
    """目录里声明的张数与类型必须和上游一致：写宽了是「选了才被拒」。"""
    spec = AgnesVideoDriver.models[0]

    assert spec.max_references == 8, "上游 images 最多 5 张、audios 最多 3 段"
    assert spec.reference_kinds == ("image", "audio"), "上游的 videos 数组传了直接 400"
