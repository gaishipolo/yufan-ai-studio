"""模型驱动模块权限点（前缀必须与模块目录名一致）。"""

from core.permissions import PermissionSpec

SPECS = (
    PermissionSpec("model_driver:read", "查看模型驱动、模型目录与生成任务", group="内容生产"),
    PermissionSpec("model_driver:create", "提交统一生成任务并重试失败记录", group="内容生产"),
    PermissionSpec("model_driver:cancel", "取消生成任务", group="内容生产"),
)
