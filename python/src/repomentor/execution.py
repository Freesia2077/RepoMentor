"""Shared execution boundaries; independent of agent and report orchestration."""

# 提供跨检索、工具与运行时共用的业务错误和取消信号，让中断沿调用链及时传播。

import threading


class HarnessError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()

    # 检索与读取循环在安全边界主动检查取消信号，及时停止后续工作。
    def check(self):
        if self.cancelled:
            raise HarnessError("cancelled", "run cancelled")
