"""演示HTTP 404错误处理功能。"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from src.adapters.oneapi import OneApiLicensedSearchProvider, LicensedProviderError
from utils.common.errors import analyze_404_error, create_error_context, log_http_request_response


def demo_404_analysis():
    """演示404错误分析功能。"""
    print("=== 404错误分析演示 ===")
    
    # 模拟不同的404错误场景
    scenarios = [
        {
            "name": "API端点变更",
            "url": "https://api.douyin.com/v1/search",
            "response_body": "The API endpoint has been changed. Please use /v2/search instead.",
        },
        {
            "name": "资源不存在",
            "url": "https://api.douyin.com/v1/videos/12345",
            "response_body": "Not Found. The requested video does not exist.",
        },
        {
            "name": "权限问题",
            "url": "https://api.douyin.com/v1/search",
            "response_body": "Permission denied. You don't have access to this resource.",
        },
        {
            "name": "未知原因",
            "url": "https://api.douyin.com/v1/search",
            "response_body": "Error occurred.",
        },
    ]
    
    for scenario in scenarios:
        print(f"\n场景: {scenario['name']}")
        print(f"URL: {scenario['url']}")
        
        analysis = analyze_404_error(
            url=scenario['url'],
            response_body=scenario['response_body'],
        )
        
        print(f"可能原因: {analysis['possible_causes']}")
        print(f"建议: {analysis['recommendations']}")
        print(f"是否可重试: {analysis['is_retryable']}")


def demo_error_context():
    """演示错误上下文创建功能。"""
    print("\n=== 错误上下文创建演示 ===")
    
    # 模拟一个HTTP错误
    error = Exception("HTTP 404: Not Found")
    error.code = 404
    
    context = create_error_context(
        error=error,
        request_url="https://api.douyin.com/v1/search",
        request_method="GET",
        request_headers={"Content-Type": "application/json", "Authorization": "Bearer ***"},
        request_body={"keyword": "二手车", "count": 10},
        response_status=404,
        response_body="Not Found",
        additional_info={"platform": "douyin", "retry_count": 1},
    )
    
    print(f"错误ID: {context.error_id}")
    print(f"错误类型: {type(context.error).__name__}")
    print(f"错误消息: {context.error}")
    print(f"错误分类: {context.category.value}")
    print(f"严重程度: {context.severity.value}")
    print(f"请求URL: {context.request_info.get('url')}")
    print(f"请求方法: {context.request_info.get('method')}")
    print(f"响应状态码: {context.response_info.get('status_code')}")
    print(f"时间戳: {context.timestamp}")


def demo_logging():
    """演示HTTP请求响应日志记录功能。"""
    print("\n=== HTTP请求响应日志记录演示 ===")
    
    # 模拟一个HTTP请求
    log_http_request_response(
        url="https://api.douyin.com/v1/search",
        method="GET",
        request_headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token",
            "Cookie": "session=abc123",
        },
        request_body={"keyword": "二手车", "count": 10},
        response_status=404,
        response_body="Not Found",
        additional_info={"platform": "douyin", "request_id": "req-123"},
    )
    
    print("HTTP请求响应日志已记录到控制台。")


def demo_oneapi_404_handling():
    """演示OneAPI适配器的404错误处理。"""
    print("\n=== OneAPI 404错误处理演示 ===")
    
    try:
        # 模拟404错误
        OneApiLicensedSearchProvider._raise_http_error(404)
    except LicensedProviderError as e:
        print("捕获到OneAPI 404错误:")
        print(f"  错误消息: {e}")
        print(f"  错误代码: {e.code}")
        print(f"  是否可重试: {e.retryable}")
        print(f"  错误类型: {e.kind.value}")


def main():
    """主函数。"""
    print("HTTP 404错误处理功能演示")
    print("=" * 50)
    
    demo_404_analysis()
    demo_error_context()
    demo_logging()
    demo_oneapi_404_handling()
    
    print("\n" + "=" * 50)
    print("演示完成！")
    print("\n主要改进:")
    print("1. 增强了404错误分析功能，能够识别不同原因导致的404错误")
    print("2. 创建了详细的错误上下文，便于调试和追踪")
    print("3. 添加了HTTP请求响应日志记录，包含敏感信息脱敏")
    print("4. 为OneAPI和抖音官方适配器添加了智能404错误处理")
    print("5. 实现了基于错误分析的智能重试机制")


if __name__ == "__main__":
    main()