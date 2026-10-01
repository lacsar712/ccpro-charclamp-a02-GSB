from __future__ import annotations

from litestar.connection import ASGIConnection
from litestar.middleware.session.server_side import ServerSideSessionBackend, ServerSideSessionConfig
from litestar.security.session_auth import SessionAuth
from sqlalchemy import select

from charclamp.domain.models import User
from charclamp.infra.db import SessionLocal


async def retrieve_user_handler(session: dict, connection: ASGIConnection) -> User | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        return result.scalar_one_or_none()


session_auth = SessionAuth[User, ServerSideSessionBackend](
    retrieve_user_handler=retrieve_user_handler,
    session_backend_config=ServerSideSessionConfig(
        session_id_bytes=32,
    ),
    # 注意：不能把 "/" 放进 exclude——它会作为正则前缀贪婪匹配所有路径，
    # 使整个鉴权中间件失效（Litestar 2.13 会告警并令 request.user 抛异常）。
    # 未登录访问受保护页时由 main.py 的 NotAuthorizedException 处理器重定向到 /login。
    exclude=["/login", "/logout", "/static", "/schema", "/favicon.ico"],
)
