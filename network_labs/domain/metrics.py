import time


class TransferMetrics:
    def __init__(self) -> None:
        self._bytes = 0
        self._start = 0.0
        self._end = 0.0

    def start(self) -> None:
        self._start = time.time()
        self._bytes = 0

    def add_bytes(self, count: int) -> None:
        self._bytes += count

    def stop(self) -> None:
        self._end = time.time()

    @property
    def elapsed(self) -> float:
        return max(self._end - self._start, 0.001)

    @property
    def bitrate_mbps(self) -> float:
        return (self._bytes * 8) / (self.elapsed * 1_000_000)

    def summary(self) -> str:
        return (
            f"Transferred {self._bytes} bytes "
            f"in {self.elapsed:.2f}s "
            f"({self.bitrate_mbps:.2f} Mbps)"
        )
