import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional
import httpx
from app.config import settings

logger = logging.getLogger("gemini-proxy")


class GeminiAPIError(Exception):
    def __init__(self, status_code: int, message: str, details: Optional[Dict] = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.details = details or {}


class GeminiRateLimitError(GeminiAPIError):
    """Raised when 429 Too Many Requests or RESOURCE_EXHAUSTED occurs."""
    pass


class GeminiModelNotFoundError(GeminiAPIError):
    """Raised when 404 Model Not Found occurs."""
    pass


class GeminiAuthError(GeminiAPIError):
    """Raised when 400/403 API Key is invalid."""
    pass


class GeminiServerError(GeminiAPIError):
    """Raised when 500/503 Gemini upstream server error occurs."""
    pass


class GeminiClient:
    def __init__(self):
        self.base_url = settings.gemini_base_url
        self.timeout = httpx.Timeout(settings.request_timeout, connect=15.0)

    def _parse_error_body(self, text: str) -> Dict[str, Any]:
        try:
            data = json.loads(text)
            err = data.get("error", {})
            return {
                "message": err.get("message", text),
                "status": err.get("status", ""),
                "code": err.get("code", 0),
                "details": err.get("details", []),
            }
        except Exception:
            return {"message": text, "status": "UNKNOWN", "code": 0}

    def _raise_appropriate_error(self, status_code: int, body_text: str):
        err_info = self._parse_error_body(body_text)
        msg = err_info["message"]

        if status_code == 429 or err_info.get("status") == "RESOURCE_EXHAUSTED":
            raise GeminiRateLimitError(status_code, msg, err_info)
        elif status_code == 404 or "not found" in msg.lower():
            raise GeminiModelNotFoundError(status_code, msg, err_info)
        elif status_code in (400, 401, 403) and ("API_KEY" in body_text or "permission" in msg.lower()):
            raise GeminiAuthError(status_code, msg, err_info)
        elif status_code in (500, 502, 503, 504):
            raise GeminiServerError(status_code, msg, err_info)
        else:
            raise GeminiAPIError(status_code, msg, err_info)

    async def generate_content(
        self, model: str, api_key: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make a non-streaming generateContent request to Gemini API."""
        url = f"{self.base_url}/models/{model}:generateContent"
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code != 200:
                self._raise_appropriate_error(resp.status_code, resp.text)

            return resp.json()

    async def stream_generate_content(
        self, model: str, api_key: str, payload: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Connect to streamGenerateContent SSE endpoint and yield parsed JSON candidate chunks.
        Validates HTTP status before yielding anything so fallback can handle initial errors.
        """
        url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            req = client.build_request("POST", url, headers=headers, json=payload)
            resp = await client.send(req, stream=True)

            if resp.status_code != 200:
                body_bytes = await resp.aread()
                await resp.aclose()
                self._raise_appropriate_error(resp.status_code, body_bytes.decode("utf-8", errors="replace"))

            # Stream was established with 200 OK
            try:
                buffer = ""
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str:
                            try:
                                chunk = json.loads(data_str)
                                yield chunk
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse SSE JSON chunk: {data_str}")
            finally:
                await resp.aclose()


gemini_client = GeminiClient()
