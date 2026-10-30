#!/usr/bin/env python3
"""
bot-kit — a Telegram/Discord-style bot core with payments, admin and persistence.

Stdlib only. Offline-testable via an injected transport and update feed:
    python3 -m unittest test_bot -v

Deliberately structured the way a paid delivery should be:
  * a real state machine for the checkout flow (no ad-hoc flags)
  * SQLite persistence so state survives restarts
  * admin commands (/stats, /broadcast, /orders)
  * payments expressed as an invoice object (crypto invoice link or Telegram Payments)
  * every outbound message goes through an injectable Transport (testable, no network)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ----------------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, username TEXT, is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item TEXT,
    amount TEXT, currency TEXT, status TEXT, invoice TEXT,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS state(
    user_id INTEGER PRIMARY KEY, stage TEXT, data TEXT);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def upsert_user(self, uid: int, username: str, is_admin: bool = False) -> None:
        self.db.execute("INSERT INTO users(id,username,is_admin) VALUES(?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET username=excluded.username",
                        (uid, username, int(is_admin)))
        self.db.commit()

    def is_admin(self, uid: int) -> bool:
        r = self.db.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()
        return bool(r["is_admin"]) if r else False

    def user_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    def add_order(self, uid: int, item: str, amount: str, currency: str,
                  invoice: str) -> int:
        cur = self.db.execute(
            "INSERT INTO orders(user_id,item,amount,currency,status,invoice) "
            "VALUES(?,?,?,?, 'pending', ?)", (uid, item, amount, currency, invoice))
        self.db.commit()
        return cur.lastrowid

    def mark_paid(self, order_id: int) -> None:
        self.db.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
        self.db.commit()

    def orders(self, uid: int | None = None) -> list[dict]:
        q = "SELECT * FROM orders" + (" WHERE user_id=?" if uid is not None else "")
        rows = self.db.execute(q, (uid,) if uid is not None else ()).fetchall()
        return [dict(r) for r in rows]

    def paid_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) c FROM orders WHERE status='paid'").fetchone()["c"]

    def set_stage(self, uid: int, stage: str, data: dict | None = None) -> None:
        self.db.execute("INSERT INTO state(user_id,stage,data) VALUES(?,?,?) "
                        "ON CONFLICT(user_id) DO UPDATE SET stage=excluded.stage,"
                        "data=excluded.data",
                        (uid, stage, json.dumps(data or {})))
        self.db.commit()

    def get_stage(self, uid: int) -> tuple[str, dict]:
        r = self.db.execute("SELECT stage,data FROM state WHERE user_id=?",
                            (uid,)).fetchone()
        if not r:
            return "idle", {}
        return r["stage"], json.loads(r["data"] or "{}")

    def clear_stage(self, uid: int) -> None:
        self.db.execute("DELETE FROM state WHERE user_id=?", (uid,))
        self.db.commit()


# ----------------------------------------------------------------------------
# Transport + messages
# ----------------------------------------------------------------------------
@dataclass
class OutMessage:
    chat_id: int
    text: str
    reply_markup: list[str] = field(default_factory=list)


class Transport:
    """Injectable outbound channel. Tests use MemoryTransport."""

    def send(self, msg: OutMessage) -> None:  # pragma: no cover
        raise NotImplementedError


class MemoryTransport(Transport):
    def __init__(self):
        self.outbox: list[OutMessage] = []

    def send(self, msg: OutMessage) -> None:
        self.outbox.append(msg)


@dataclass
class Update:
    user_id: int
    username: str
    text: str
    is_admin: bool = False


# ----------------------------------------------------------------------------
# Payments
# ----------------------------------------------------------------------------
def make_invoice(order_id: int, amount: str, currency: str,
                 wallet: str = "bc1q-demo-invoice") -> str:
    """Deterministic crypto invoice link for an order (demo)."""
    h = hashlib.sha256(f"{order_id}:{amount}:{currency}".encode()).hexdigest()[:12]
    return f"{wallet}?order={order_id}&amt={amount}&cur={currency}&ref={h}"


# ----------------------------------------------------------------------------
# Bot core
# ----------------------------------------------------------------------------
CATALOG = {"basic": ("Basic plan", "20", "USDT"),
           "pro": ("Pro plan", "50", "USDT")}


class Bot:
    def __init__(self, store: Store, transport: Transport, admin_ids: set[int] | None = None):
        self.store = store
        self.t = transport
        self.admin_ids = set(admin_ids or [])
        self.handlers: dict[str, Callable[[Update], None]] = {
            "/start": self.on_start,
            "/help": self.on_help,
            "/buy": self.on_buy,
            "/orders": self.on_orders,
            "/stats": self.on_stats,
            "/broadcast": self.on_broadcast,
            "/cancel": self.on_cancel,
        }

    # -- helpers
    def say(self, u: Update, text: str, buttons: list[str] | None = None) -> None:
        self.t.send(OutMessage(u.user_id, text, buttons or []))

    def ensure_user(self, u: Update) -> None:
        self.store.upsert_user(u.user_id, u.username,
                               is_admin=(u.user_id in self.admin_ids or u.is_admin))

    # -- dispatch
    def handle(self, u: Update) -> None:
        self.ensure_user(u)
        stage, data = self.store.get_stage(u.user_id)
        # checkout state machine takes priority over commands except /cancel
        if stage == "await_item" and not u.text.startswith("/cancel"):
            return self._checkout_item(u, u.text.strip())
        if stage == "await_confirm" and not u.text.startswith("/cancel"):
            return self._checkout_confirm(u, u.text.strip())
        cmd = u.text.split()[0].lower() if u.text else ""
        fn = self.handlers.get(cmd)
        if fn:
            fn(u)
        else:
            self.say(u, "Unknown command. Try /help")

    # -- commands
    def on_start(self, u: Update) -> None:
        self.say(u, "Welcome! I sell plans. /buy to order, /help for more.",
                 ["/buy", "/help"])

    def on_help(self, u: Update) -> None:
        lines = [f"{k} — {v[0]} ({v[1]} {v[2]})" for k, v in CATALOG.items()]
        self.say(u, "Catalog:\n" + "\n".join(lines) +
                    "\nCommands: /buy /orders /cancel. Admin: /stats /broadcast")

    def on_buy(self, u: Update) -> None:
        self.store.set_stage(u.user_id, "await_item")
        self.say(u, "Which plan? " + ", ".join(CATALOG.keys()), list(CATALOG.keys()))

    def _checkout_item(self, u: Update, item: str) -> None:
        if item not in CATALOG:
            self.say(u, f"No such plan '{item}'. Choose: " + ", ".join(CATALOG.keys()))
            return
        name, amount, cur = CATALOG[item]
        self.store.set_stage(u.user_id, "await_confirm",
                             {"item": item, "amount": amount, "cur": cur})
        self.say(u, f"{name}: {amount} {cur}. Confirm? (yes/no)")

    def _checkout_confirm(self, u: Update, ans: str) -> None:
        _, data = self.store.get_stage(u.user_id)
        if ans.lower() not in ("yes", "y", "да"):
            self.store.clear_stage(u.user_id)
            self.say(u, "Cancelled.")
            return
        oid = self.store.add_order(u.user_id, data["item"], data["amount"],
                                   data["cur"], "")
        inv = make_invoice(oid, data["amount"], data["cur"])
        self.store.db.execute("UPDATE orders SET invoice=? WHERE id=?", (inv, oid))
        self.store.db.commit()
        self.store.mark_paid(oid)  # demo: auto-settle; real flow waits for webhook
        self.store.clear_stage(u.user_id)
        self.say(u, f"Order #{oid} created and settled.\nInvoice: {inv}")

    def on_cancel(self, u: Update) -> None:
        self.store.clear_stage(u.user_id)
        self.say(u, "Cancelled.")

    def on_orders(self, u: Update) -> None:
        rows = self.store.orders(u.user_id)
        if not rows:
            self.say(u, "No orders yet. /buy to start.")
            return
        body = "\n".join(f"#{r['id']} {r['item']} {r['amount']} {r['currency']} "
                         f"[{r['status']}]" for r in rows)
        self.say(u, "Your orders:\n" + body)

    # -- admin
    def _require_admin(self, u: Update) -> bool:
        if not self.store.is_admin(u.user_id):
            self.say(u, "Admin only.")
            return False
        return True

    def on_stats(self, u: Update) -> None:
        if not self._require_admin(u):
            return
        self.say(u, f"users={self.store.user_count()} paid_orders={self.store.paid_count()}")

    def on_broadcast(self, u: Update) -> None:
        if not self._require_admin(u):
            return
        msg = u.text.split(" ", 1)[1] if " " in u.text else ""
        if not msg:
            self.say(u, "Usage: /broadcast <text>")
            return
        rows = self.store.db.execute("SELECT id FROM users").fetchall()
        for r in rows:
            self.t.send(OutMessage(r["id"], f"[announce] {msg}"))
        self.say(u, f"Sent to {len(rows)} users.")
