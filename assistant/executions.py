"""The execution registry of D9 on Redis, for the CLI (`core/executions.py` has the protocol).

Synchronous redis-py (`redis.Redis`), because the core that calls it is synchronous; inside the
async turn node each call blocks for milliseconds, a cost accepted in D46 for a one-user CLI.
Each key is `idem:<execution_key>` with a JSON value and a 24 h TTL (D9.1):
- `reserve`: `SET NX GET EX 86400` with `started`. One atomic command: it sets the key only if
  it is new and returns what was there before, so two turns can never both reserve it.
- `complete`: `SET XX EX 86400` with `done` and the result (the 24 h count from the end of the
  run); `XX` never brings back a key that was released or expired meanwhile.
- `release`: `DEL` (D9.3).
A Redis error is not caught here: it reaches the turn like any infrastructure error.
"""

from typing import Any

import redis

from core.executions import TTL_SECONDS, decoded, encoded_done, encoded_started

KEY_PREFIX = "idem:"


class RedisExecutions:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def reserve(self, key: str) -> dict[str, Any] | None:
        previous = self._client.set(
            KEY_PREFIX + key, encoded_started(), nx=True, get=True, ex=TTL_SECONDS
        )
        return decoded(previous)

    def complete(self, key: str, result: dict[str, Any]) -> None:
        self._client.set(KEY_PREFIX + key, encoded_done(result), xx=True, ex=TTL_SECONDS)

    def release(self, key: str) -> None:
        self._client.delete(KEY_PREFIX + key)
