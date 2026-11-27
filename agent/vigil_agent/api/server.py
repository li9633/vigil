"""本地 HTTP 服务装配：线程化服务器与依赖注入入口。"""

import threading
from http.server import ThreadingHTTPServer

from .handler import VigilRequestHandler


class VigilHTTPServer(ThreadingHTTPServer):
    """携带配置与内存会话的 HTTP 服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        token,
        www_root,
        status_provider=None,
        checks_provider=None,
        checks_handler=None,
        events_provider=None,
        logs_provider=None,
        action_handler=None,
        session_ttl=43200,
        login_max_failures=5,
        login_window=60,
        guards_provider=None,
        guards_handler=None,
        settings_provider=None,
        settings_handler=None,
        pairing_start_handler=None,
        pairing_poll_handler=None,
        pairing_cancel_handler=None,
        logs_export_provider=None,
        probes_provider=None,
        probes_install_handler=None,
        probes_delete_handler=None,
        probes_test_handler=None,
        checks_test_handler=None,
    ):
        super().__init__(address, VigilRequestHandler)
        self.token = token or ""
        self.www_root = www_root
        self.status_provider = status_provider
        self.checks_provider = checks_provider
        self.checks_handler = checks_handler
        self.events_provider = events_provider
        self.logs_provider = logs_provider
        self.action_handler = action_handler
        self.guards_provider = guards_provider
        self.guards_handler = guards_handler
        self.settings_provider = settings_provider
        self.settings_handler = settings_handler
        self.pairing_start_handler = pairing_start_handler
        self.pairing_poll_handler = pairing_poll_handler
        self.pairing_cancel_handler = pairing_cancel_handler
        self.logs_export_provider = logs_export_provider
        self.probes_provider = probes_provider
        self.probes_install_handler = probes_install_handler
        self.probes_delete_handler = probes_delete_handler
        self.probes_test_handler = probes_test_handler
        self.checks_test_handler = checks_test_handler
        self.session_ttl = int(session_ttl)
        self.login_max_failures = int(login_max_failures)
        self.login_window = int(login_window)
        self.sessions = {}
        self.login_failures = {}
        self.lock = threading.Lock()


def create_server(
    bind,
    port,
    token,
    www_root,
    status_provider=None,
    checks_provider=None,
    checks_handler=None,
    events_provider=None,
    logs_provider=None,
    action_handler=None,
    session_ttl=43200,
    login_max_failures=5,
    login_window=60,
    guards_provider=None,
    guards_handler=None,
    settings_provider=None,
    settings_handler=None,
    pairing_start_handler=None,
    pairing_poll_handler=None,
    pairing_cancel_handler=None,
    logs_export_provider=None,
    probes_provider=None,
    probes_install_handler=None,
    probes_delete_handler=None,
    probes_test_handler=None,
    checks_test_handler=None,
):
    """创建（未启动的）HTTP 服务实例。"""
    return VigilHTTPServer(
        (bind, int(port)),
        token,
        www_root,
        status_provider=status_provider,
        checks_provider=checks_provider,
        checks_handler=checks_handler,
        events_provider=events_provider,
        logs_provider=logs_provider,
        action_handler=action_handler,
        session_ttl=session_ttl,
        login_max_failures=login_max_failures,
        login_window=login_window,
        guards_provider=guards_provider,
        guards_handler=guards_handler,
        settings_provider=settings_provider,
        settings_handler=settings_handler,
        pairing_start_handler=pairing_start_handler,
        pairing_poll_handler=pairing_poll_handler,
        pairing_cancel_handler=pairing_cancel_handler,
        logs_export_provider=logs_export_provider,
        probes_provider=probes_provider,
        probes_install_handler=probes_install_handler,
        probes_delete_handler=probes_delete_handler,
        probes_test_handler=probes_test_handler,
        checks_test_handler=checks_test_handler,
    )
