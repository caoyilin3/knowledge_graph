"""模型层：时间归一化、旧状态兼容、字段校验。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from knowledge_graph import ContentItem, Status, ValidationError, as_utc


def make_item(**overrides) -> ContentItem:
    payload = dict(id="item-001", title="标题", content="正文", content_type="news")
    payload.update(overrides)
    return ContentItem(**payload)


class ContentItemTests(unittest.TestCase):
    def test_naive_datetime_is_interpreted_as_utc(self):
        item = make_item(created_at=datetime(2026, 1, 1, 8, 0))
        self.assertEqual(item.created_at.tzinfo, timezone.utc)
        self.assertEqual(item.created_at.hour, 8)

    def test_aware_datetime_is_converted_to_utc(self):
        tz = timezone(timedelta(hours=8))
        item = make_item(created_at=datetime(2026, 1, 1, 8, 0, tzinfo=tz))
        self.assertEqual(item.created_at.hour, 0)

    def test_legacy_superseded_status_maps_to_deprecated(self):
        payload = make_item().to_dict()
        payload["status"] = "superseded"
        restored = ContentItem.from_dict(payload)
        self.assertIs(restored.status, Status.DEPRECATED)

    def test_to_dict_roundtrip(self):
        item = make_item(
            tags=["AI", "模型"],
            expire_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            superseded_by="item-002",
        )
        restored = ContentItem.from_dict(item.to_dict())
        self.assertEqual(restored.to_dict(), item.to_dict())

    def test_invalid_id_rejected(self):
        for bad in ("../etc/passwd", "", "有中文", "-leading-dash"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                make_item(id=bad)

    def test_self_supersede_rejected(self):
        with self.assertRaises(ValidationError):
            make_item(superseded_by="item-001")

    def test_self_corrects_rejected(self):
        with self.assertRaises(ValidationError):
            make_item(corrects="item-001")

    def test_empty_title_rejected(self):
        with self.assertRaises(ValidationError):
            make_item(title="   ")

    def test_duplicate_tags_are_deduplicated(self):
        item = make_item(tags=["a", "a", " ", "b"])
        self.assertEqual(item.tags, ["a", "b"])

    def test_metadata_must_be_json_serializable(self):
        with self.assertRaises(ValidationError):
            make_item(metadata={"when": datetime(2026, 1, 1, tzinfo=timezone.utc)})

    def test_metadata_keys_must_be_strings(self):
        with self.assertRaises(ValidationError):
            make_item(metadata={1: "x"})

    def test_too_many_tags_rejected(self):
        with self.assertRaises(ValidationError):
            make_item(tags=[f"tag-{i}" for i in range(101)])

    def test_too_long_tag_rejected(self):
        with self.assertRaises(ValidationError):
            make_item(tags=["x" * 101])

    def test_is_expired_boundary(self):
        moment = datetime(2026, 6, 1, tzinfo=timezone.utc)
        item = make_item(expire_at=moment)
        self.assertTrue(item.is_expired(moment))
        self.assertFalse(item.is_expired(moment - timedelta(seconds=1)))
        self.assertFalse(make_item().is_expired(moment))

    def test_missing_required_field_rejected(self):
        with self.assertRaises(ValidationError):
            ContentItem.from_dict({"id": "x", "title": "t"})

    def test_unknown_fields_ignored_for_forward_compat(self):
        payload = make_item().to_dict()
        payload["future_field"] = 1
        self.assertEqual(ContentItem.from_dict(payload).id, "item-001")

    def test_as_utc_rejects_non_datetime(self):
        with self.assertRaises(ValidationError):
            as_utc("2026-01-01")  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
