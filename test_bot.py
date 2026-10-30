#!/usr/bin/env python3
"""Offline tests for bot-kit. python3 -m unittest test_bot -v"""
import unittest

from bot import Bot, MemoryTransport, Store, Update, make_invoice


def mk(uid=1, name="u1", text="/start", admin=False):
    return Update(user_id=uid, username=name, text=text, is_admin=admin)


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.t = MemoryTransport()
        self.bot = Bot(self.store, self.t, admin_ids={99})

    def last(self):
        return self.t.outbox[-1].text

    def feed(self, *texts, uid=1, admin=False):
        for t in texts:
            self.bot.handle(mk(uid=uid, text=t, admin=admin))


class TestBasics(Base):
    def test_start_registers_user_and_greets(self):
        self.feed("/start")
        self.assertIn("Welcome", self.last())
        self.assertEqual(self.store.user_count(), 1)

    def test_help_lists_catalog(self):
        self.feed("/help")
        self.assertIn("basic", self.last())
        self.assertIn("pro", self.last())

    def test_unknown_command(self):
        self.feed("/nope")
        self.assertIn("Unknown", self.last())


class TestCheckoutStateMachine(Base):
    def test_full_flow_creates_paid_order(self):
        self.feed("/buy", "pro", "yes")
        orders = self.store.orders(1)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "paid")
        self.assertIn("Invoice:", self.last())
        self.assertIn("order=1", self.last())

    def test_bad_item_stays_in_state(self):
        self.feed("/buy", "nope")
        self.assertIn("No such plan", self.last())
        stage, _ = self.store.get_stage(1)
        self.assertEqual(stage, "await_item")

    def test_no_confirms_cancels(self):
        self.feed("/buy", "basic", "no")
        self.assertIn("Cancelled", self.last())
        self.assertEqual(self.store.orders(1), [])
        stage, _ = self.store.get_stage(1)
        self.assertEqual(stage, "idle")

    def test_cancel_exits_state(self):
        self.feed("/buy", "/cancel")
        stage, _ = self.store.get_stage(1)
        self.assertEqual(stage, "idle")

    def test_state_survives_new_bot_instance(self):
        self.feed("/buy", "pro")
        b2 = Bot(self.store, self.t, admin_ids={99})
        b2.handle(mk(text="yes"))
        self.assertEqual(len(self.store.orders(1)), 1)


class TestOrders(Base):
    def test_orders_empty(self):
        self.feed("/orders")
        self.assertIn("No orders", self.last())

    def test_orders_lists_own_only(self):
        self.feed("/buy", "basic", "yes", uid=1)
        self.feed("/buy", "pro", "yes", uid=2)
        self.t.outbox.clear()
        self.feed("/orders", uid=1)
        self.assertIn("basic", self.last())
        self.assertNotIn("pro", self.last())


class TestAdmin(Base):
    def test_stats_denied_for_non_admin(self):
        self.feed("/stats", uid=5)
        self.assertIn("Admin only", self.last())

    def test_stats_ok_for_admin(self):
        self.feed("/buy", "basic", "yes", uid=99, admin=True)
        self.t.outbox.clear()
        self.feed("/stats", uid=99, admin=True)
        self.assertIn("users=", self.last())
        self.assertIn("paid_orders=1", self.last())

    def test_broadcast_reaches_all_users(self):
        self.feed("/start", uid=1)
        self.feed("/start", uid=2)
        self.t.outbox.clear()
        self.feed("/broadcast hello", uid=99, admin=True)
        announced = [m for m in self.t.outbox if m.text.startswith("[announce]")]
        # reaches every registered user, including the admin who sent it
        self.assertEqual(sorted(m.chat_id for m in announced), [1, 2, 99])

    def test_broadcast_requires_text(self):
        self.feed("/broadcast", uid=99, admin=True)
        self.assertIn("Usage:", self.last())


class TestInvoice(Base):
    def test_invoice_deterministic(self):
        a = make_invoice(7, "20", "USDT")
        b = make_invoice(7, "20", "USDT")
        self.assertEqual(a, b)

    def test_invoice_changes_with_amount(self):
        self.assertNotEqual(make_invoice(7, "20", "USDT"),
                            make_invoice(7, "50", "USDT"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
