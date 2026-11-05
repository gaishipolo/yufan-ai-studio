"""ASGI 入口：`uvicorn main:app --reload`。"""

from __future__ import annotations

from app import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    from core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=settings.is_dev,
    )
