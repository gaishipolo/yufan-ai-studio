"""可恢复删除只修改管理标记，不修改生成状态、输入或调用回执。"""

from datetime import UTC, datetime
from pathlib import Path

from core.logging import get_logger
from core.schema import CamelModel, UtcDateTime
from infra.storage.archive import lease
from modules.template.replacement_repository import ReplacementRepository

logger = get_logger(__name__)

# 后缀必须是 .lock：桥接层 update() 会遍历 records 给每个文件算 sha256，
# 只跳过 `.lock/.download/.tmp`。叫 `.lease` 时它会读到正被本进程锁住的
# 字节，在归档盘（SMB）上返回 EBUSY，执行器整轮失败。
MANAGEMENT_LOCK = "records/management.lock"


class RunManagement(CamelModel):
    deleted_at: UtcDateTime | None = None
    changed_by: str | None = None


def read_management(path: Path, repo: ReplacementRepository) -> RunManagement:
    file = path / "records/management.json"
    if file.exists():
        return RunManagement.model_validate(repo.storage.read(file))
    return RunManagement()


def set_deleted(run_id: str, owner: str, deleted: bool) -> None:
    repo = ReplacementRepository()
    path, _ = repo.owned(run_id, owner)
    with lease(path / MANAGEMENT_LOCK):
        state = read_management(path, repo)
        if bool(state.deleted_at) == deleted:
            return
        state.deleted_at = datetime.now(UTC) if deleted else None
        state.changed_by = owner
        repo.storage.write(path / "records/management.json", state.model_dump(mode="json"))
        logger.info(
            "替换档案%s run=%s owner=%s",
            "移入回收站" if deleted else "从回收站恢复",
            run_id,
            owner,
        )
