"""The Postgres every HopIn process shares (`postgres.py`: the engine, `hopin migrate`, the start's check that the
schema is the image's head and the readiness check; `schema.py`: the tables, which `migrations/` writes out literally),
and the Redis every HopIn process shares (`redis.py`), the async client the connector's place store runs on and the
`SharedStores` port `create_app` takes (`shared.py`: the rate-limit backend, whether the store answers and the async
client), and the accounts and sessions (`identity.py`: the `IdentityStores` port `create_app` takes; `accounts.py`:
the `accounts` table behind webauth's `SubjectUserStore`; `postgres.py` builds the port over the engine, with webauth's
Postgres session store).

The rate limit windows live in that Redis (webauth's `RedisRateLimitBackend`), so a budget spent through one process
is refused through the others; production never builds webauth's single-process backend, which only the in-thread
tests pass behind the same port. Redis's clock decides the window. A Redis that does not answer within the client's
one-second timeouts fails a budgeted request closed with `503` and `Retry-After`, and `/health` is not ready.

The accounts and sessions outlast a restart and every process admits the same session; one transaction under the
sign-in lock erases a person's sessions and account (`identity.py`: `erase`). Rooms, seats and replay logs live in
process memory. The windows expire with their window (at most 10 minutes) and hold a counted address and no
name. The agent connector's member-place records in the same Redis hold a principal and a resource address, never
content, and lapse within a listen's bound. What HopIn holds about a person, and what an admin may download, is in the
README under A Person's Data.
"""
