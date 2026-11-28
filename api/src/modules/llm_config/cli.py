"""LLM 配置的一次性迁移命令。"""

from __future__ import annotations

import argparse
import asyncio


def cmd_migrate_default(args: argparse.Namespace) -> None:
    """把旧 default 供应商迁到具名供应商，并同步随机改写配置里的模型引用。"""
    from core.random_rewrite import RANDOM_REWRITE_META_KEY
    from db.app_meta import get_json, set_json
    from db.session import get_session_factory
    from modules.llm_config.migration import migrate_legacy_default

    try:
        result = migrate_legacy_default(args.name)
    except ValueError as exc:
        raise SystemExit(f"迁移失败：{exc}") from exc

    async def sync_runtime_refs() -> str:
        factory = get_session_factory()
        async with factory() as session:
            entry = await get_json(session, RANDOM_REWRITE_META_KEY)
            if not isinstance(entry, dict):
                return ""
            model = entry.get("model")
            if not isinstance(model, str) or model not in result.model_ids:
                return ""
            await set_json(
                session,
                RANDOM_REWRITE_META_KEY,
                {**entry, "model": result.model_ids[model]},
                detail="default 供应商迁移",
            )
            await session.commit()
            return result.model_ids[model]

    updated = asyncio.run(sync_runtime_refs())
    print(f"已迁移 default → {result.provider.name}")
    print(f"模型：{', '.join(m.id for m in result.provider.models)}")
    print(f"降级链：{', '.join(result.chain)}")
    if updated:
        print(f"随机改写配置模型已同步为：{updated}")


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "llm-migrate-default",
        help="把旧 default 供应商的 LLM_*/llm/* 迁移为具名供应商",
    )
    parser.add_argument(
        "--name",
        default="primary",
        help="迁移后的供应商名（默认 primary）",
    )
    parser.set_defaults(func=cmd_migrate_default)


__all__ = ["register"]
