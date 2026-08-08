"""测试HTTP 404错误修复效果。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.adapters.official import (
    OfficialApiError,
    _default_transport,
)
from src.adapters.oneapi import OneApiLicensedSearchProvider, LicensedProviderError
from utils.common.errors import (
    analyze_404_error,
    create_error_context,
    log_http_request_response,
    smart_retry,
    ErrorCategory,
    ErrorSeverity,
)


class TestHttp404ErrorAnalysis:
    """测试HTTP 404错误分析功能。"""
    
    def test_analyze_404_error_with_api_endpoint_change(self):
        """测试API端点变更导致的404错误分析。"""
        response_body = "The API endpoint has been changed. Please check the documentation."
        analysis = analyze_404_error(
            url="https://api.example.com/v1/search",
            response_body=response_body,
        )
        
        assert analysis["status_code"] == 404
        assert "API端点可能已变更" in analysis["possible_causes"]
        # API端点变更被认为是可重试的
        assert analysis["is_retryable"] is True
    
    def test_analyze_404_error_with_resource_not_found(self):
        """测试资源不存在导致的404错误分析。"""
        response_body = "Resource not found. The requested item does not exist."
        analysis = analyze_404_error(
            url="https://api.example.com/v1/items/123",
            response_body=response_body,
        )
        
        assert analysis["status_code"] == 404
        assert "请求的资源确实不存在" in analysis["possible_causes"]
        assert analysis["is_retryable"] is False
    
    def test_analyze_404_error_with_invalid_url(self):
        """测试URL格式错误导致的404错误分析。"""
        analysis = analyze_404_error(
            url="invalid-url",
            response_body=None,
        )
        
        assert analysis["status_code"] == 404
        assert "URL格式不正确" in analysis["possible_causes"]
        assert analysis["is_retryable"] is False


class TestErrorContextCreation:
    """测试错误上下文创建功能。"""
    
    def test_create_error_context_with_http_error(self):
        """测试创建HTTP错误上下文。"""
        # 创建一个模拟的HTTP错误
        class MockHttpError(Exception):
            def __init__(self, code: int, message: str):
                super().__init__(message)
                self.code = code
        
        error = MockHttpError(404, "Not Found")
        
        context = create_error_context(
            error=error,
            request_url="https://api.example.com/v1/test",
            request_method="GET",
            request_headers={"Content-Type": "application/json"},
            request_body={"key": "value"},
            response_status=404,
            response_body="Not Found",
        )
        
        assert context.error == error
        assert context.category == ErrorCategory.HTTP_ERROR
        assert context.severity == ErrorSeverity.MEDIUM
        assert context.request_info["url"] == "https://api.example.com/v1/test"
        assert context.request_info["method"] == "GET"
        assert context.response_info["status_code"] == 404
    
    def test_create_error_context_with_timeout_error(self):
        """测试创建超时错误上下文。"""
        error = TimeoutError("Connection timed out")
        context = create_error_context(error=error)
        
        assert context.error == error
        assert context.category == ErrorCategory.TIMEOUT_ERROR
        assert context.severity == ErrorSeverity.MEDIUM


class TestSmartRetry:
    """测试智能重试机制。"""
    
    def test_smart_retry_success_on_first_attempt(self):
        """测试第一次尝试成功的情况。"""
        mock_func = MagicMock(return_value="success")
        result = smart_retry(mock_func, max_retries=3)
        
        assert result == "success"
        assert mock_func.call_count == 1
    
    def test_smart_retry_success_after_retry(self):
        """测试重试后成功的情况。"""
        mock_func = MagicMock(side_effect=[TimeoutError, "success"])
        result = smart_retry(mock_func, max_retries=3, base_delay=0.1)
        
        assert result == "success"
        assert mock_func.call_count == 2
    
    def test_smart_retry_failure_after_max_retries(self):
        """测试达到最大重试次数后失败的情况。"""
        mock_func = MagicMock(side_effect=TimeoutError)
        
        with pytest.raises(TimeoutError):
            smart_retry(mock_func, max_retries=2, base_delay=0.1)
        
        assert mock_func.call_count == 3  # 初始尝试 + 2次重试
    
    def test_smart_retry_with_non_retryable_exception(self):
        """测试不可重试的异常。"""
        mock_func = MagicMock(side_effect=ValueError("Invalid value"))
        
        with pytest.raises(ValueError):
            smart_retry(mock_func, max_retries=3, base_delay=0.1)
        
        assert mock_func.call_count == 1  # 只尝试一次


class TestDouyinKeywordAdapter404Handling:
    """测试抖音关键词适配器的404错误处理。"""
    
    def test_default_transport_404_error_analysis(self):
        """测试默认传输层对404错误的分析。"""
        # 模拟urlopen抛出HTTPError
        from urllib.error import HTTPError
        from email.message import Message
        
        # 创建HTTPError实例
        mock_fp = MagicMock()
        mock_fp.read.return_value = b"Not Found"
        mock_headers = Message()
        
        mock_http_error = HTTPError(
            url="https://api.douyin.com/v1/search",
            code=404,
            msg="Not Found",
            hdrs=mock_headers,
            fp=mock_fp
        )
        
        with patch("src.adapters.official.urlopen", side_effect=mock_http_error):
            with pytest.raises(OfficialApiError) as exc_info:
                _default_transport(
                    method="GET",
                    url="https://api.douyin.com/v1/search",
                    headers={"Content-Type": "application/json"},
                    body=None,
                )
            
            error = exc_info.value
            assert error.code == 404
            assert "404" in str(error)
    
    def test_default_transport_404_retryable_detection(self):
        """测试404错误是否可重试的检测。"""
        # 模拟404错误响应，包含API端点变更信息
        from urllib.error import HTTPError
        from email.message import Message
        
        # 创建HTTPError实例
        mock_fp = MagicMock()
        mock_fp.read.return_value = b"The API endpoint has been changed"
        mock_headers = Message()
        
        mock_http_error = HTTPError(
            url="https://api.douyin.com/v1/search",
            code=404,
            msg="Not Found",
            hdrs=mock_headers,
            fp=mock_fp
        )
        
        with patch("src.adapters.official.urlopen", side_effect=mock_http_error):
            with pytest.raises(OfficialApiError) as exc_info:
                _default_transport(
                    method="GET",
                    url="https://api.douyin.com/v1/search",
                    headers={"Content-Type": "application/json"},
                    body=None,
                )
            
            error = exc_info.value
            assert error.code == 404
            # 由于响应中包含"endpoint"，应该标记为可重试
            assert error.retryable is True


class TestOneApiAdapter404Handling:
    """测试OneAPI适配器的404错误处理。"""
    
    def test_raise_http_error_404_not_retryable_without_body(self):
        """测试_raise_http_error(404)无响应体时的处理——无法判断端点变更，不可重试。"""
        # 当没有响应体时，analyze_404_error 无法确认是端点变更
        # 因此不可重试
        with pytest.raises(LicensedProviderError) as exc_info:
            OneApiLicensedSearchProvider._raise_http_error(404)
        
        error = exc_info.value
        assert error.code == "404"
        # 无响应体时无法判断端点变更，默认不可重试
        assert error.retryable is False
        assert "资源未找到" in str(error)
    
    def test_raise_http_error_500_retryable(self):
        """测试_raise_http_error方法对500错误的处理。"""
        # 500错误应该被标记为可重试
        with pytest.raises(LicensedProviderError) as exc_info:
            OneApiLicensedSearchProvider._raise_http_error(500)
        
        error = exc_info.value
        assert error.code == "500"
        assert error.retryable is True
        assert "服务暂时不可用" in str(error)
    
    def test_raise_http_error_429_not_retryable(self):
        """测试_raise_http_error方法对429错误的处理。"""
        # 429错误不应该被标记为可重试
        with pytest.raises(LicensedProviderError) as exc_info:
            OneApiLicensedSearchProvider._raise_http_error(429)
        
        error = exc_info.value
        assert error.code == "429"
        assert error.retryable is False
        assert "限制请求频率" in str(error)


class TestErrorLogging:
    """测试错误日志记录功能。"""
    
    def test_log_http_request_response(self):
        """测试HTTP请求响应日志记录。"""
        # 创建模拟日志记录器
        mock_logger = MagicMock()
        
        with patch("utils.common.errors.logger", mock_logger):
            log_http_request_response(
                url="https://api.example.com/v1/test",
                method="GET",
                request_headers={"Content-Type": "application/json"},
                request_body={"key": "value"},
                response_status=404,
                response_body="Not Found",
            )
        
        # 验证日志记录被调用
        assert mock_logger.warning.called or mock_logger.info.called
    
    def test_error_context_log_error(self):
        """测试错误上下文日志记录。"""
        # 创建一个模拟的HTTP错误
        class MockHttpError(Exception):
            def __init__(self, code: int, message: str):
                super().__init__(message)
                self.code = code
        
        error = MockHttpError(404, "Not Found")
        context = create_error_context(
            error=error,
            request_url="https://api.example.com/v1/test",
            request_method="GET",
            response_status=404,
            response_body="Not Found",
        )
        
        # 创建模拟日志记录器
        mock_logger = MagicMock()
        
        with patch("utils.common.errors.logger", mock_logger):
            context.log_error()
        
        # 验证日志记录被调用（根据错误严重程度，可能调用warning或info）
        assert mock_logger.warning.called or mock_logger.info.called


def test_integration_404_error_handling():
    """集成测试404错误处理。"""
    # 模拟urlopen抛出HTTPError
    from urllib.error import HTTPError
    from email.message import Message
    
    # 创建HTTPError实例
    mock_fp = MagicMock()
    mock_fp.read.return_value = b"Not Found"
    mock_headers = Message()
    
    mock_http_error = HTTPError(
        url="https://api.douyin.com/v1/search",
        code=404,
        msg="Not Found",
        hdrs=mock_headers,
        fp=mock_fp
    )
    
    with patch("src.adapters.official.urlopen", side_effect=mock_http_error):
        try:
            _default_transport(
                method="GET",
                url="https://api.douyin.com/v1/search",
                headers={"Content-Type": "application/json"},
                body=None,
            )
            assert False, "应该抛出异常"
        except OfficialApiError as e:
            # 验证错误被正确处理
            assert e.code == 404
            assert "404" in str(e)
            # 验证错误信息包含有用的调试信息
            assert "资源未找到" in str(e)


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])