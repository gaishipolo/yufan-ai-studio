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
# 刷新驱动目录 = 热接上新增的驱动文件。这是**运维级**动作：
# 不能挂在 read 上（看模型目录的人不该能改进程里装了什么），
# 也不该只凭 read:all —— 那是「看别人任务」的开关，与此无关。
PERM_REFRESH = "model_driver:refresh"
# 运行时配置读写：改的是 .env 里的 SSRF 白名单与供应商密钥，属于运维级高危动作。
# 读/写分开：能看配置（含密钥指纹）的人未必该能改；写权限不挂在 read 上。
PERM_CONFIG_READ = "model_driver:config:read"
PERM_CONFIG_WRITE = "model_driver:config:write"
# 运维页：刷新动作原来登记为「后台能力」（有权限码、没有页面可点）。P4 拍板
# 单独建一页承载它 —— 页面码与接口码分开，关页面时接口也跟着关。审计（谁在
# 什么时候刷了什么）后端目前只有日志、没有可查询记录，留待下一步。
PAGE = "page:ops"
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
    PermissionSpec(
        PERM_REFRESH,
        "刷新模型驱动目录",
        group="运维",
        description=(
            "重新扫描驱动目录，把新增的驱动文件热接上（进程级：只影响本实例，"
            "多实例各自收敛）。改已加载的文件不热替换，要重启才生效"
        ),
        pages=(PAGE,),
    ),
    PermissionSpec(
        PERM_CONFIG_READ,
        "查看运行时配置",
        group="运维",
        description="查看域名白名单与供应商接入状态；密钥只显示脱敏掩码与指纹，不显示明文",
        pages=(PAGE,),
    ),
    PermissionSpec(
        PERM_CONFIG_WRITE,
        "修改运行时配置",
        group="运维",
        description=(
            "修改域名白名单与供应商密钥/地址，写回服务实际读取的 .env；"
            "下一次请求即生效。高危：白名单是 SSRF 防线，写错会让外部链接被当成可信来源"
        ),
        pages=(PAGE,),
    ),
)

__all__ = [
    "PAGE",
    "PERM_CANCEL",
    "PERM_CANCEL_ANY",
    "PERM_CONFIG_READ",
    "PERM_CONFIG_WRITE",
    "PERM_CREATE",
    "PERM_READ",
    "PERM_READ_ALL",
    "PERM_REFRESH",
    "PERM_RETRY_ANY",
    "SPECS",
]
