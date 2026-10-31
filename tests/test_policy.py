"""策略层：保留规则、优先级仲裁、默认策略表。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from knowledge_graph import (
    DEFAULT_POLICIES,
    Action,
    PolicyBook,
    Retention,
    RetentionKind,
    RetentionPolicy,
    ValidationError,
    default_book,
)

CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)


class RetentionTests(unittest.TestCase):
    def test_fixed_days_computes_expiry(self):
        retention = Retention(RetentionKind.FIXED_DAYS, days=7)
        self.assertEqual(retention.expire_at(CREATED).day, 8)

    def test_permanent_has_no_expiry(self):
        self.assertIsNone(Retention().expire_at(CREATED))

    def test_until_superseded_has_no_time_expiry(self):
        retention = Retention(RetentionKind.UNTIL_SUPERSEDED)
        self.assertIsNone(retention.expire_at(CREATED))
        self.assertFalse(retention.is_time_based)

    def test_fixed_days_requires_positive_days(self):
        for days in (0, -1):
            with self.subTest(days=days), self.assertRaises(ValidationError):
                Retention(RetentionKind.FIXED_DAYS, days=days)

    def test_non_fixed_kinds_reject_days(self):
        with self.assertRaises(ValidationError):
            Retention(RetentionKind.PERMANENT, days=3)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValidationError):
            Retention("forever")

    def test_describe_is_human_readable(self):
        self.assertIn("7", Retention(RetentionKind.FIXED_DAYS, days=7).describe())


class PolicyBookTests(unittest.TestCase):
    def test_unknown_category_falls_back_to_permanent(self):
        policy = default_book().get("never-heard-of-it")
        self.assertIs(policy.retention.kind, RetentionKind.PERMANENT)
        self.assertIs(policy.action, Action.NONE)
        self.assertEqual(policy.category, "never-heard-of-it")

    def test_resolve_picks_highest_priority(self):
        book = default_book()
        winner = book.resolve(["news", "science"])
        self.assertEqual(winner.category, "science")

    def test_resolve_is_deterministic_on_tie(self):
        book = PolicyBook()
        book.register(RetentionPolicy("b-type", Retention(), Action.NONE, priority=5))
        book.register(RetentionPolicy("a-type", Retention(), Action.NONE, priority=5))
        self.assertEqual(book.resolve(["b-type", "a-type"]).category, "b-type")
        self.assertEqual(book.resolve(["a-type", "b-type"]).category, "b-type")

    def test_resolve_empty_returns_default(self):
        self.assertEqual(default_book().resolve([]).category, "_default")

    def test_register_rejects_wrong_type(self):
        with self.assertRaises(ValidationError):
            PolicyBook().register({"category": "x"})  # type: ignore[arg-type]

    def test_with_overrides_does_not_mutate_original(self):
        book = default_book()
        clone = book.with_overrides(
            {"news": RetentionPolicy("news", Retention(), Action.NONE, priority=99)}
        )
        self.assertIs(book.get("news").action, Action.DELETE)
        self.assertIs(clone.get("news").action, Action.NONE)

    def test_category_required(self):
        with self.assertRaises(ValidationError):
            RetentionPolicy("  ")

    def test_priority_must_be_int(self):
        with self.assertRaises(ValidationError):
            RetentionPolicy("x", priority="high")  # type: ignore[arg-type]

    def test_supersedable_type_cannot_delete_on_expiry(self):
        with self.assertRaises(ValidationError):
            RetentionPolicy(
                "x",
                Retention(RetentionKind.UNTIL_SUPERSEDED),
                Action.DELETE,
                can_be_superseded=True,
            )


class DefaultPolicyTests(unittest.TestCase):
    def test_defaults_cover_all_builtin_types(self):
        self.assertEqual(set(DEFAULT_POLICIES), {"news", "science", "fake_science", "supplement"})

    def test_news_expires_in_seven_days_and_is_deleted(self):
        policy = DEFAULT_POLICIES["news"]
        self.assertIs(policy.retention.kind, RetentionKind.FIXED_DAYS)
        self.assertEqual(policy.retention.days, 7)
        self.assertIs(policy.action, Action.DELETE)

    def test_science_is_permanent(self):
        policy = DEFAULT_POLICIES["science"]
        self.assertIs(policy.retention.kind, RetentionKind.PERMANENT)
        self.assertIs(policy.action, Action.NONE)

    def test_fake_science_is_deprecated_not_deleted(self):
        policy = DEFAULT_POLICIES["fake_science"]
        self.assertIs(policy.action, Action.DEPRECATE)
        self.assertTrue(policy.can_be_superseded)

    def test_only_fake_science_can_be_superseded(self):
        allowed = [c for c, p in DEFAULT_POLICIES.items() if p.can_be_superseded]
        self.assertEqual(allowed, ["fake_science"])

    def test_default_book_is_a_fresh_instance(self):
        book = default_book()
        book.register(RetentionPolicy("news", Retention(), Action.NONE))
        self.assertIs(default_book().get("news").action, Action.DELETE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
