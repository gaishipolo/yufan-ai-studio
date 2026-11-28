"""随机档模型改写服务（W2）的守卫。

全部用假 completer，不碰网络、不碰数据库。测的是：
- 可改写集合的五条判据；
- 请求里带了什么（标签、引用、长度、时限、动态预算）；
- 模型稿的严格解析与引用守恒；
- 与作者/使用者共用的 `apply_slot_text_overrides` 闸门；
- 上游 401/403/传输错误不会以 401/403/原始 httpx 异常漏出。
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from core.exceptions import BadRequest, Forbidden, RateLimited, Unauthorized, UpstreamError
from core.template_spec import InputSlot, TemplateSpec
from infra.llm import LlmRequest, LlmResponse, LlmUsage
from modules.template_pack import randomness
from modules.template_pack.randomness import rewritable_slot_keys, rewrite_slot_texts


def _slot(
    key: str,
    position: int,
    *,
    binding: str = "user",
    min_count: int = 0,
    empty_text: str = "",
    label: str = "",
    asset_id: str | None = None,
    ref_mode: str = "mixed",
    slot_type: str = "image",
) -> InputSlot:
    return InputSlot(
        key=key,
        type=slot_type,  # type: ignore[arg-type]
        binding=binding,  # type: ignore[arg-type]
        ref_mode=ref_mode,  # type: ignore[arg-type]
        min_count=min_count,
        max_count=1,
        label=label or key,
        asset_id=asset_id,
        empty_text=empty_text,
        position=position,
    )


def _spec(
    *,
    scene_text: str = "夜晚的城市街头，霓虹灯亮着",
    extra: tuple[InputSlot, ...] = (),
    prompt_tail: str = "",
) -> TemplateSpec:
    """1 固定（source）+ 1 必填（hero）+ 2 可选（scene/outfit）。"""
    return TemplateSpec(
        key="demo",
        name="演示",
        model="m",
        prompt=(
            "原片 {{Mixed 1}}，人物 {{Mixed 2}}，"
            "场景 {{Mixed 3}}，服装 {{Mixed 4}}"
            + prompt_tail
        ),
        slots=(
            _slot("source", 0, binding="fixed", min_count=1, asset_id="v-src", label="原片"),
            _slot("hero", 1, min_count=1, label="人物"),
            _slot("scene", 2, empty_text=scene_text, label="场景"),
            _slot("outfit", 3, empty_text="红色的长风衣", label="服装"),
            *extra,
        ),
    )


class FakeCompleter:
    """记录请求并返回预置响应/异常。"""

    def __init__(
        self,
        *,
        payload: dict | None = None,
        text: str | None = None,
        finish_reason: str = "stop",
        error: BaseException | None = None,
    ) -> None:
        self.payload = payload
        self.text = text
        self.finish_reason = finish_reason
        self.error = error
        self.requests: list[tuple[LlmRequest, float | None, float | None]] = []

    async def __call__(
        self,
        request: LlmRequest,
        *,
        timeout_sec: float | None = None,
        deadline: float | None = None,
    ) -> LlmResponse:
        self.requests.append((request, timeout_sec, deadline))
        if self.error is not None:
            raise self.error
        text = self.text if self.text is not None else json.dumps(self.payload, ensure_ascii=False)
        return LlmResponse(
            text=text,
            model="qwen-plus",
            finish_reason=self.finish_reason,
            usage=LlmUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            platform_model="llm/qwen-plus",
        )


def _payload(**texts: str) -> dict:
    return {"slots": [{"key": key, "text": text} for key, text in texts.items()]}


def _ok(scene: str = "雨后的城市街道，霓虹映在水面上", outfit: str = "一件酒红色长外套") -> dict:
    return _payload(scene=scene, outfit=outfit)


def test_可改写集合只含可选_有插入点_有兜底文案的槽位() -> None:
    spec = _spec(
        extra=(
            # 可选但提示词里没有插入点：改了也不会进提示词
            _slot("unused", 4, empty_text="这段是死数据", label="未引用"),
            # 可选、有插入点，但没有兜底文案：模型没有原文可改
            _slot("blank", 5, label="空文案"),
        ),
        # Mixed 6 指向 blank；unused 没有被引用
        prompt_tail="，另一段 {{Mixed 6}}",
    )
    assert rewritable_slot_keys(spec) == ("scene", "outfit")


@pytest.mark.asyncio
async def test_请求带标签_引用清单_长度上限_时限与动态预算() -> None:
    fake = FakeCompleter(
        payload=_ok(scene="雨后的街道，{{slot:hero}} 站在霓虹里")
    )
    spec = _spec(scene_text="站在{{slot:hero}}旁边，霓虹灯亮着")

    overrides = await rewrite_slot_texts(spec, complete=fake)

    assert overrides == {
        "scene": "雨后的街道，{{slot:hero}} 站在霓虹里",
        "outfit": "一件酒红色长外套",
    }
    request, timeout_sec, deadline = fake.requests[0]
    assert request.model == "llm/default"
    assert request.params["temperature"] == 1.0
    assert request.params["max_tokens"] == randomness.MIN_REWRITE_TOKENS

    system, user = request.messages
    assert system.role == "system" and "只输出 JSON" in system.content
    data = json.loads(user.content)
    by_key = {item["key"]: item for item in data["slots"]}
    assert set(by_key) == {"scene", "outfit"}
    assert by_key["scene"]["label"] == "场景"
    assert by_key["scene"]["text"] == "站在{{slot:hero}}旁边，霓虹灯亮着"
    assert by_key["scene"]["refs"] == ["{{slot:hero}}"]
    assert by_key["scene"]["maxChars"] >= len(by_key["scene"]["text"])
    # 必填 / 已固定才允许被引用；可选槽位不在清单里
    assert data["allowedRefs"] == ["{{slot:source}}", "{{slot:hero}}"]

    assert timeout_sec == randomness.RANDOM_CALL_TIMEOUT_SEC
    assert deadline is not None
    assert deadline - time.monotonic() <= randomness.RANDOM_TOTAL_BUDGET_SEC


@pytest.mark.asyncio
async def test_引用必须原样保留() -> None:
    fake = FakeCompleter(
        payload=_ok(scene="雨后的街道，{{slot:hero}} 站在霓虹里")
    )
    overrides = await rewrite_slot_texts(
        _spec(scene_text="站在{{slot:hero}}旁边，霓虹灯亮着"),
        complete=fake,
    )
    assert "{{slot:hero}}" in overrides["scene"]


@pytest.mark.asyncio
async def test_丢失原文引用会被拒() -> None:
    fake = FakeCompleter(payload=_ok(scene="雨后的街道，霓虹映在水面上"))
    with pytest.raises(BadRequest, match="引用与原文不一致"):
        await rewrite_slot_texts(
            _spec(scene_text="站在{{slot:hero}}旁边，霓虹灯亮着"),
            complete=fake,
        )


@pytest.mark.asyncio
async def test_新增引用会被拒() -> None:
    fake = FakeCompleter(payload=_ok(scene="雨后的街道，{{slot:hero}} 站在霓虹里"))
    with pytest.raises(BadRequest, match="引用与原文不一致"):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.asyncio
async def test_Mixed写法_修饰符与锚点直接拒() -> None:
    fake = FakeCompleter(payload=_ok(scene="雨后的 {{Mixed 2}} 街道"))
    with pytest.raises(BadRequest, match="不允许的写法"):
        await rewrite_slot_texts(_spec(), complete=fake)

    fake = FakeCompleter(payload=_ok(scene="雨后的 {{slot:hero*}} 街道"))
    with pytest.raises(BadRequest, match="不允许的写法"):
        await rewrite_slot_texts(_spec(), complete=fake)

    fake = FakeCompleter(payload=_ok(scene="⟦e:hero⟧雨后的街道⟦/e⟧"))
    with pytest.raises(BadRequest, match="不允许的写法"):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.asyncio
async def test_自引用与不存在的槽位走共享闸门() -> None:
    fake = FakeCompleter(payload=_ok(scene="雨后的 {{slot:scene}} 街道"))
    with pytest.raises(BadRequest, match="引用了它自己"):
        await rewrite_slot_texts(_spec(), complete=fake)

    fake = FakeCompleter(payload=_ok(scene="雨后的 {{slot:ghost}} 街道"))
    with pytest.raises(BadRequest, match="不存在的槽位"):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.parametrize(
    "text, message",
    [
        ("不是 JSON", "不是 JSON"),
        ("[]", "顶层不是对象"),
        ('{"slots": {}}', "缺少 slots 数组"),
        ('{"slots": [1]}', "slots 里有非对象"),
        ('{"slots": [{"key": "scene"}]}', "key/text 必须是字符串"),
        ('{"slots": [{"key": "scene", "text": "a"}, {"key": "scene", "text": "b"}]}', "槽位重复"),
        ('{"slots": [{"key": "scene", "text": "a"}, {"key": "other", "text": "b"}]}', "多出槽位"),
        ('{"slots": [{"key": "scene", "text": "a"}]}', "漏改了槽位"),
    ],
)
@pytest.mark.asyncio
async def test_输出结构不合法时分别报错(text: str, message: str) -> None:
    fake = FakeCompleter(text=text)
    with pytest.raises(BadRequest, match=message):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.asyncio
async def test_截断与内容拦截分开报() -> None:
    fake = FakeCompleter(payload=_ok(), finish_reason="length")
    with pytest.raises(BadRequest, match="被截断"):
        await rewrite_slot_texts(_spec(), complete=fake)

    fake = FakeCompleter(payload=_ok(), finish_reason="content_filter")
    with pytest.raises(BadRequest, match="内容安全"):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.parametrize("outcome", [Unauthorized("bad key"), Forbidden("denied")])
@pytest.mark.asyncio
async def test_上游401_403转成UpstreamError(outcome: BaseException) -> None:
    fake = FakeCompleter(error=outcome)
    with pytest.raises(UpstreamError, match="认证失败|无权访问"):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.asyncio
async def test_上游429原样抛出不被误转() -> None:
    """429 不是会话失效；前端没有 429 重放逻辑，保持 429 + 供应商消息。"""
    fake = FakeCompleter(error=RateLimited("busy"))
    with pytest.raises(RateLimited, match="busy"):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.parametrize(
    "outcome, message",
    [
        (httpx.ConnectError("refused"), "无法连接供应商"),
        (httpx.ConnectTimeout("connect timeout"), "请求未送达"),
        (httpx.ReadTimeout("slow"), "可能已受理"),
    ],
)
@pytest.mark.asyncio
async def test_传输错误不会以裸httpx异常漏出(
    outcome: BaseException, message: str
) -> None:
    fake = FakeCompleter(error=outcome)
    with pytest.raises(UpstreamError, match=message):
        await rewrite_slot_texts(_spec(), complete=fake)


@pytest.mark.asyncio
async def test_没有可改写槽位时直接400且不调模型() -> None:
    spec = TemplateSpec(
        key="demo",
        name="演示",
        model="m",
        prompt="人物 {{slot:hero}}",
        slots=(_slot("hero", 0, min_count=1, label="人物"),),
    )
    fake = FakeCompleter(payload=_ok())
    with pytest.raises(BadRequest, match="没有可随机的槽位"):
        await rewrite_slot_texts(spec, complete=fake)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_原文总长超限时直接400且不调模型(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(randomness, "MAX_REWRITE_SOURCE_CHARS", 5)
    fake = FakeCompleter(payload=_ok())
    with pytest.raises(BadRequest, match="超过上限"):
        await rewrite_slot_texts(_spec(), complete=fake)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_代码围栏JSON可以解析() -> None:
    fake = FakeCompleter(
        text="```json\n" + json.dumps(_ok(), ensure_ascii=False) + "\n```"
    )
    overrides = await rewrite_slot_texts(_spec(), complete=fake)
    assert set(overrides) == {"scene", "outfit"}


@pytest.mark.asyncio
async def test_动态预算随原文字数增长() -> None:
    long_text = "场景" * 300
    fake = FakeCompleter(payload=_ok(scene=long_text, outfit=long_text))
    await rewrite_slot_texts(_spec(scene_text=long_text), complete=fake)
    request, _, _ = fake.requests[0]
    assert request.params["max_tokens"] > randomness.MIN_REWRITE_TOKENS