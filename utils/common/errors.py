"""通用错误处理工具模块。

提供增强的错误日志记录、智能重试机制和错误分析功能。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ErrorSeverity(Enum):
    """错误严重程度枚举。"""
    LOW = "low"  # 低严重程度，可忽略
    MEDIUM = "medium"  # 中等严重程度，需要关注
    HIGH = "high"  # 高严重程度，需要立即处理
    CRITICAL = "critical"  # 严重错误，系统可能受影响


class ErrorCategory(Enum):
    """错误分类枚举。"""
    HTTP_ERROR = "http_error"  # HTTP错误
    NETWORK_ERROR = "network_error"  # 网络错误
    TIMEOUT_ERROR = "timeout_error"  # 超时错误
    AUTH_ERROR = "auth_error"  # 认证错误
    RATE_LIMIT_ERROR = "rate_limit_error"  # 频率限制错误
    VALIDATION_ERROR = "validation_error"  # 验证错误
    UNKNOWN_ERROR = "unknown_error"  # 未知错误


class ErrorContext:
    """错误上下文信息。"""
    
    def __init__(
        self,
        error: Exception,
        category: ErrorCategory,
        severity: ErrorSeverity,
        request_info: dict[str, Any] | None = None,
        response_info: dict[str, Any] | None = None,
        additional_info: dict[str, Any] | None = None,
    ) -> None:
        self.error = error
        self.category = category
        self.severity = severity
        self.request_info = request_info or {}
        self.response_info = response_info or {}
        self.additional_info = additional_info or {}
        self.timestamp = datetime.now().astimezone()
        self.error_id = f"err-{int(time.time())}-{id(error)}"
    
    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "error_id": self.error_id,
            "timestamp": self.timestamp.isoformat(),
            "error_type": type(self.error).__name__,
            "error_message": str(self.error),
            "category": self.category.value,
            "severity": self.severity.value,
            "request_info": self.request_info,
            "response_info": self.response_info,
            "additional_info": self.additional_info,
        }
    
    def log_error(self) -> None:
        """记录错误日志。"""
        error_dict = self.to_dict()
        
        if self.severity in {ErrorSeverity.HIGH, ErrorSeverity.CRITICAL}:
            logger.error(
                "严重错误 [%s] %s: %s",
                self.error_id,
                self.category.value,
                str(self.error),
                extra=error_dict,
            )
        elif self.severity == ErrorSeverity.MEDIUM:
            logger.warning(
                "警告 [%s] %s: %s",
                self.error_id,
                self.category.value,
                str(self.error),
                extra=error_dict,
            )
        else:
            logger.info(
                "信息 [%s] %s: %s",
                self.error_id,
                self.category.value,
                str(self.error),
                extra=error_dict,
            )


def categorize_http_error(status_code: int) -> tuple[ErrorCategory, ErrorSeverity]:
    """根据HTTP状态码分类错误。"""
    if status_code == 400:
        return ErrorCategory.VALIDATION_ERROR, ErrorSeverity.LOW
    elif status_code in {401, 403}:
        return ErrorCategory.AUTH_ERROR, ErrorSeverity.MEDIUM
    elif status_code == 404:
        return ErrorCategory.HTTP_ERROR, ErrorSeverity.MEDIUM
    elif status_code == 429:
        return ErrorCategory.RATE_LIMIT_ERROR, ErrorSeverity.MEDIUM
    elif 500 <= status_code < 600:
        return ErrorCategory.HTTP_ERROR, ErrorSeverity.HIGH
    else:
        return ErrorCategory.UNKNOWN_ERROR, ErrorSeverity.MEDIUM


def categorize_exception(exc: Exception) -> tuple[ErrorCategory, ErrorSeverity]:
    """根据异常类型分类错误。"""
    exc_type = type(exc).__name__
    
    if "Timeout" in exc_type or "timeout" in str(exc).lower():
        return ErrorCategory.TIMEOUT_ERROR, ErrorSeverity.MEDIUM
    elif "Connection" in exc_type or "connection" in str(exc).lower():
        return ErrorCategory.NETWORK_ERROR, ErrorSeverity.MEDIUM
    elif "HTTP" in exc_type:
        return ErrorCategory.HTTP_ERROR, ErrorSeverity.MEDIUM
    else:
        return ErrorCategory.UNKNOWN_ERROR, ErrorSeverity.LOW


def create_error_context(
    error: Exception,
    request_url: str | None = None,
    request_method: str | None = None,
    request_headers: dict[str, str] | None = None,
    request_body: Any = None,
    response_status: int | None = None,
    response_body: Any = None,
    additional_info: dict[str, Any] | None = None,
) -> ErrorContext:
    """创建错误上下文。"""
    # 分类错误
    if hasattr(error, "code") and isinstance(getattr(error, "code", None), int):
        category, severity = categorize_http_error(getattr(error, "code"))
    else:
        category, severity = categorize_exception(error)
    
    # 构建请求信息
    request_info = {}
    if request_url:
        request_info["url"] = request_url
    if request_method:
        request_info["method"] = request_method
    if request_headers:
        request_info["headers"] = request_headers
    if request_body is not None:
        # 截断过长的请求体
        body_str = str(request_body)
        if len(body_str) > 1000:
            body_str = body_str[:1000] + "..."
        request_info["body"] = body_str
    
    # 构建响应信息
    response_info = {}
    if response_status is not None:
        response_info["status_code"] = response_status
    if response_body is not None:
        # 截断过长的响应体
        body_str = str(response_body)
        if len(body_str) > 1000:
            body_str = body_str[:1000] + "..."
        response_info["body"] = body_str
    
    return ErrorContext(
        error=error,
        category=category,
        severity=severity,
        request_info=request_info,
        response_info=response_info,
        additional_info=additional_info,
    )


def smart_retry(
    func: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
    retryable_status_codes: set[int] | None = None,
    on_retry: Callable[[Exception, int], None] | None = None,
) -> T:
    """智能重试机制。
    
    Args:
        func: 要重试的函数
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        retryable_exceptions: 可重试的异常类型
        retryable_status_codes: 可重试的HTTP状态码
        on_retry: 重试时的回调函数
    
    Returns:
        函数执行结果
    
    Raises:
        最后一次重试的异常
    """
    if retryable_exceptions is None:
        retryable_exceptions = (ConnectionError, TimeoutError, OSError)
    
    if retryable_status_codes is None:
        retryable_status_codes = {429, 500, 502, 503, 504}
    
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:
            last_exception = exc
            
            # 检查是否可重试
            is_retryable = False
            
            # 检查异常类型
            if isinstance(exc, retryable_exceptions):
                is_retryable = True
            
            # 检查HTTP状态码
            if hasattr(exc, "code") and isinstance(getattr(exc, "code", None), int) and getattr(exc, "code") in retryable_status_codes:
                is_retryable = True
            
            # 如果不可重试或已达到最大重试次数，抛出异常
            if not is_retryable or attempt >= max_retries:
                raise
            
            # 计算延迟时间（指数退避）
            delay = min(base_delay * (2 ** attempt), max_delay)
            
            # 调用重试回调
            if on_retry:
                on_retry(exc, attempt + 1)
            
            # 记录重试日志
            logger.info(
                "重试第 %d/%d 次，延迟 %.1f 秒，错误: %s",
                attempt + 1,
                max_retries,
                delay,
                str(exc),
            )
            
            # 等待后重试
            time.sleep(delay)
    
    # 这里不会执行，但为了类型检查
    raise last_exception  # type: ignore[misc]


def enhanced_retry(
    func: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
    retryable_status_codes: set[int] | None = None,
    on_retry: Callable[[Exception, int], None] | None = None,
    request_url: str | None = None,
    request_method: str | None = None,
    request_headers: dict[str, str] | None = None,
    request_body: Any = None,
) -> T:
    """增强的重试机制，包含详细的错误记录。"""
    
    def on_retry_with_logging(exc: Exception, attempt: int) -> None:
        """带日志记录的重试回调。"""
        # 创建错误上下文
        error_context = create_error_context(
            error=exc,
            request_url=request_url,
            request_method=request_method,
            request_headers=request_headers,
            request_body=request_body,
            additional_info={"retry_attempt": attempt},
        )
        
        # 记录错误
        error_context.log_error()
        
        # 调用原始回调
        if on_retry:
            on_retry(exc, attempt)
    
    return smart_retry(
        func=func,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_exceptions=retryable_exceptions,
        retryable_status_codes=retryable_status_codes,
        on_retry=on_retry_with_logging,
    )


def analyze_404_error(
    url: str,
    response_body: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """分析404错误原因。
    
    Args:
        url: 请求的URL
        response_body: 响应体内容
        headers: 请求头
    
    Returns:
        包含错误分析结果的字典
    """
    analysis: dict[str, Any] = {
        "url": url,
        "status_code": 404,
        "timestamp": datetime.now().astimezone().isoformat(),
        "possible_causes": [],
        "recommendations": [],
        "is_retryable": False,
    }
    
    # 分析可能的原因
    if response_body:
        response_lower = response_body.lower()
        
        # 检查是否是API端点变更
        if "endpoint" in response_lower or "api" in response_lower:
            analysis["possible_causes"].append("API端点可能已变更")
            analysis["recommendations"].append("检查API文档，确认正确的端点URL")
        
        # 检查是否是资源不存在
        if "not found" in response_lower or "不存在" in response_lower:
            analysis["possible_causes"].append("请求的资源确实不存在")
            analysis["recommendations"].append("检查资源ID或关键词是否正确")
        
        # 检查是否是权限问题
        if "permission" in response_lower or "权限" in response_lower:
            analysis["possible_causes"].append("可能是权限问题导致的404")
            analysis["recommendations"].append("检查API密钥和权限配置")
    
    # 检查URL格式
    if not url.startswith(("http://", "https://")):
        analysis["possible_causes"].append("URL格式不正确")
        analysis["recommendations"].append("确保URL以http://或https://开头")
    
    # 根据分析结果判断是否可重试
    # 这里可以根据具体业务逻辑进行调整
    if len(analysis["possible_causes"]) > 0:
        # 检查是否有特定的可重试原因
        retryable_causes = ["API端点可能已变更"]
        has_retryable_cause = any(cause in analysis["possible_causes"] for cause in retryable_causes)
        
        if has_retryable_cause:
            # 如果有API端点变更等可重试原因，标记为可重试
            analysis["is_retryable"] = True
        else:
            # 如果有其他原因，可能需要人工检查
            analysis["is_retryable"] = False
    else:
        # 如果没有明确原因，可能是暂时性问题
        analysis["is_retryable"] = True
    
    return analysis


def log_http_request_response(
    url: str,
    method: str,
    request_headers: dict[str, str] | None = None,
    request_body: Any = None,
    response_status: int | None = None,
    response_body: Any = None,
    error: Exception | None = None,
    additional_info: dict[str, Any] | None = None,
) -> None:
    """记录HTTP请求和响应的详细信息。"""
    log_data: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "url": url,
        "method": method,
    }
    
    if request_headers:
        # 脱敏处理敏感头部
        sanitized_headers = {}
        for key, value in request_headers.items():
            if key.lower() in {"authorization", "cookie", "x-api-key"}:
                sanitized_headers[key] = "***REDACTED***"
            else:
                sanitized_headers[key] = value
        log_data["request_headers"] = sanitized_headers
    
    if request_body is not None:
        body_str = str(request_body)
        if len(body_str) > 1000:
            body_str = body_str[:1000] + "..."
        log_data["request_body"] = body_str
    
    if response_status is not None:
        log_data["response_status"] = response_status
    
    if response_body is not None:
        body_str = str(response_body)
        if len(body_str) > 1000:
            body_str = body_str[:1000] + "..."
        log_data["response_body"] = body_str
    
    if error:
        log_data["error_type"] = type(error).__name__
        log_data["error_message"] = str(error)
    
    if additional_info:
        log_data["additional_info"] = additional_info
    
    # 根据状态码决定日志级别
    if response_status is not None and isinstance(response_status, int):
        if response_status >= 500:
            logger.error("HTTP 请求详情: %s", log_data)
        elif response_status >= 400:
            logger.warning("HTTP 请求详情: %s", log_data)
        else:
            logger.info("HTTP 请求详情: %s", log_data)
    else:
        logger.info("HTTP 请求详情: %s", log_data)