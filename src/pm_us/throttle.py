"""Adaptive pacing: find the venue's real rate limit instead of guessing it.

A fixed pause is wrong in both directions. At 0.6s it never speeds up when the
venue has headroom, and it never slows down when the venue is complaining - it
just fails, backs off, and retries. Measured result: 300 book calls took 15
minutes, 3.0s each against a 0.6s target, because two layers of exponential
backoff compounded on every rate-limited call.

This is AIMD - additive increase, multiplicative decrease - the same control
loop TCP uses for congestion, and for the same reason: the sustainable rate is
unknown, changes, and can only be discovered by probing it.

  success  ->  interval *= 0.97      creep faster
  429      ->  interval *= 1.8       back off hard, immediately

It converges on the fastest pace the venue tolerates rather than on whatever
number seemed safe when the code was written. Recovering 3.0s/call to something
near 0.6s is 5x the coverage per run, which on a cron schedule is 5x the board
scanned for the same cost.
"""

import time


class Throttle:
    def __init__(self, start=0.6, floor=0.06, ceil=8.0, decay=0.97, grow=1.8):
        self.interval = start
        self.floor, self.ceil = floor, ceil
        self.decay, self.grow = decay, grow
        self.calls = self.limited = 0
        self._last = 0.0

    def wait(self):
        gap = time.time() - self._last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last = time.time()

    def ok(self):
        self.calls += 1
        self.interval = max(self.floor, self.interval * self.decay)

    def rate_limited(self):
        self.limited += 1
        self.interval = min(self.ceil, self.interval * self.grow)

    def stats(self):
        return (f"{self.calls} calls, {self.limited} rate-limited "
                f"({self.limited / max(self.calls + self.limited, 1):.0%}), "
                f"pacing {self.interval:.2f}s")


def is_rate_limit(exc):
    s = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in s or "429" in s or "too many" in s


def paced_call(fn, throttle, attempts=3):
    """Call fn() under the throttle, adapting to what the venue says.

    Rate limits are NOT errors here - they are the signal the controller
    exists to consume. Only a genuine failure burns an attempt.
    """
    for i in range(attempts):
        throttle.wait()
        try:
            out = fn()
            throttle.ok()
            return out
        except Exception as e:
            if is_rate_limit(e):
                throttle.rate_limited()
                if i < attempts - 1:
                    continue
            elif i < attempts - 1:
                time.sleep(0.5 * (2 ** i))
                continue
            raise
    return None
