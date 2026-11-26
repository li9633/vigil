"""通用校验：Check id、依赖闭包与拓扑排序。"""

import re

CHECK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ValidationError(ValueError):
    """配置校验失败。"""


def is_check_id(value):
    return isinstance(value, str) and bool(CHECK_ID_RE.match(value))


def validate_check_id(value):
    if not is_check_id(value):
        raise ValidationError("非法的检测项 id：%r" % (value,))
    return value


def dependency_closure(roots, graph):
    """返回 roots 及其全部传递依赖，保持首次出现顺序。"""
    result = []
    seen = set()

    def visit(node):
        if node in seen:
            return
        seen.add(node)
        result.append(node)
        for dep in graph.get(node, ()):
            visit(dep)

    for root in roots:
        visit(root)
    return result


def topological_sort(graph):
    """依赖优先排序；发现环时抛 ValidationError。"""
    order = []
    state = {}

    def visit(node, stack):
        status = state.get(node)
        if status == "done":
            return
        if status == "visiting":
            cycle = " -> ".join(stack + [node])
            raise ValidationError("检测项依赖存在环：%s" % cycle)
        state[node] = "visiting"
        for dep in graph.get(node, ()):
            visit(dep, stack + [node])
        state[node] = "done"
        order.append(node)

    for node in graph:
        visit(node, [])
    return order
