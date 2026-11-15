"""The reads waiting their turn: one is done at a time, and a read can take seconds.

What the player asks for (the hotkey, a snip) and lines from a text hooker go first, in order.
Automatic reads wait behind them, each with the picture taken when it was set off, so a line
that has left the screen by its turn is still read. Two things keep them from piling up when
reads are slower than the game sets them off:

- a waiting picture is let go when a newer one still shows all of its text unchanged: reading
  the newer one shows the same lines, so nothing is lost;
- at most `most_waiting` pictures wait. One more pushes the oldest out, and its lines are lost
  if they were on no later picture: the price of not showing lines ever later.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

MOST_WAITING = 3


class ReadQueue:
    def __init__(self, covers: Callable[[object, object], bool] | None = None, most_waiting: int = MOST_WAITING) -> None:
        """`covers(newer, older)`: whether the newer look still shows all of the older one's
        text (what `put_auto` is given as `seen`)."""
        self._covers = covers
        self._most = most_waiting
        self._asked: deque = deque()
        self._auto: deque[tuple[object, object]] = deque()  # (request, what was seen at that look)
        self._changed = threading.Condition()

    def put(self, request: object) -> None:
        """A read the player asked for, or a hooker's line: ahead of every automatic read."""
        with self._changed:
            self._asked.append(request)
            self._changed.notify()

    def put_auto(self, request: object, seen: object = None) -> tuple[int, int]:
        """An automatic read. Returns how many waiting ones it let go: (shown again by this
        one, pushed out because too many were waiting)."""
        with self._changed:
            covered = 0
            if seen is not None and self._covers is not None:
                kept = deque(pair for pair in self._auto if pair[1] is None or not self._covers(seen, pair[1]))
                covered = len(self._auto) - len(kept)
                self._auto = kept
            self._auto.append((request, seen))
            pushed_out = 0
            while len(self._auto) > self._most:
                self._auto.popleft()
                pushed_out += 1
            self._changed.notify()
            return covered, pushed_out

    def get(self, timeout: float | None = None) -> object:
        """The next read to do; waits for one. Raises TimeoutError if none comes in `timeout` s."""
        with self._changed:
            if not self._changed.wait_for(lambda: self._asked or self._auto, timeout):
                raise TimeoutError
            return self._asked.popleft() if self._asked else self._auto.popleft()[0]

    def __len__(self) -> int:
        with self._changed:
            return len(self._asked) + len(self._auto)
