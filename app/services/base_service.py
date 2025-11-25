import httpx
import asyncio
from typing import Any, Optional
from app.utils.logger import get_logger

logger = get_logger("BaseService")

# ==========================================
# GLOBAL HTTP CLIENT (Tek seferde yaratılır)
# ==========================================
GLOBAL_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(8.0))


# ==========================================
# RATE LIMIT (Tüm servisler ortak 10 API çağrısı)
# ==========================================
API_LIMIT = asyncio.Semaphore(10)


# ==========================================
# ÖZEL HATA SINIFI
# ==========================================
class ExternalAPIError(Exception):
    def __init__(self, source: str, status_code: int, detail: str):
        super().__init__(f"[{source}] API Error {status_code}: {detail}")
        self.source = source
        self.status_code = status_code
        self.detail = detail


# ==========================================
# BASE SERVICE (TÜM SERVİSLERİN TEMELİ)
# ==========================================
class BaseService:
    """
    Tüm 3. parti API client'ları için ortak altyapı.
    - Rate limiting
    - Retry (Exponential Backoff)
    - Global HTTP client
    - Error handling
    """

    MAX_RETRIES = 3
    INITIAL_BACKOFF = 0.4  # saniye

    def __init__(self, base_url: str, name: Optional[str] = None):
        self.base_url = base_url
        self.name = name or base_url  # Log için isimlendirme kolaylığı

    async def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        """
        Asıl API çağrı fonksiyonu:
        - Rate limit
        - Retry with exponential backoff
        - Standart error handling
        """
        backoff = self.INITIAL_BACKOFF

        for attempt in range(1, self.MAX_RETRIES + 1):

            try:
                async with API_LIMIT:
                    response = await GLOBAL_CLIENT.request(
                        method=method.upper(),
                        url=self.base_url + endpoint,
                        params=params,
                        headers=headers,
                    )

                # HTTP 4xx veya 5xx
                if response.status_code >= 400:
                    detail = response.text or "Unknown error"
                    logger.error(
                        f"{self.name} responded with error",
                        status=response.status_code,
                        detail=detail,
                        url=str(response.request.url),
                        attempt=attempt
                    )

                    if attempt == self.MAX_RETRIES:
                        raise ExternalAPIError(self.name, response.status_code, detail)

                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue

                # JSON parse hataları için güvenli dönüş
                try:
                    return response.json()
                except Exception:
                    logger.error(
                        f"{self.name} JSON parse error",
                        url=str(response.request.url),
                        body=response.text
                    )
                    raise ExternalAPIError(
                        self.name,
                        response.status_code,
                        "Failed to parse JSON response"
                    )

            except httpx.RequestError as e:
                logger.error(
                    f"{self.name} network error",
                    error=str(e),
                    attempt=attempt
                )

                if attempt == self.MAX_RETRIES:
                    raise ExternalAPIError(self.name, 503, str(e))

                await asyncio.sleep(backoff)
                backoff *= 2

        # Buraya geliyorsa retry mekanizması bitmiştir
        raise ExternalAPIError(self.name, 500, "Request failed after retries")
