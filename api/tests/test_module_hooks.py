"""模块钩子的接线：`ModuleSpec.cli` 与 `ModuleSpec.seed`。

这两条钩子是「框架不认识业务」的最后两块拼图，所以守的点不是「功能能用」，
而是三条容易退化的性质：

1. **删得掉**：`cli.py` / `seed.py` 一行业务 import 都没有（`test_architecture` 守）；
2. **顺序对**：演示数据必须先把账号种出来，否则其它模块的行指向一个不存在的人；
3. **坏一个不连坐**：某个模块 import 炸了，`app backup` 这种救命命令还得能跑。
"""

from __future__ import annotations

import argparse

import pytest
from fastapi import APIRouter

import cli
from api.module import ModuleSpec


# --------------------------------------------------------------------------- #
# seed：顺序与跨模块一致性
# --------------------------------------------------------------------------- #
def test_identity_seeds_before_any_other_module() -> None:
    """identity 必须是第一个种数据的模块。

    其它模块的演示行都带 `owner_id` / `author_id`，指向 `SeedContext.demo_user_id`。
    顺序错了不会报错（这些表之间没有外键），只会让列表页显示一个不存在的人——
    正是那种「要盯着界面看才发现」的坏。
    """
    from api.discovery import active_modules

    seeding = [spec for spec in active_modules() if spec.seed is not None]
    assert {spec.name for spec in seeding} >= {"identity", "asset", "task", "image"}

    ordered = sorted(seeding, key=lambda spec: (spec.seed_order, spec.name))
    assert ordered[0].name == "identity"
    others = [spec.seed_order for spec in seeding if spec.name != "identity"]
    assert ordered[0].seed_order < min(others)


def test_seed_order_is_a_separate_field_from_route_order() -> None:
    """`seed_order` 与 `order` 是两个字段。

    合成一个的话，调接口注册顺序（动 `order`）就会顺手改掉演示数据的先后，
    而这两件事的「正确顺序」根本不是一回事。
    """
    plain = ModuleSpec(name="x", router=APIRouter())
    late_routes = ModuleSpec(name="x", router=APIRouter(), order=900)
    early_seed = ModuleSpec(name="x", router=APIRouter(), seed_order=5)

    assert plain.seed_order == 100 and late_routes.seed_order == 100
    assert early_seed.order == 100


class _CapturingSession:
    """只收 `add_all` 的行，不碰数据库。

    用它而不是真库：这条要验的是「模块有没有用 `ctx`」，而不是「库里现在是什么」。
    真库里同时躺着别的用例造的数据（别的 owner），断言整张表的归属必然被它们污染。

    **查询一律返回空**（而不是抛 `NotImplementedError`）：seed 现在会建分类树、
    补词表，这些都要先查一次"有没有"。查到空 → 它按"什么都没有"走新建分支，
    于是那些行也会进 `rows`——这正好让我们能顺带断言它们的 `added_by` 也是探针 id。
    """

    def __init__(self) -> None:
        self.rows: list[object] = []

    def add_all(self, rows: list[object]) -> None:
        self.rows.extend(rows)

    def add(self, row: object) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        """行已经在 `rows` 里，落库是假动作。

    仍需提供一个空实现：seed 在写标签关联前要 `flush()` 才能拿到素材的生成 id。
    """

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def delete(self, row: object) -> None:
        pass

    def begin_nested(self):
        """`async with session.begin_nested():` —— 幂等初始化的 savepoint。"""
        return _NullSavepoint()

    async def scalars(self, _statement: object):
        return _EmptyResult()

    async def execute(self, _statement: object):
        return _EmptyResult()

    async def scalar(self, _statement: object):
        return None

    async def get(self, _entity: object, _ident: object):
        return None


class _EmptyResult:
    """`(await session.scalars(...)).all()` 与 `.execute(...).all()` 都要能跑。"""

    def all(self) -> list[object]:
        return []

    def first(self) -> None:
        return None

    def scalars(self) -> _EmptyResult:
        return self


class _NullSavepoint:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.parametrize(
    ("module_name", "owner_attr"),
    [("task", "author_id"), ("asset", "owner_id"), ("image", "owner_id")],
)
def test_seed_uses_the_context_user_instead_of_a_hardcoded_id(
    module_name: str, owner_attr: str
) -> None:
    """演示数据必须用 `ctx.demo_user_id`，不许写死 `"u_001"`。

    探针 id 是「不可能出现在代码里」的值：模块要是硬编码了，这里立刻红。
    这条正是 `SeedContext` 存在的理由——五个模块各抄一份常量，改一次要改五处，
    而没有任何东西会告诉你改漏了。

    **不再断言 `count == len(session.rows)`**：seed 的返回值一直是「业务行数」，
    而分类树、词表这类派生结构不计数（它们由 `ensure_tree` / `ensure_vocabulary`
    建，两个入口也可以被迁移、bootstrap 单独调用）。改成「带归属字段的行必须全是
    探针 id」，这才是这条用例真正要守的东西；`count` 只要求不小于带归属的行数。
    """
    import asyncio
    import importlib

    from api.module import SeedContext

    seed_module = importlib.import_module(f"modules.{module_name}.seed")
    session = _CapturingSession()
    context = SeedContext(demo_user_id="u_probe", demo_user_name="探针")

    count = asyncio.run(seed_module.seed(session, context))  # type: ignore[arg-type]

    assert session.rows, f"{module_name} 的 seed 没有产出任何行"
    owned = [row for row in session.rows if hasattr(row, owner_attr)]
    assert owned, f"{module_name} 的 seed 没有产出任何带 {owner_attr} 的行"
    assert count >= len(owned), f"{module_name} 返回 {count}，少于它写入的 {len(owned)} 行"

    # `owner_attr` 之外，标签关联用的是 `added_by`（它记的是"谁打的"，不是归属）。
    # 两处都必须是探针 id：写死 `"u_001"` 的种子数据在换演示账号后会指向另一个人。
    for attr in (owner_attr, "added_by"):
        values = {getattr(row, attr) for row in session.rows if hasattr(row, attr)}
        if values:
            assert values == {"u_probe"}, (
                f"{module_name} 的演示数据没走 ctx，{attr} 实际取值：{values}"
            )


def test_demo_user_is_seeded_with_the_configured_id() -> None:
    """identity 种的第一个账号必须就是配置里那个 id。

    其它模块按这个 id 种归属，两者对不上不会报错，只会让列表页显示一个不存在的人。
    """
    import asyncio

    from sqlalchemy import select

    from core.config import get_settings
    from db.session import get_session_factory
    from modules.identity.models import User

    expected = get_settings().demo_user_id

    async def read() -> str | None:
        async with get_session_factory()() as session:
            return (
                await session.execute(select(User.id).where(User.id == expected))
            ).scalar_one_or_none()

    assert asyncio.run(read()) == expected


# --------------------------------------------------------------------------- #
# cli：命令来自模块，且不能互相踩
# --------------------------------------------------------------------------- #
MODULE_COMMANDS = {
    "create-admin",
    "prune-logins",
    "prune-sessions",
    "sync-roles",
    "import-archive-index",
    "migrate-blobs",
    "prune-blobs",
    "rebuild-mirrors",
}


def _fresh_sub() -> argparse._SubParsersAction[argparse.ArgumentParser]:
    parser = argparse.ArgumentParser()
    return parser.add_subparsers(dest="command")


def test_module_commands_are_registered_from_modules() -> None:
    sub = _fresh_sub()
    cli._register_module_commands(sub)
    missing = MODULE_COMMANDS - set(sub.choices)
    assert not missing, f"模块的运维命令没挂上：{sorted(missing)}"


def test_duplicate_command_name_is_rejected_and_restored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """模块想顶掉框架命令时，**原命令必须还在**，而且要有可见的报错。

    只检测不还原的话，`app backup` 在那之后就已经是坏的了——报一行错却留下一个
    坏掉的 CLI，比不报还糟。

    这里是跨版本兜底：3.13 的 argparse 自己抛 `conflicting subparser`，
    3.11/3.12（项目的 target）是**静默覆盖**。所以断言只看两件事——
    原对象没被换掉、stderr 有话说——而不绑定是哪个版本抛的。
    """
    import api.discovery as discovery

    def evil(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        sub.add_parser("backup")

    monkeypatch.setattr(
        discovery,
        "active_modules",
        lambda: [ModuleSpec(name="evil", router=APIRouter(), cli=evil)],
    )

    sub = _fresh_sub()
    original = sub.add_parser("backup")
    cli._register_module_commands(sub)

    assert sub.choices["backup"] is original, "框架命令被模块顶掉了，且没有还原"
    assert "evil" in capsys.readouterr().err


def test_one_broken_module_does_not_take_down_the_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """某个模块注册命令时抛异常，其它模块照常挂上。

    `app backup` / `app restore` 是出事时才跑的命令，那时候坏掉的可能就是某个模块。
    """
    import api.discovery as discovery

    def boom(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        raise RuntimeError("这个模块装坏了")

    def fine(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        sub.add_parser("hello")

    monkeypatch.setattr(
        discovery,
        "active_modules",
        lambda: [
            ModuleSpec(name="broken", router=APIRouter(), cli=boom),
            ModuleSpec(name="fine", router=APIRouter(), cli=fine),
        ],
    )

    sub = _fresh_sub()
    cli._register_module_commands(sub)

    assert "hello" in sub.choices, "一个模块炸了，连累了别的模块"
    assert "注册失败" in capsys.readouterr().err


def test_module_discovery_failure_does_not_take_down_the_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """连模块发现本身炸了也不该让 `app` 起不来。"""
    import api.discovery as discovery

    def explode() -> list[ModuleSpec]:
        raise RuntimeError("发现失败")

    monkeypatch.setattr(discovery, "active_modules", explode)

    sub = _fresh_sub()
    cli._register_module_commands(sub)  # 不抛

    assert "模块发现失败" in capsys.readouterr().err
