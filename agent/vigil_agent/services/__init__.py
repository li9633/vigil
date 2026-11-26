"""agent 用例服务层。

只依赖领域包（probes/checks/guards/transport/spool/logstore）与运行时基础设施，
不依赖 HTTP 层；`AgentApp` 负责装配与调度，服务负责具体规则与编排。
"""
