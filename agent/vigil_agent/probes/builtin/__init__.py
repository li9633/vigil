"""内置 probe 集合。"""

from ..base import ProbeRegistry
from . import carrier, command, dns, http, icmp, pppoe, process, route, tcp

BUILTIN_PROBES = (
    carrier.PROBE,
    pppoe.PROBE,
    route.PROBE,
    dns.PROBE,
    http.PROBE,
    tcp.PROBE,
    process.PROBE,
    icmp.PROBE,
    command.PROBE,
)


def builtin_registry():
    """构建包含全部内置 probe 的注册表。"""
    return ProbeRegistry(BUILTIN_PROBES)
