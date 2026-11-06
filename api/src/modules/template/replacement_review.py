"""人工验收独立归档；单文件原子保存结论与历史，不覆盖执行器状态。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from core.exceptions import Conflict, InvalidState
from core.logging import get_logger
from infra.archive import lease
from modules.template.replacement_repository import ReplacementRepository
from modules.template.replacement_schema import (
    ReplacementReview,
    ReplacementReviewEvent,
    ReplacementReviewIn,
)

logger = get_logger(__name__)

# 评价可以反复修改；只保留最近 20 次修订，避免单文件无限增长。
HISTORY_LIMIT = 20


def read_review(path: Path, repo: ReplacementRepository, legacy: str) -> ReplacementReview:
    file = path / "review/review.json"
    result = (
        ReplacementReview.model_validate(repo.storage.read(file))
        if file.is_file()
        else ReplacementReview(status=legacy)
    )
    # 早期仅写 pending 占位；已有人工结论不能被占位文件抹掉。
    if result.version == 0 and result.status == "pending" and legacy != "pending":
        result.status = legacy
    return result


# 与 MANAGEMENT_LOCK 同理：review/ 也在桥接层的 checksum 遍历范围内，
# 锁文件名必须以 .lock 结尾，否则并发轮询时会读到被锁住的字节。
REVIEW_LOCK = "review/review.lock"


def save_review(run_id: str, payload: ReplacementReviewIn, owner: str) -> None:
    repo = ReplacementRepository()
    path, _ = repo.owned(run_id, owner)
    with lease(path / REVIEW_LOCK):
        path, meta = repo.owned(run_id, owner)
        if meta.status != "success" or not (path / "outputs/generated.mp4").is_file():
            raise InvalidState("仅可评价已成功生成且成片已保存的任务")
        previous = read_review(path, repo, meta.review_status)
        if previous.version != payload.expected_version:
            raise Conflict("评价已被更新，请刷新并核对最新结论后再保存")
        event = ReplacementReviewEvent(
            status=payload.status,
            version=previous.version + 1,
            reason_codes=payload.reason_codes,
            comment=payload.comment.strip(),
            reviewed_by=owner,
            reviewed_at=datetime.now(UTC),
        )
        # 将修订和历史放进同一原子文件，避免两份文件一成功一失败。
        # 历史只保留最近 N 次：评价可以反复改，无上限会把这个文件写成日志。
        history = [
            *previous.history,
            ReplacementReviewEvent.model_validate(previous.model_dump()),
        ][-HISTORY_LIMIT:]
        result = ReplacementReview(**event.model_dump(), history=history)
        repo.storage.write(path / "review/review.json", result.model_dump(mode="json"))
        logger.info(
            "替换成片评价已保存 run_id=%s owner=%s version=%s status=%s",
            run_id, owner, result.version, result.status,
        )
