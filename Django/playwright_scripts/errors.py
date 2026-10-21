"""Actionable provider errors without exposing response bodies or credentials."""


def model_error_message(exc):
    status = getattr(exc, 'status_code', None)
    messages = {
        401: '模型服务认证失败（401）；请检查平台中的 API Key。',
        402: '模型服务余额或额度不足（402）；恢复额度后可重新生成，已有脚本和执行记录保留。',
        403: '模型服务拒绝访问（403）；请检查该模型的调用权限。',
        429: '模型服务限流或配额受限（429）；请检查配额，稍后重新生成。',
    }
    if status in messages:
        return messages[status]
    if isinstance(status, int) and 500 <= status <= 599:
        return f'模型服务暂时异常（{status}）；稍后可重新生成，已有记录保留。'
    return f'模型调用未完成（{type(exc).__name__}）；请检查模型服务后重新生成。'
