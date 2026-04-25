from typing import TypeVar, Generic, Optional, Any
from pydantic import BaseModel

T = TypeVar('T')


class ApiErrorPayload(BaseModel):
    """Structured error payload — .NET ExceptionHandlingMiddleware ile hizalı şekil."""
    code: str
    message: str
    errors: Optional[Any] = None  # field-level validation errors, eğer varsa


class ApiResponse(BaseModel, Generic[T]):
    """Standardized API response wrapper ensuring {success, data, error} structure."""
    success: bool
    data: Optional[T] = None
    error: Optional[ApiErrorPayload] = None

    @classmethod
    def ok(cls, data: T) -> "ApiResponse[T]":
        return cls(success=True, data=data)

    @classmethod
    def fail(
        cls,
        message: str,
        data: Optional[Any] = None,
        *,
        errors: Optional[Any] = None,
        code: str = "UPSTREAM_ERROR",
    ) -> "ApiResponse[Any]":
        """
        Hata yanıtı üretir. `message` zorunlu; `data` opsiyonel (default response payload),
        `errors` keyword-only field-level detay, `code` keyword-only machine-readable kimlik.
        Geriye uyumluluk: eski `fail(msg, data_list)` çağrıları hâlâ çalışır.
        """
        return cls(
            success=False,
            data=data,
            error=ApiErrorPayload(code=code, message=message, errors=errors),
        )
