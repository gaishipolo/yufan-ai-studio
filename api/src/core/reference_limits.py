"""参考素材的硬性限制校验（纯逻辑，两条链共用）。

供应商的限制各不一样（Agnes 收 256-5760 像素的边长，百炼收 240-8000），
但**判据的形状**是同一套，所以只留一份实现：

- 受理期按资产库里的尺寸判一次 —— 早失败、报错说人话；
- 提交前按真实字节再判一次 —— 资产库里的宽高可能是 0（历史行 / 未探测），
  或者与实际文件对不上，两道都过才不会被上游一句英文打回。

放 `core/`：业务模块之间不许互相 import（架构守卫），而受理期在
`modules/task`、提交前在 `modules/model_driver`，共用逻辑必须落在底层。
"""

from __future__ import annotations

from core.exceptions import BadRequest

__all__ = ["check_side_lengths"]


def check_side_lengths(
    *,
    label: str,
    width: int,
    height: int,
    min_side: int,
    max_side: int,
) -> None:
    """图片**单边**必须落在 [min_side, max_side] 之间，否则抛 `BadRequest`。

    判的是单边而不是总像素：上游的原话是
    `input image side length must be between 256 and 5760 pixels` ——
    一张 6000x6000 的图总像素早就超了，但上游说的仍然是边长。
    """
    if width < min_side or width > max_side or height < min_side or height > max_side:
        subject = f"参考图「{label}」" if label else "参考图"
        raise BadRequest(
            f"{subject}的边长必须介于 {min_side} 与 {max_side} 像素之间"
            f"（实测 {width}x{height}），请换一张尺寸合适的图"
        )
