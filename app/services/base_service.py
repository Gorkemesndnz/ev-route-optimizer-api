import httpx
import asyncio
from typing import Any
from app.utils.logger import get_logger

# Rate limit için Semaphore (tüm servisler arasında paylaşımlı,
# aynı anda en fazla 10 dış API çağrısı)
API_SEMAPHORE = asyncio.Semaphore(10)

logger = get_logger("base_service")

class ExternalAPIError(Exception):
    """Tüm 3. parti API hataları için özel hata sınıfı."""
    def __init__(self, source: str, status_code: int, detail: str):
        self.source = source
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{source}] API Error ({status_code}): {detail}")

class BaseService:
    """
    Diğer tüm servisler için temel sınıf (Hata yönetimi, Yeniden deneme, 
    Rate Limit, Asenkron client).
    """
    def __init__(self, base_url: str):
        self.base_url = base_url
        # 'httpx.AsyncClient' tüm servisler tarafından paylaşılacak
        # ve 'async def' fonksiyonlarda kullanılacak.
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def _call_api(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        headers: dict | None = None
    ) -> Any:
        """
        Exponential Backoff (Gecikmeli Yeniden Deneme) ve Rate Limiting
        ile korumalı ana API çağrı fonksiyonu.
        """
        retries = 3
        delay = 0.5  # saniye

        for attempt in range(retries):
            try:
                # Rate limit koruması (Semaphore)
                async with API_SEMAPHORE:
                    response = await self.client.request(
                        method=method,
                        url=endpoint,
                        params=params,
                        headers=headers
                    )
                
                response.raise_for_status()  # 4xx veya 5xx hata varsa exception fırlat
                data = response.json()  # JSON'u parse et
                
                # Google Maps API özel kontrolü: HTTP 200 olsa bile status field'ı kontrol et
                if self.base_url == "https://maps.googleapis.com/maps/api":
                    google_status = data.get("status")
                    if google_status != "OK":
                        error_message = data.get("error_message", f"Google API status: {google_status}")
                        logger.error(
                            "Google Maps API error detected",
                            source=self.base_url,
                            google_status=google_status,
                            error_message=error_message,
                            url=str(response.request.url)
                        )
                        raise ExternalAPIError(
                            source=self.base_url,
                            status_code=response.status_code,
                            detail=f"Google API error: {google_status} - {error_message}"
                        )
                
                return data

            except httpx.HTTPStatusError as e:
                logger.error(
                    "API call failed (HTTPStatusError)",
                    source=self.base_url,
                    status=e.response.status_code,
                    url=e.request.url,
                    attempt=attempt + 1
                )
                if attempt == retries - 1:  # Son denemeyse, hatayı fırlat
                    raise ExternalAPIError(
                        source=self.base_url,
                        status_code=e.response.status_code,
                        detail=str(e)
                    )
                
            except httpx.RequestError as e:  # Timeout, DNS hatası vb.
                logger.error(
                    "API call failed (RequestError)",
                    source=self.base_url,
                    error=str(e),
                    url=str(e.request.url),
                    attempt=attempt + 1
                )
                if attempt == retries - 1:
                    raise ExternalAPIError(
                        source=self.base_url,
                        status_code=503,  # Service Unavailable
                        detail=str(e)
                    )
        
            # Yeniden denemeden önce bekle (Exponential Backoff)
            await asyncio.sleep(delay)
            delay *= 2  # 0.5s, 1s, 2s

        # Bu noktaya gelinmemeli, ancak 'return' için
        raise ExternalAPIError(
            source=self.base_url,
            status_code=500,
            detail="API call failed after all retries"
        )
