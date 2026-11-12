# Paddle notification fixtures

Shaped after the example payloads in Paddle's public webhook documentation (Billing API,
`transaction.completed`, `subscription.updated`, `subscription.canceled`, `subscription.created`,
`adjustment.created`). Every id, the owner UUID and every amount are made up; nothing here came
from a real Paddle account. Used by `tests/test_paddle_events.py`; the signature tests sign them
with a test secret. Check them against the sandbox once a key exists (ADR 0032, Alternatives).
