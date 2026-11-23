"""去字幕派生任务：创建幂等、父任务校验、投影和删除。"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from core.config import get_settings
from core.context import CurrentUser
from core.exceptions import Forbidden, InvalidState, RateLimited
from core.response import PageParams
from db.session import get_session_factory
from infra.ai.router import ModelRouter
from infra.queue.memory import MemoryJobQueue
from modules.task.models import TaskFavorite, VideoTask
from modules.task.repository import TaskRepository
from modules.task.schema import TaskListQuery
from modules.task.service import TaskService


@pytest.fixture
async def setup_task(monkeypatch):
    async with get_session_factory()() as session:
        parent = VideoTask(
            title="去字幕验收", prompt="测试画面", model="测试", model_id="seedance-2.5",
            status="completed", output_url="videos/source.mp4", author_id="desub-owner",
            author_name="测试用户", downloadable=True,
        )
        session.add(parent)
        await session.commit()
        service = TaskService(session, TaskRepository(session), MemoryJobQueue(), ModelRouter())
        dispatch = AsyncMock()
        monkeypatch.setattr(service, "_dispatch", dispatch)
        actor = CurrentUser(id=parent.author_id, name=parent.author_name)
        yield service, parent, actor, dispatch
        await session.rollback()
        ids = select(VideoTask.id).where(VideoTask.author_id == actor.id)
        await session.execute(delete(TaskFavorite).where(TaskFavorite.task_id.in_(ids)))
        await session.execute(delete(VideoTask).where(VideoTask.author_id == actor.id))
        await session.commit()


async def test_create_is_idempotent_and_preserves_source(setup_task):
    service, parent, actor, dispatch = setup_task
    child = await service.create_desubtitle(parent.id, actor)
    again = await service.create_desubtitle(parent.id, actor)
    assert again.id == child.id
    assert child.derived_from_task_id == parent.id
    assert child.variant == "subtitle_erased"
    assert child.model_id == "libtv-subtitle-eraser"
    assert "videos/source.mp4" in child.params_json
    dispatch.assert_awaited_once()


async def test_concurrent_create_recovers_unique_conflict_during_flush(setup_task, monkeypatch):
    service, parent, actor, dispatch = setup_task
    parent_id = parent.id
    add = service.repo.add
    winner_id = None

    async def insert_competitor(task):
        nonlocal winner_id
        async with get_session_factory()() as competing:
            winner = VideoTask(
                title="并发去字幕", prompt="", model="去字幕", model_id="libtv-subtitle-eraser",
                author_id=actor.id, author_name=actor.name, derived_from_task_id=parent_id,
                variant="subtitle_erased", status="queued",
            )
            competing.add(winner)
            await competing.commit()
            winner_id = winner.id
        return await add(task)

    monkeypatch.setattr(service.repo, "add", insert_competitor)
    task = await service.create_desubtitle(parent_id, actor)
    assert task.id == winner_id
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("state,output", [("running", "videos/a.mp4"), ("completed", None)])
async def test_requires_completed_parent_with_output(setup_task, state, output):
    service, parent, actor, dispatch = setup_task
    parent.status, parent.output_url = state, output
    await service.session.commit()
    with pytest.raises(InvalidState):
        await service.create_desubtitle(parent.id, actor)
    dispatch.assert_not_awaited()


async def test_read_all_does_not_authorize_deriving_another_users_video(setup_task):
    service, parent, _, dispatch = setup_task
    reader = CurrentUser(id="other-reader", name="只读", permissions=frozenset({"task:read:all"}))
    with pytest.raises(Forbidden):
        await service.create_desubtitle(parent.id, reader)
    dispatch.assert_not_awaited()


async def test_cannot_derive_from_a_variant(setup_task):
    service, parent, actor, dispatch = setup_task
    parent.derived_from_task_id = "another-parent"
    parent.variant = "subtitle_erased"
    await service.session.commit()
    with pytest.raises(InvalidState):
        await service.create_desubtitle(parent.id, actor)
    dispatch.assert_not_awaited()


async def test_detail_and_list_project_variant_without_an_extra_card(setup_task):
    service, parent, actor, _ = setup_task
    child = await service.create_desubtitle(parent.id, actor)
    child.status, child.progress = "running", 42
    await service.session.commit()
    detail = await service.present(parent, actor)
    assert detail.subtitle_erase.progress == 42
    listing = await service.list_tasks(actor, TaskListQuery(), PageParams())
    assert [item.id for item in listing.items] == [parent.id]
    assert listing.items[0].subtitle_erase.id == child.id


async def test_delete_parent_removes_variant_and_its_favorites(setup_task):
    service, parent, actor, _ = setup_task
    child = await service.create_desubtitle(parent.id, actor)
    child_id = child.id
    service.session.add(TaskFavorite(user_id=actor.id, task_id=child_id))
    await service.session.commit()
    assert await service.delete_tasks([parent.id], actor) == (1, [])
    assert await service.session.get(VideoTask, child_id) is None
    assert await service.session.scalar(
        select(TaskFavorite.id).where(TaskFavorite.task_id == child_id)
    ) is None


async def test_long_parent_title_is_truncated_within_column_limit(setup_task):
    service, parent, actor, _ = setup_task
    parent.title = "超" * 200
    await service.session.commit()
    child = await service.create_desubtitle(parent.id, actor)
    assert len(child.title) <= 200
    assert child.title.endswith("（去字幕）")


async def test_create_respects_in_flight_limit_while_idempotent_read_still_works(
    setup_task, monkeypatch
):
    service, parent, actor, dispatch = setup_task
    monkeypatch.setattr(get_settings(), "task_max_in_flight_per_user", 1)
    blocking = VideoTask(
        title="在途占坑", prompt="p", model="测试", model_id="seedance-2.5",
        status="running", author_id=actor.id, author_name=actor.name,
    )
    service.session.add(blocking)
    await service.session.commit()

    with pytest.raises(RateLimited):
        await service.create_desubtitle(parent.id, actor)
    dispatch.assert_not_awaited()

    monkeypatch.setattr(get_settings(), "task_max_in_flight_per_user", 5)
    created = await service.create_desubtitle(parent.id, actor)
    monkeypatch.setattr(get_settings(), "task_max_in_flight_per_user", 1)
    # 已存在时走幂等返回，不被在途上限挡掉
    again = await service.create_desubtitle(parent.id, actor)
    assert again.id == created.id
