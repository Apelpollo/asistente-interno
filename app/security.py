import os, secrets
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def _valid_keys() -> set[str]:
    return {k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()}

def require_api_key(key: str | None = Security(api_key_header)) -> str:
    if not key or not any(secrets.compare_digest(key, k) for k in _valid_keys()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o faltante")
    return key