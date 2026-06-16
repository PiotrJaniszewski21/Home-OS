import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, max_attempts=5, window_seconds=900):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._attempts = defaultdict(list)

    def is_limited(self, key):
        now = time.time()
        attempts = self._attempts[key]
        self._attempts[key] = [t for t in attempts if now - t < self.window]
        return len(self._attempts[key]) >= self.max_attempts

    def record(self, key):
        self._attempts[key].append(time.time())

    def reset(self, key):
        self._attempts.pop(key, None)


login_limiter = RateLimiter(max_attempts=5, window_seconds=900)
