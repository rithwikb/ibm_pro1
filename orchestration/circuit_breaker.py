"""
orchestration/circuit_breaker.py — Resilient Circuit Breaker & API Fallback Engine
Guarantees 100% demo uptime by intercepting network errors, timeouts, or token limits
and gracefully routing to deterministic high-fidelity generators.
"""

from __future__ import annotations

import time
from typing import Callable, Any

# Circuit Breaker States
STATE_CLOSED = "CLOSED"        # Normal operation: route to live LLM API
STATE_OPEN = "OPEN"            # Failure tripped: route directly to resilient fallback
STATE_HALF_OPEN = "HALF_OPEN"  # Trialing recovery


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 2, recovery_timeout_s: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = STATE_CLOSED
        self.fallback_invocations = 0

    def call(self, primary_fn: Callable[[], Any], fallback_fn: Callable[[], Any]) -> tuple[Any, bool]:
        """
        Execute primary_fn safely. If it fails or if circuit is OPEN, execute fallback_fn.
        Returns: (result, used_fallback: bool)
        """
        now = time.time()

        # Check if circuit should transition from OPEN -> HALF_OPEN
        if self.state == STATE_OPEN:
            if now - self.last_failure_time > self.recovery_timeout_s:
                self.state = STATE_HALF_OPEN
            else:
                self.fallback_invocations += 1
                return fallback_fn(), True

        try:
            result = primary_fn()
            # Success in HALF_OPEN resets to CLOSED
            if self.state == STATE_HALF_OPEN:
                self.state = STATE_CLOSED
                self.failure_count = 0
            return result, False
        except Exception:
            self.failure_count += 1
            self.last_failure_time = now
            if self.failure_count >= self.failure_threshold:
                self.state = STATE_OPEN
            self.fallback_invocations += 1
            return fallback_fn(), True

    def get_status(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "fallback_invocations": self.fallback_invocations,
            "is_resilient": True,
        }


# Global singleton circuit breaker
_global_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    return _global_breaker
