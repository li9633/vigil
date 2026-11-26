"""文本工具：输出截断。

持久化与证据文本统一使用该函数，避免各处实现长度上限不一致。
"""

DEFAULT_LIMIT = 512


def truncate(text, limit=DEFAULT_LIMIT, suffix="…(截断)"):
    """把文本截断到 limit 个字符；None 视为空串。"""
    if text is None:
        return ""
    text = str(text)
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    return text[:limit] + suffix
