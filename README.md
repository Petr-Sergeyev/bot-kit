# bot-kit

A Telegram/Discord-style bot core with a real checkout state machine, payments, admin
commands and SQLite persistence. **Stdlib only.**

```bash
python3 -m unittest test_bot -v   # 16 offline tests
```

Companion samples:
[`scraper-kit`](https://github.com/Petr-Sergeyev/scraper-kit) ·
[`integration-kit`](https://github.com/Petr-Sergeyev/integration-kit)

## What it provides

| Capability | Where | Test |
|---|---|---|
| Checkout as an explicit state machine (`idle → await_item → await_confirm → done`) | `Bot.handle`, `Store.set_stage` | `TestCheckoutStateMachine` |
| State survives restarts / new bot instances (SQLite) | `Store` | `test_state_survives_new_bot_instance` |
| Per-user order isolation | `Store.orders(uid)` | `test_orders_lists_own_only` |
| Payments as an invoice object (crypto invoice link, deterministic) | `make_invoice` | `TestInvoice` |
| Admin gating (`/stats`, `/broadcast`) | `_require_admin` | `TestAdmin` |
| Broadcast to all registered users | `on_broadcast` | `test_broadcast_reaches_all_users` |
| Injectable outbound transport (testable, no network) | `Transport`, `MemoryTransport` | all tests |
| `/cancel` exits any state cleanly | `on_cancel` | `test_cancel_exits_state` |

## Why a client cares

Most delivered bots are a pile of `if text == ...` branches with global variables; they break
the moment two users talk at once or the process restarts. This one keeps per-user state in a
database, models checkout as transitions, and isolates admin surface behind a check — so it
behaves the same in production as in the test suite.

## Contract scope

Included: command/callback routing, state machine for your sales or onboarding flow,
payments (Telegram Payments or a crypto invoice link of your choice), admin panel
(broadcast / users / orders / metrics), SQLite or Postgres persistence, tests for the core
flows, README with deploy steps, live demo bot.

Not included: hosting costs (deploy to your VPS/fly.io/railway), and any payment flow that
would require me to hold your funds.

## Tests

```
python3 -m unittest test_bot -v
# Ran 16 tests ... OK
```

All offline: updates are fed in as plain objects and outbound messages are captured by a
memory transport, so flows are asserted deterministically.

## Licence

MIT.
