"""统一响应包装与分页原语。

所有接口返回 `{code, message, data}`，与前端 `shared/api/types.ts` 的
`ApiResponse<T>` 严格一致；HTTP 状态码同时保持语义正确。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import Field

from core.schema import CamelModel

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100


class ApiResponse(CamelModel, Generic[T]):
    """统一响应体。"""

    code: int = Field(default=0, description="0 表示成功，其余为业务错误码")
    message: str = Field(default="OK")
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = "OK") -> ApiResponse[T]:
        return cls(code=0, message=message, data=data)


class PageMeta(CamelModel):
    total: int = 0
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


class Page(CamelModel, Generic[T]):
    """标准分页结果：`items / total / page / pageSize`。"""

    items: list[T] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @classmethod
    def create(cls, items: Sequence[T], total: int, params: PageParams) -> Page[T]:
        return cls(items=list(items), total=total, page=params.page, page_size=params.page_size)


class PageParams:
    """分页查询参数依赖。

    前端传的是 camelCase（`pageSize`），因此显式声明 alias。
    用法：`params: PageParams = Depends()`。
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
        page_size: Annotated[
            int, Query(alias="pageSize", ge=1, le=MAX_PAGE_SIZE, description="每页条数")
        ] = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size
