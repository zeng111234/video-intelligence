"""错误处理使用示例。

展示如何在爬虫代码中使用新的错误处理功能。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.common.errors import (
    analyze_404_error,
    create_error_context,
    enhanced_retry,
    log_http_request_response,
    smart_retry,
)


def example_1_basic_error_handling():
    """示例1: 基本错误处理。"""
    print("示例1: 基本错误处理")
    print("-" * 40)
    
    try:
        # 模拟一个HTTP请求
        import urllib.request
        import urllib.error
        
        # 尝试访问一个不存在的URL
        urllib.request.urlopen("https://httpbin.org/status/404")
    except urllib.error.HTTPError as e:
        print(f"捕获到HTTP错误: {e.code}")
        
        # 分析404错误
        analysis = analyze_404_error(
            url="https://httpbin.org/status/404",
            response_body="Not Found",
        )
        
        print(f"错误分析: {analysis}")
        print(f"是否可重试: {analysis['is_retryable']}")
        print(f"可能原因: {analysis['possible_causes']}")
        print(f"建议: {analysis['recommendations']}")
    except Exception as e:
        print(f"其他错误: {e}")


def example_2_detailed_error_context():
    """示例2: 详细的错误上下文。"""
    print("\n示例2: 详细的错误上下文")
    print("-" * 40)
    
    # 模拟一个错误
    error = Exception("模拟的HTTP 404错误")
    error.code = 404
    
    # 创建详细的错误上下文
    context = create_error_context(
        error=error,
        request_url="https://api.example.com/v1/search",
        request_method="GET",
        request_headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token",
            "Cookie": "session=abc123",
        },
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
    
    # 记录错误日志
    print("\n记录错误日志...")
    context.log_error()


def example_3_http_request_logging():
    """示例3: HTTP请求响应日志记录。"""
    print("\n示例3: HTTP请求响应日志记录")
    print("-" * 40)
    
    # 记录HTTP请求和响应
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


def example_4_smart_retry():
    """示例4: 智能重试机制。"""
    print("\n示例4: 智能重试机制")
    print("-" * 40)
    
    # 模拟一个不稳定的函数
    call_count = 0
    
    def unstable_function():
        nonlocal call_count
        call_count += 1
        print(f"  第 {call_count} 次尝试...")
        
        if call_count < 3:
            raise ConnectionError("连接失败")
        return "成功"
    
    try:
        # 使用智能重试
        result = smart_retry(
            unstable_function,
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0,
            retryable_exceptions=(ConnectionError,),
        )
        print(f"  最终结果: {result}")
        print(f"  总尝试次数: {call_count}")
    except Exception as e:
        print(f"  重试失败: {e}")


def example_5_enhanced_retry_with_logging():
    """示例5: 带日志的增强重试。"""
    print("\n示例5: 带日志的增强重试")
    print("-" * 40)
    
    # 模拟一个不稳定的函数
    call_count = 0
    
    def unstable_function():
        nonlocal call_count
        call_count += 1
        print(f"  第 {call_count} 次尝试...")
        
        if call_count < 2:
            raise TimeoutError("请求超时")
        return "成功"
    
    try:
        # 使用增强的重试机制
        result = enhanced_retry(
            unstable_function,
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0,
            retryable_exceptions=(TimeoutError,),
            request_url="https://api.example.com/v1/search",
            request_method="GET",
            request_headers={"Content-Type": "application/json"},
            request_body={"keyword": "二手车"},
        )
        print(f"  最终结果: {result}")
        print(f"  总尝试次数: {call_count}")
    except Exception as e:
        print(f"  重试失败: {e}")


def example_6_integration_with_crawler():
    """示例6: 与爬虫集成。"""
    print("\n示例6: 与爬虫集成")
    print("-" * 40)
    
    # 模拟一个爬虫函数
    def crawl_keyword(keyword, platform="douyin"):
        """模拟爬虫函数。"""
        print(f"  开始爬取关键词: {keyword}, 平台: {platform}")
        
        # 模拟HTTP请求
        import urllib.request
        import urllib.error
        
        try:
            # 模拟请求
            url = "https://httpbin.org/status/404"
            urllib.request.urlopen(url)
            return {"status": "success", "data": []}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # 分析404错误
                analysis = analyze_404_error(
                    url=url,
                    response_body="Not Found",
                )
                
                # 创建错误上下文
                error_context = create_error_context(
                    error=e,
                    request_url=url,
                    request_method="GET",
                    response_status=e.code,
                    response_body="Not Found",
                    additional_info={
                        "keyword": keyword,
                        "platform": platform,
                        "error_analysis": analysis,
                    },
                )
                
                # 记录错误
                error_context.log_error()
                
                # 根据分析结果决定是否重试
                if analysis["is_retryable"]:
                    print("  错误可重试，准备重试...")
                    # 这里可以实现重试逻辑
                else:
                    print(f"  错误不可重试: {analysis['possible_causes']}")
                
                return {"status": "error", "error": str(e), "analysis": analysis}
            else:
                raise
        except Exception as e:
            # 记录其他错误
            error_context = create_error_context(
                error=e,
                request_url=url,
                request_method="GET",
                additional_info={"keyword": keyword, "platform": platform},
            )
            error_context.log_error()
            return {"status": "error", "error": str(e)}
    
    # 执行爬虫
    result = crawl_keyword("二手车", "douyin")
    print(f"  爬取结果: {result}")


def main():
    """主函数。"""
    print("错误处理使用示例")
    print("=" * 50)
    
    example_1_basic_error_handling()
    example_2_detailed_error_context()
    example_3_http_request_logging()
    example_4_smart_retry()
    example_5_enhanced_retry_with_logging()
    example_6_integration_with_crawler()
    
    print("\n" + "=" * 50)
    print("示例完成！")
    print("\n主要功能:")
    print("1. analyze_404_error() - 分析404错误原因")
    print("2. create_error_context() - 创建详细的错误上下文")
    print("3. log_http_request_response() - 记录HTTP请求响应日志")
    print("4. smart_retry() - 智能重试机制")
    print("5. enhanced_retry() - 带日志的增强重试")


if __name__ == "__main__":
    main()
