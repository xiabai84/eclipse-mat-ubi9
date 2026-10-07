"""Optional bearer-token authentication.

With API_TOKEN set, every endpoint except /health requires "Authorization: Bearer <API_TOKEN>". Without it the
service is open (as before) and logs a warning at startup.
"""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_settings

_bearer = HTTPBearer(auto_error=False)


def require_token(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    token = get_settings().api_token
    if not token:
        return
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"})
