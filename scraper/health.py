"""Collection health tracks successful device reads, never cached exports."""

import threading
import time


class CollectionHealth:
    def __init__(self):
        self._sources = {}
        self._lock = threading.Lock()

    def register(self, collector, sources, interval):
        with self._lock:
            for source in sources:
                # Zero means this source has never successfully returned metrics.
                self._sources[(collector, source)] = (0.0, max(180, 3 * interval))

    def success(self, collector, source):
        with self._lock:
            key = (collector, source)
            _, threshold = self._sources[key]
            self._sources[key] = (time.time(), threshold)

    def snapshot(self):
        with self._lock:
            return dict(self._sources)
