from modules.template.prompt_media import enrich_input_summary
from modules.template.schema import ReplacementInputSummary


def test_legacy_summary_uses_frozen_spec_order_and_keeps_all_three_images():
    summary = ReplacementInputSummary.model_validate(
        {
            "assets": [
                {
                    "id": "front",
                    "name": "原图.jpg",
                    "role": "target_product",
                    "type": "image",
                    "url": "/front",
                },
                {
                    "id": "video",
                    "name": "原片.mp4",
                    "role": "source_video",
                    "type": "video",
                    "url": "/video",
                },
                {
                    "id": "views",
                    "name": "三视图.png",
                    "role": "target_product",
                    "type": "image",
                    "url": "/views",
                },
                {
                    "id": "detail",
                    "name": "细节.png",
                    "role": "target_product",
                    "type": "image",
                    "url": "/detail",
                },
            ]
        }
    )
    spec = {
        "references": [
            {"id": "video"},
            {"id": "views", "usage": "多视结构"},
            {"id": "front", "usage": "主图"},
            {"id": "detail", "usage": "细节"},
        ]
    }
    result = enrich_input_summary(summary, spec)
    assert len(result.assets) == 4
    by_id = {item.id: item for item in result.assets}
    assert by_id["views"].token == "{{Mixed 2}}"
    assert by_id["front"].token == "{{Mixed 3}}"
    assert by_id["detail"].token == "{{Mixed 4}}"
    assert by_id["views"].usage == "多视结构"
    assert by_id["video"].preview_url == "/video/thumbnail"
    assert summary.assets[0].token == ""


def test_missing_history_never_guesses_tokens_from_current_assets():
    summary = ReplacementInputSummary.model_validate(
        {
            "assets": [
                {
                    "id": "front",
                    "name": "原图",
                    "role": "target_product",
                    "type": "image",
                    "url": "/front",
                },
            ]
        }
    )
    assert enrich_input_summary(summary, None).assets[0].token == ""
