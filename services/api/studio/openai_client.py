"""What every OpenAI adapter of the app shares: its errors, JSON-schema helpers and the connection check.

The adapters themselves (series extraction, episode breakdown) live next to the step they serve.
"""

from __future__ import annotations

import httpx

from .domain import DomainError
from .model_catalog import MODEL_CATALOG
from .secrets import get_provider_key


class PlannerRemoteError(DomainError):
    def __init__(self, message: str, request_id: str = ""):
        super().__init__(message, 409)
        self.request_id = request_id


class PlannerNotSent(DomainError):
    """A preflight failure before any paid HTTP request was made."""


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}


def check_connection(model: str, transport=None) -> dict:
    """Ask OpenAI whether the saved key can see the model. Generates nothing and costs nothing."""
    if model not in MODEL_CATALOG:
        raise DomainError("Model OpenAI không được hỗ trợ.")
    try:
        key = get_provider_key("OPENAI")
    except Exception as exc:
        raise DomainError("Không đọc được khóa trong kho bảo mật Windows.", 409) from exc
    if not key:
        raise DomainError("Chưa lưu khóa OpenAI trong Windows Credential Manager.", 409)
    try:
        with httpx.Client(transport=transport, timeout=60, trust_env=False) as client:
            response = client.get(f"https://api.openai.com/v1/models/{model}", headers={"Authorization": f"Bearer {key}"})
        if response.status_code != 200:
            raise DomainError(f"OpenAI kiểm tra kết nối trả HTTP {response.status_code}; kiểm tra khóa và quyền model.", 409)
    except httpx.HTTPError as exc:
        raise DomainError("Không kết nối được OpenAI; kiểm tra mạng và thử lại.", 409) from exc
    return {"reachable": True, "model": model, "message": "Endpoint model OpenAI trả lời; chưa xác minh lời gọi tạo nháp hoặc phát sinh yêu cầu sinh nội dung."}
