"""跨平台文件锁。

生产环境为 OpenWrt（POSIX），使用 fcntl.flock 实现进程间互斥；
开发环境 Windows 下退化到 msvcrt.locking，仅用于本地测试。
"""

import os

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None


class FileLock:
    """非阻塞排他锁；acquire() 返回是否成功。"""

    def __init__(self, path):
        self.path = path
        self._handle = None
        self.acquired = False

    def acquire(self):
        if self.acquired:
            return True
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            handle = open(self.path, "a+b")
        except OSError:
            return False

        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
            except OSError:
                self.acquired = False
        elif msvcrt is not None:
            try:
                # Windows 的字节锁要求文件至少有 1 字节
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.acquired = True
            except OSError:
                self.acquired = False
        else:
            # 无可用锁实现时退化为进程内互斥，至少不阻塞启动
            self.acquired = True

        if not self.acquired:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self):
        if not self._handle:
            return
        try:
            if fcntl is not None and self.acquired:
                try:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            elif msvcrt is not None and self.acquired:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            try:
                self._handle.close()
            finally:
                self._handle = None
                self.acquired = False

    def __enter__(self):
        return self if self.acquire() else None

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
