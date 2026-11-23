"""模型驱动模块权限点（前缀必须与模块目录名一致）。

跨用户可见性与「替别人重跑」是**两个独立的开关**，与 `task` / `image` 模块同一套口径：
`model_driver:read` 只表示「能进这个模块」，不表示「能看到别人的任务」——
否则持 `read` 的账号可以查别人的任务详情（含 `externalId`、预签名直链）并取消/重试。
"""

from core.permissions import PermissionSpec

PERM_READ = "model_driver:read"
PERM_CREATE = "model_driver:create"
PERM_CANCEL = "model_driver:cancel"
# 跨用户可见：看别人的任务、进别人的详情，都靠这一个开关
PERM_READ_ALL = "model_driver:read:all"
# 替别人重跑是**花钱**的动作：不能只凭 read:all 就解锁（task/image 同款）
PERM_RETRY_ANY = "model_driver:retry:any"
# 替别人取消是**影响他人在跑任务**的动作：不认 read:all，单独一个开关
PERM_CANCEL_ANY = "model_driver:cancel:any"
SPECS = (
    PermissionSpec(
        PERM_READ,
        "查看模型驱动、模型目录与生成任务",
        group="内容生产",
        # 归「任务中心」：顶部那条 LibTV 额度条要读 `/model-driver/libtv/account`，
        # 它由本码把关。不归那页的话，关掉任务中心、额度条的数据接口仍然可用。
        pages=("page:task-center",),
    ),
    PermissionSpec(PERM_CREATE, "提交统一生成任务并重试失败记录", group="内容生产"),
    PermissionSpec(PERM_CANCEL, "取消生成任务", group="内容生产"),
    PermissionSpec(
        PERM_READ_ALL,
        "查看他人生成任务",
        group="内容生产",
        description="跨用户可见：详情查询的归属判定依据；不含重试/取消他人任务",
    ),
    PermissionSpec(
        PERM_RETRY_ANY,
        "重试他人生成任务",
        group="内容生产",
        description="会消耗额度：重试他人任务前额外校验此权限",
    ),
    PermissionSpec(
        PERM_CANCEL_ANY,
        "取消他人生成任务",
        group="内容生产",
        description="会真的调供应商取消，影响别人正在跑的任务；不认 read:all",
    ),
)

__all__ = [
    "PERM_CANCEL",
    "PERM_CANCEL_ANY",
    "PERM_CREATE",
    "PERM_READ",
    "PERM_READ_ALL",
    "PERM_RETRY_ANY",
    "SPECS",
]
