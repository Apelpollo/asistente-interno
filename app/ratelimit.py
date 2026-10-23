from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

def rate_key(request: Request) -> str:
    return request.headers.get("X-API-Key") or get_remote_address(request)

limiter = Limiter(key_func=rate_key, default_limits=["60/minute"])