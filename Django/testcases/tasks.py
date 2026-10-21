"""
测试用例执行的Celery异步任务
"""
import logging
import asyncio
import re
from celery import shared_task
from django.utils import timezone
from django.db import transaction, connections
from datetime import datetime
from typing import Dict, Any
from django.conf import settings
import os
import json
import uuid
import httpx
from rest_framework_simplejwt.tokens import RefreshToken

from .models import TestExecution, TestSuite, TestCaseResult, TestCase
from prompts.models import UserPrompt, PromptType
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='testcases.execute_test_suite')
def execute_test_suite(self, execution_id):
    """
    执行测试套件的异步任务
    
    Args:
        execution_id: TestExecution实例的ID
        
    Returns:
        dict: 执行结果摘要
    """
    try:
        from .execution_tracking import snapshot_case
        with transaction.atomic():
            execution = TestExecution.objects.select_for_update(of=('self',)).select_related('suite').get(id=execution_id)
            if execution.status != 'pending':
                return {'status': execution.status}
            suite = execution.suite
            execution.status = 'running'
            execution.started_at = timezone.now()
            execution.celery_task_id = self.request.id
            # Support pending records made by older versions as well.
            if not execution.results.exists():
                cases = list(suite.testcases.order_by('level', 'id'))
                TestCaseResult.objects.bulk_create([
                    TestCaseResult(execution=execution, testcase=case, case_snapshot=snapshot_case(case))
                    for case in cases])
            execution.total_count = execution.results.count()
            execution.save()
            all_tasks = list(execution.results.select_related('testcase'))

        # 获取并发配置
        max_concurrent = suite.max_concurrent_tasks
        logger.info(f"并发配置: {max_concurrent} 个任务同时执行")
        
        # 使用asyncio执行并发测试
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _execute_tasks_concurrently(execution, all_tasks, max_concurrent)
            )
        finally:
            loop.run_until_complete(sync_to_async(connections.close_all)())
            loop.close()
        
        # 更新执行记录为已完成
        execution.refresh_from_db()
        from .execution_tracking import refresh_execution
        execution = refresh_execution(execution.id)
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'completed_at', 'updated_at'])
        
        logger.info(f"测试套件执行完成: {suite.name}, "
                   f"通过: {execution.passed_count}, "
                   f"失败: {execution.failed_count}, "
                   f"错误: {execution.error_count}, "
                   f"跳过: {execution.skipped_count}")
        
        return {
            'execution_id': execution.id,
            'suite_name': suite.name,
            'status': execution.status,
            'total': execution.total_count,
            'passed': execution.passed_count,
            'failed': execution.failed_count,
            'skipped': execution.skipped_count,
            'error': execution.error_count,
            'pass_rate': execution.pass_rate,
            'duration': execution.duration
        }
        
    except TestExecution.DoesNotExist:
        error_msg = f"测试执行记录不存在: {execution_id}"
        logger.error(error_msg)
        return {'error': error_msg}
        
    except Exception as e:
        error_msg = f"执行测试套件时发生错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        from .execution_tracking import finish_execution
        for result_id in TestCaseResult.objects.filter(execution_id=execution_id, status__in=['pending', 'running']).values_list('id', flat=True):
            finish_execution(result_id, forced='error', reason=error_msg)
        TestExecution.objects.filter(pk=execution_id, status__in=['pending', 'running']).update(
            status='error', completed_at=timezone.now())

        return {'error': error_msg}


def execute_single_testcase(result: TestCaseResult):
    """
    执行单个测试用例 - 通过对话API驱动测试执行
    
    Args:
        result: TestCaseResult实例
    """
    logger.info(f"开始执行测试用例: {result.testcase.name} (ID: {result.testcase.id})")
    
    try:
        # 在新的事件循环中运行异步执行
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_execute_testcase_via_chat_api(result))
        finally:
            loop.run_until_complete(sync_to_async(connections.close_all)())
            loop.close()
        
        result.refresh_from_db()
        logger.info('测试用例执行结束: %s, 状态: %s', result.testcase.name, result.status)
        
    except Exception as e:
        error_msg = f"执行测试用例失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        from .execution_tracking import finish_execution
        finish_execution(result.pk, forced='error', reason=error_msg)
        raise


async def _execute_tasks_concurrently(execution, tasks_list, max_concurrent):
    """
    并发执行测试任务
    
    Args:
        execution: TestExecution实例
        tasks_list: TestCaseResult 列表
        max_concurrent: 最大并发数
    """
    import asyncio
    
    # 使用信号量控制并发数
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def execute_with_semaphore(task_obj):
        """带信号量控制的执行函数"""
        async with semaphore:
            # 检查是否已取消
            current_execution = await sync_to_async(TestExecution.objects.get)(id=execution.id)
            if current_execution.status == 'cancelled':
                task_name = task_obj.testcase.name
                logger.info(f"测试执行已取消，跳过任务: {task_name}")
                return
            
            try:
                # 执行测试用例
                await _execute_testcase_via_chat_api(task_obj)
                task_name = task_obj.testcase.name
                
                # 刷新状态
                await sync_to_async(task_obj.refresh_from_db)()
                logger.info('任务执行结束: %s, 状态: %s', task_name, task_obj.status)
                
                # 统一状态映射
                status_map = {
                    'pass': 'pass', 'passed': 'pass',
                    'fail': 'fail', 'failed': 'fail',
                    'skip': 'skip', 'skipped': 'skip',
                    'error': 'error', 'blocked': 'blocked', 'cancelled': 'cancelled'
                }
                normalized_status = status_map.get(task_obj.status, 'error')
                
                # 更新统计（使用原子操作避免竞态）
                await sync_to_async(_update_execution_counts)(execution, normalized_status)
                
            except Exception as e:
                task_name = task_obj.testcase.name
                    
                error_msg = f"并发执行任务失败: {str(e)}"
                logger.error(f"{error_msg} - {task_name}", exc_info=True)
                
                from .execution_tracking import finish_execution
                await sync_to_async(finish_execution)(task_obj.pk, forced='error', reason=error_msg)

    # 创建所有任务
    async_tasks = [execute_with_semaphore(task) for task in tasks_list]
    
    # 并发执行所有任务
    await asyncio.gather(*async_tasks, return_exceptions=True)


def _update_execution_counts(execution, status):
    from .execution_tracking import refresh_execution
    refresh_execution(execution.id)


@sync_to_async
def _get_testcase_steps(testcase):
    """获取测试用例步骤"""
    return list(testcase.steps.all().order_by('step_number'))

@sync_to_async
def _get_test_execution_prompt(executor):
    """获取测试用例执行提示词"""
    return UserPrompt.get_user_prompt_by_type(executor, PromptType.TEST_CASE_EXECUTION)

@sync_to_async
def _save_result(result: TestCaseResult):
    """异步安全地保存测试结果"""
    result.save()


def _normalize_media_url(url: str) -> str:
    """
    规范化媒体URL，确保正确添加MEDIA_URL前缀
    避免双重前缀问题（如 /media//media/...）
    
    Args:
        url: 原始URL路径
        
    Returns:
        规范化后的URL
    """
    if not url:
        return url
    
    # 如果已经是完整的HTTP URL，直接返回
    if url.startswith('http://') or url.startswith('https://'):
        return url
    
    # 规范化路径分隔符（将反斜杠替换为正斜杠）
    url = url.replace('\\', '/')
    
    media_url = settings.MEDIA_URL.rstrip('/')  # 通常是 '/media'
    
    # 如果已经以 MEDIA_URL 开头，直接返回
    if url.startswith(media_url + '/') or url.startswith(media_url):
        return url
    
    # 如果以 / 开头，去掉开头的 /
    if url.startswith('/'):
        url = url[1:]
    
    return f"{media_url}/{url}"


def _extract_test_result_json(response_text: str) -> dict | None:
    """
    从AI响应中提取测试结果JSON，支持多种格式
    
    支持的格式：
    1. 纯JSON
    2. ```json ... ``` 代码块
    3. ``` ... ``` 代码块
    4. 混合文本中的JSON对象
    
    Returns:
        解析后的JSON对象，或None
    """
    if not response_text or not response_text.strip():
        return None
    
    response_text = response_text.strip()
    
    # 预处理：处理可能的转义字符
    # 有些情况下响应中的换行符是字面量 \\n 而不是实际换行符
    normalized_text = response_text.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
    # 处理转义的引号 (如 \\\" -> ")
    normalized_text = normalized_text.replace('\\"', '"')
    
    # 方法1: 尝试提取 ```json ... ``` 代码块
    json_block_pattern = r'```json\s*([\s\S]*?)\s*```'
    for text in [normalized_text, response_text]:
        matches = re.findall(json_block_pattern, text, re.DOTALL)
        for match in matches:
            try:
                result = json.loads(match.strip())
                if isinstance(result, dict) and ('status' in result or 'steps' in result):
                    logger.debug(f"从 ```json 代码块提取JSON成功")
                    return result
            except json.JSONDecodeError:
                continue
    
    # 方法2: 尝试提取普通 ``` ... ``` 代码块
    code_block_pattern = r'```\s*([\s\S]*?)\s*```'
    for text in [normalized_text, response_text]:
        matches = re.findall(code_block_pattern, text, re.DOTALL)
        for match in matches:
            try:
                result = json.loads(match.strip())
                if isinstance(result, dict) and ('status' in result or 'steps' in result):
                    logger.debug(f"从普通代码块提取JSON成功")
                    return result
            except json.JSONDecodeError:
                continue
    
    # 方法3: 尝试直接解析整个响应
    for text in [normalized_text, response_text]:
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                logger.debug(f"直接解析响应为JSON成功")
                return result
        except json.JSONDecodeError:
            pass
    
    # 方法4: 尝试从文本中提取JSON对象 (寻找 { ... } 结构)
    # 从最后一个可能的JSON对象开始查找（通常结果在末尾）
    brace_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    for text in [normalized_text, response_text]:
        matches = re.findall(brace_pattern, text)
        for match in reversed(matches):  # 从最后一个开始尝试
            try:
                result = json.loads(match)
                if isinstance(result, dict) and ('status' in result or 'steps' in result):
                    logger.debug(f"从大括号结构提取JSON成功")
                    return result
            except json.JSONDecodeError:
                continue
    
    # 方法5: 更复杂的嵌套JSON提取
    # 找到所有的 { 开始位置，然后尝试匹配到对应的 }
    for text in [normalized_text, response_text]:
        start_positions = [i for i, c in enumerate(text) if c == '{']
        for start in reversed(start_positions):  # 从最后一个开始
            brace_count = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            candidate = text[start:i+1]
                            result = json.loads(candidate)
                            if isinstance(result, dict) and ('status' in result or 'steps' in result):
                                logger.debug(f"从嵌套结构提取JSON成功")
                                return result
                        except json.JSONDecodeError:
                            break  # 这个开始位置不行，尝试下一个
                        break
    
    return None

async def _execute_testcase_via_chat_api(result: TestCaseResult):
    from .execution_tracking import finish_execution
    execution = await sync_to_async(lambda: result.execution)()
    testcase = await sync_to_async(lambda: result.testcase)()
    executor = await sync_to_async(lambda: execution.executor)()
    session_id = f"test_exec_{execution.id}_{result.id}_{uuid.uuid4().hex}"
    token = await sync_to_async(lambda: str(RefreshToken.for_user(executor).access_token))()
    payload = {"message": f"执行ID为 {testcase.pk} 的测试用例。按照冻结的步骤逐步操作，并提交结构化最终执行报告。",
        "session_id": session_id, "project_id": str(testcase.project_id), "test_case_id": testcase.pk,
        "execution_result_id": result.pk, "use_knowledge_base": False,
        "generate_playwright_script": execution.generate_playwright_script}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1900, connect=30)) as client:
            async with client.stream('POST', f"{settings.BASE_URL}/api/orchestrator/agent-loop/",
                    json=payload, headers={'Authorization': f'Bearer {token}'}) as response:
                response.raise_for_status()
                async for _ in response.aiter_lines():
                    pass
        await sync_to_async(result.refresh_from_db)()
        if result.status in ('pending', 'running'):
            await sync_to_async(finish_execution)(result.pk, forced='blocked', reason='执行接口未写入完整结果。')
    except Exception as exc:
        await sync_to_async(finish_execution)(result.pk, forced='error', reason=f'执行接口异常：{type(exc).__name__}')
    finally:
        try:
            from mcp_tools.persistent_client import mcp_session_manager
            await mcp_session_manager.cleanup_user_session(
                user_id=str(executor.id), project_id=str(testcase.project_id), session_id=session_id)
        except Exception:
            logger.warning('MCP session cleanup failed for result %s', result.pk, exc_info=True)
        await sync_to_async(result.refresh_from_db)()


@shared_task(name='testcases.cancel_test_execution')
def cancel_test_execution(execution_id):
    from .execution_tracking import cancel_execution
    cancel_execution(execution_id)
    return {'success': True}
