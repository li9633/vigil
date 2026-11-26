"""文件与路径工具：目录创建、原子写、权限与防目录穿越。"""

import os
import tempfile


def ensure_dir(path, mode=0o755):
    if not path:
        return False
    try:
        os.makedirs(path, mode=mode, exist_ok=True)
        return True
    except OSError:
        return False


def secure_file(path, mode=0o600):
    try:
        os.chmod(path, mode)
        return True
    except OSError:
        return False


def atomic_write(path, data, mode=0o600, encoding="utf-8"):
    """同目录临时文件 + fsync + replace，避免写入中断留下半文件。"""
    target = os.path.abspath(path)
    directory = os.path.dirname(target)
    ensure_dir(directory)
    fd, temp_path = tempfile.mkstemp(prefix=".vigil-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
        return target
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def safe_join(root, *parts):
    """把相对路径拼到 root 下，并阻止 ../ 与符号链接逃逸。"""
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, *parts))
    try:
        common = os.path.commonpath([root_real, target])
    except ValueError:
        raise ValueError("路径不在允许目录内") from None
    if common != root_real:
        raise ValueError("路径不在允许目录内")
    return target
