"""Shared execution boundaries; independent of agent and report orchestration."""

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

    def check(self):
        if self.cancelled:
            raise HarnessError("cancelled", "run cancelled")
