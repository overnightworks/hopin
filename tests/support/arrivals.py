"""The sessions a fake service holds, in the order they arrived, and the wait for the next one."""

import threading


class Arrivals[Item](list[Item]):
    """A list that a service thread fills and a test thread waits on."""

    def __init__(self, wait_seconds: float) -> None:
        super().__init__()
        self._wait_seconds = wait_seconds
        self._arrived = threading.Condition()

    def arrive(self, item: Item) -> None:
        with self._arrived:
            self.append(item)
            self._arrived.notify_all()

    def wait_for(self, number: int) -> Item:
        """Block until `number` items arrived, and answer that one."""
        with self._arrived:
            assert self._arrived.wait_for(lambda: len(self) >= number, self._wait_seconds), "no session came"
            return self[number - 1]
