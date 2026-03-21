from typing import TypeVar, Generic, Optional, Any
from pydantic import BaseModel

T = TypeVar('T')

class ApiResponse(BaseModel, Generic[T]):
    """Standardized API response wrapper ensuring {success, data, error} structure."""
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None
    
    @classmethod
    def ok(cls, data: T) -> "ApiResponse[T]":
        return cls(success=True, data=data)
        
    @classmethod
    def fail(cls, error: str, data: Optional[Any] = None) -> "ApiResponse[Any]":
        return cls(success=False, error=error, data=data)
