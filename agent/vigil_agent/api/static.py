"""本地 UI 静态资源：占位页与安全路径解析。"""

import os
import urllib.parse

# 提示：状态不能只靠颜色，占位页同样用文字说明
PLACEHOLDER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vigil 客户端</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
         background: #0f172a; color: #e2e8f0; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; }
  main { max-width: 480px; padding: 32px; }
  h1 { font-size: 22px; margin: 0 0 12px; }
  p { line-height: 1.7; color: #94a3b8; margin: 0 0 10px; }
  code { background: #1e293b; padding: 2px 6px; border-radius: 4px; color: #cbd5e1; }
</style>
</head>
<body>
<main>
  <h1>Vigil 客户端 UI 未安装</h1>
  <p>agent 已运行，但未找到前端静态资源目录
     <code>/usr/share/vigil/www</code>。</p>
  <p>请安装完整发行包，或把前端构建产物放到该目录后刷新页面。</p>
  <p>本地 API 仍可正常使用。</p>
</main>
</body>
</html>
"""


def resolve_static_path(root, request_path):
    """把请求路径解析为 www_root 下的真实文件。

    返回 (path, error)：error 为 "forbidden" / "not_found" / None。
    SPA 深链接（无扩展名）回退到 index.html；带扩展名的资源仍 404。
    """
    relative = urllib.parse.unquote(request_path.lstrip("/"))
    if not relative:
        relative = "index.html"
    elif relative.endswith("/"):
        relative += "index.html"

    root_real = os.path.realpath(root)
    full_path = os.path.realpath(os.path.join(root_real, relative))
    if full_path != root_real and not full_path.startswith(root_real + os.sep):
        return None, "forbidden"
    if os.path.isdir(full_path):
        full_path = os.path.join(full_path, "index.html")
    if not os.path.isfile(full_path):
        index_path = os.path.join(root_real, "index.html")
        if "." not in os.path.basename(relative) and os.path.isfile(index_path):
            return index_path, None
        return None, "not_found"
    return full_path, None
