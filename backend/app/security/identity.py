"""最小 JWT 身份上下文。

身份只在 HTTP 边界解析一次，后续服务接收不可变的 ``AuthenticatedIdentity``。
模型输出和客户端请求都不能覆盖 ``user_id``；工具查询必须使用这里的服务端身份。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)
_bearer_dependency = Depends(_bearer)
_DEMO_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class IdentityTokenError(ValueError):
    """令牌缺失、签名不正确或声明不满足最小安全要求。"""


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """从服务端验证结果派生的用户身份和制度可见范围。"""

    user_id: UUID
    scopes: frozenset[str]
    roles: frozenset[str] = frozenset()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(signing_input: str, secret: str) -> str:
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64encode(signature)


def encode_access_token(
    identity: AuthenticatedIdentity,
    *,
    secret: str | None = None,
    expires_in: timedelta | None = None,
) -> str:
    """生成测试和开发环境使用的 HS256 令牌；生产令牌应由身份服务签发。"""
    settings = get_settings()
    signing_secret = secret or settings.jwt_secret.get_secret_value()
    now = datetime.now(UTC)
    lifetime = expires_in or timedelta(minutes=settings.jwt_expire_minutes)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(identity.user_id),
        "scopes": sorted(identity.scopes),
        "roles": sorted(identity.roles),
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
    }
    encoded_header = _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{encoded_header}.{encoded_payload}"
    return f"{signing_input}.{_sign(signing_input, signing_secret)}"


def decode_access_token(token: str, *, secret: str | None = None) -> AuthenticatedIdentity:
    """校验签名、签发者、过期时间和身份声明，返回不可变身份上下文。"""
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        header = json.loads(_b64decode(encoded_header))
        payload: dict[str, Any] = json.loads(_b64decode(encoded_payload))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityTokenError("Malformed access token") from exc

    settings = get_settings()
    signing_secret = secret or settings.jwt_secret.get_secret_value()
    signing_input = f"{encoded_header}.{encoded_payload}"
    expected_signature = _sign(signing_input, signing_secret)
    if header.get("alg") != "HS256" or not hmac.compare_digest(
        encoded_signature, expected_signature
    ):
        raise IdentityTokenError("Invalid access token signature")
    if payload.get("iss") != settings.jwt_issuer:
        raise IdentityTokenError("Invalid access token issuer")
    try:
        expires_at = int(payload["exp"])
        user_id = UUID(str(payload["sub"]))
        scopes = frozenset(str(scope) for scope in payload.get("scopes", []))
        roles = frozenset(str(role) for role in payload.get("roles", []))
    except (KeyError, TypeError, ValueError) as exc:
        raise IdentityTokenError("Invalid access token claims") from exc
    if expires_at <= int(datetime.now(UTC).timestamp()):
        raise IdentityTokenError("Access token expired")
    if not scopes:
        raise IdentityTokenError("Access token has no scopes")
    return AuthenticatedIdentity(user_id=user_id, scopes=scopes, roles=roles)


async def get_current_identity(
    credentials: HTTPAuthorizationCredentials | None = _bearer_dependency,
) -> AuthenticatedIdentity:
    """FastAPI 身份依赖：生产环境拒绝匿名请求，开发和测试允许固定身份。"""
    settings = get_settings()
    if credentials is None:
        if settings.app_env != "production":
            return AuthenticatedIdentity(
                user_id=_DEMO_USER_ID,
                scopes=frozenset({"all_employees"}),
                roles=frozenset({"employee"}),
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(credentials.credentials)
    except IdentityTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
