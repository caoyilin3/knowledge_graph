"""核心引擎：生命周期 + 纠错链。

相比 v1 的三处关键变化
----------------------
1. **被取代不再等于被删除**：``superseded`` 状态改为 ``deprecated``，条目保留，
   纠错链可查；只有 ``news`` 这类低价值类型才会被真正删除。
2. **清理动作由策略决定**：``delete / archive / deprecate / none`` 四种，而不是
   写死一个 ``store.delete``。
3. **清理可预演**：``cleanup(dry_run=True)`` 返回将要发生的事，不落盘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

from .audit import AuditLog
from .errors import ConflictError, NotFoundError, ValidationError
from .models import ContentItem, Status, as_utc, require_text, utc_now
from .policy import Action, PolicyBook, default_book
from .stores.base import BaseStore

#: 允许通过 ``update()`` 修改的字段。id / created_at / superseded_by / corrects
#: 都不在其中——要改关系请用 ``supersede()`` / ``restore()``。
MUTABLE_FIELDS = ("title", "content", "tags", "metadata", "status", "expire_at", "content_type")


@dataclass
class CleanupReport:
    """一次清理的结果。``deleted`` 等字段是条目 id 列表，便于逐条核对。"""

    deleted: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    total_active: int = 0
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.deleted or self.archived or self.deprecated or self.expired)

    @property
    def handled(self) -> int:
        return len(self.deleted) + len(self.archived) + len(self.deprecated) + len(self.expired)

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": list(self.deleted),
            "archived": list(self.archived),
            "deprecated": list(self.deprecated),
            "expired": list(self.expired),
            "handled": self.handled,
            "total_active": self.total_active,
            "dry_run": self.dry_run,
        }


class Engine:
    """知识条目的生命周期与纠错引擎。"""

    def __init__(
        self,
        store: BaseStore,
        *,
        policies: PolicyBook | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        if not isinstance(store, BaseStore):
            raise TypeError("store 必须实现 BaseStore")
        self.store = store
        self.policies = policies or default_book()
        self.audit = audit if audit is not None else AuditLog()

    # ------------------------------------------------------------------ 新增

    def add(
        self,
        item_id: str,
        title: str,
        content: str,
        content_type: str,
        *,
        tags: Iterable[str] | None = None,
        corrects: str | None = None,
        created_at: Any = None,
        metadata: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> ContentItem:
        """新增一条内容。

        ``corrects`` 指向一条 ``fake_science`` 条目时，会同步建立纠错链：
        被纠正的条目立即按策略处理（默认 ``deprecate``，保留可追溯）。
        """
        existing = self.store.get(item_id)
        if existing is not None:
            if not overwrite:
                raise ConflictError(f"条目已存在: {item_id}（需要覆盖请显式 overwrite=True）")
            # 覆盖一个仍在纠错链上的条目会把链拆成两半（反向指针悬空、旧关系丢失），
            # 所以这里不做静默覆盖，要求调用方先显式拆链。
            linked = (
                existing.superseded_by
                or existing.corrects
                or any(other.corrects == item_id for other in self.store.list_all())
            )
            if linked:
                raise ConflictError(
                    f"{item_id} 参与纠错链，不能用 overwrite 覆盖；"
                    "请先 restore() 或 delete(force=True) 拆链"
                )

        category = require_text(content_type, "content_type", max_length=64)
        policy = self.policies.get(category)
        created = as_utc(created_at) if created_at is not None else utc_now()

        target: ContentItem | None = None
        if corrects is not None:
            target = self._require(corrects)
            self._validate_supersede(target, item_id, force=overwrite)

        item = ContentItem(
            id=item_id,
            title=title,
            content=content,
            content_type=category,
            created_at=created,
            expire_at=policy.retention.expire_at(created),
            tags=list(tags or ()),
            metadata=metadata or {},
        )
        self.store.save(item)
        self.audit.log("create", item.id, f"创建 {item.content_type}: {item.title}")

        if target is not None:
            self._link_supersede(target, item, now=utc_now())
        return item

    # ------------------------------------------------------------------ 读取

    def get(self, item_id: str) -> ContentItem | None:
        return self.store.get(item_id)

    def _require(self, item_id: str) -> ContentItem:
        item = self.store.get(item_id)
        if item is None:
            raise NotFoundError(f"条目不存在: {item_id}")
        return item

    def list_active(self, content_type: str | None = None) -> list[ContentItem]:
        """默认视图：只有 active，按创建时间倒序。"""
        filters: dict[str, Any] = {"status": Status.ACTIVE}
        if content_type is not None:
            filters["content_type"] = content_type
        items = self.store.query(**filters)
        return sorted(items, key=lambda i: i.created_at, reverse=True)

    def list_by_type(self, content_type: str, *, include_hidden: bool = False) -> list[ContentItem]:
        filters: dict[str, Any] = {"content_type": content_type}
        if not include_hidden:
            filters["status"] = Status.ACTIVE
        return sorted(self.store.query(**filters), key=lambda i: i.created_at, reverse=True)

    def search(
        self,
        keyword: str,
        *,
        include_hidden: bool = False,
        match: str = "all",
        ranked: bool = False,
    ) -> list[ContentItem]:
        """标题 / 正文 / 标签的匹配（不区分大小写）。

        空白分隔的多个词默认要求**全部命中**（``match="all"``）；``match="any"``
        表示命中任一即可。``ranked=True`` 时按相关度排序（标题 > 标签 > 正文），
        否则按创建时间倒序。
        """
        phrase = require_text(keyword, "keyword", max_length=200).strip().lower()
        terms = [term for term in phrase.split() if term]
        if match not in ("all", "any"):
            raise ValidationError("match 只能是 'all' 或 'any'")

        scored: list[tuple[float, ContentItem]] = []
        for item in self.store.list_all():
            if not include_hidden and item.status is not Status.ACTIVE:
                continue
            score = self._match_score(item, phrase, terms, match)
            if score is not None:
                scored.append((score, item))

        if ranked:
            scored.sort(key=lambda pair: (-pair[0], -pair[1].created_at.timestamp()))
        else:
            scored.sort(key=lambda pair: pair[1].created_at, reverse=True)
        return [item for _, item in scored]

    @staticmethod
    def _match_score(
        item: ContentItem, phrase: str, terms: list[str], match: str
    ) -> float | None:
        title = item.title.lower()
        content = item.content.lower()
        tags = [tag.lower() for tag in item.tags]

        scores: list[float] = []
        for term in terms:
            title_hit = term in title
            tag_hit = any(term in tag for tag in tags)
            content_hit = term in content
            if not (title_hit or tag_hit or content_hit):
                if match == "all":
                    return None
                scores.append(0.0)
                continue
            scores.append(5.0 * title_hit + 3.0 * tag_hit + 1.0 * content_hit)

        if not any(scores):
            return None
        score = sum(scores)
        if phrase and phrase in title:
            score += 10.0
        elif phrase and phrase in content:
            score += 3.0
        return score

    def list_by_tags(
        self,
        tags: Iterable[str],
        *,
        match_all: bool = True,
        include_hidden: bool = False,
        content_type: str | None = None,
    ) -> list[ContentItem]:
        """按标签筛选。``match_all=False`` 时命中任一标签即可。"""
        wanted = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
        if not wanted:
            return []

        results: list[ContentItem] = []
        for item in self.store.list_all():
            if not include_hidden and item.status is not Status.ACTIVE:
                continue
            if content_type is not None and item.content_type != content_type:
                continue
            item_tags = {tag.lower() for tag in item.tags}
            hit = wanted <= item_tags if match_all else bool(wanted & item_tags)
            if hit:
                results.append(item)
        return sorted(results, key=lambda i: i.created_at, reverse=True)

    def tag_counts(self, *, include_hidden: bool = False) -> dict[str, int]:
        """标签使用次数，按次数倒序、同名按字母序。"""
        counts: dict[str, int] = {}
        for item in self.store.list_all():
            if not include_hidden and item.status is not Status.ACTIVE:
                continue
            for tag in item.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower())))

    # ------------------------------------------------------------------ 更新

    def update(self, item_id: str, **fields: Any) -> ContentItem:
        """按白名单更新字段；改 ``content_type`` 会按新策略重算过期时间。"""
        unknown = set(fields) - set(MUTABLE_FIELDS)
        if unknown:
            raise ValidationError(f"不可更新字段: {sorted(unknown)}；可改: {list(MUTABLE_FIELDS)}")
        if not fields:
            raise ValidationError("update() 至少需要一个字段")

        item = self._require(item_id)
        if "content_type" in fields and item.superseded_by:
            raise ConflictError(
                f"{item_id} 已被 {item.superseded_by} 取代，不能修改类型；请先 restore()"
            )
        # store 返回的是对象引用；中途任何校验失败都不能留下半改状态。
        snapshot = item.copy()
        changes: dict[str, str] = {}
        try:
            return self._apply_update(item, fields, changes)
        except Exception:
            item.__dict__.update(snapshot.__dict__)
            raise

    def _apply_update(
        self, item: ContentItem, fields: dict[str, Any], changes: dict[str, str]
    ) -> ContentItem:
        if "title" in fields:
            item.title = require_text(fields["title"], "title", max_length=500)
            changes["title"] = item.title
        if "content" in fields:
            item.content = require_text(
                fields["content"], "content", max_length=200_000, allow_empty=True
            )
            changes["content"] = f"<{len(item.content)} chars>"
        if "tags" in fields:
            item.tags = _validated_tags(fields["tags"])
            changes["tags"] = ",".join(item.tags)
        if "metadata" in fields:
            if not isinstance(fields["metadata"], dict):
                raise ValidationError("metadata 必须是 dict")
            item.metadata = dict(fields["metadata"])
            changes["metadata"] = f"<{len(item.metadata)} keys>"

        if "content_type" in fields:
            new_type = require_text(fields["content_type"], "content_type", max_length=64)
            if new_type != item.content_type:
                old_type = item.content_type
                item.content_type = new_type
                changes["content_type"] = f"{old_type} → {new_type}"
                if "expire_at" not in fields:
                    item.expire_at = self.policies.get(new_type).retention.expire_at(
                        item.created_at
                    )

        if "status" in fields:
            new_status = Status.parse(fields["status"])
            if new_status is Status.ACTIVE and item.superseded_by:
                raise ConflictError(
                    f"{item.id} 已被 {item.superseded_by} 取代；恢复请用 restore()"
                )
            changes["status"] = f"{item.status.value} → {new_status.value}"
            item.status = new_status

        if "expire_at" in fields:
            value = fields["expire_at"]
            item.expire_at = as_utc(value) if value is not None else None
            changes["expire_at"] = item.expire_at.isoformat() if item.expire_at else "None"

        item.revision += 1
        self.store.save(item)
        self.audit.log("update", item.id, f"更新字段 {sorted(fields)}", changes=changes)
        return item

    # ------------------------------------------------------------------ 删除

    def delete(self, item_id: str, *, force: bool = False) -> bool:
        """手动硬删除。

        若条目参与纠错链（它纠正了别人 / 别人纠正了它），默认拒绝；
        ``force=True`` 时一并拆链并记审计。
        """
        item = self._require(item_id)
        dependents = [i for i in self.store.list_all() if i.superseded_by == item_id]
        claimants = [i for i in self.store.list_all() if i.corrects == item_id]
        if (dependents or claimants) and not force:
            names = sorted({i.id for i in dependents} | {i.id for i in claimants})
            raise ConflictError(
                f"{item_id} 参与纠错链（关联: {names}）；确认拆链请用 force=True"
            )

        if force:
            for fake in dependents:
                self._undo_supersede(fake, reason=f"supplement {item_id} 被删除")
            for supplement in claimants:
                supplement.corrects = None
                supplement.revision += 1
                self.store.save(supplement)
                self.audit.log("unlink", supplement.id, f"清理失效的 corrects 指针 → {item_id}")

        self.store.delete(item_id)
        self.audit.log("delete", item_id, f"手动删除: {item.title}")
        return True

    def purge(
        self,
        *,
        status: Status | str = Status.DEPRECATED,
        older_than_days: int | None = None,
        dry_run: bool = False,
        force: bool = False,
    ) -> list[str]:
        """批量清除某个状态的条目（默认 deprecated）。

        会同时清理指向被清除条目的 ``corrects`` 指针；若仍有条目把被清除项当作
        ``superseded_by``，默认拒绝，``force=True`` 时一并拆链恢复。
        """
        target_status = Status.parse(status)
        if target_status is Status.ACTIVE:
            raise ValidationError("不能批量清除 active 条目")
        if older_than_days is not None and older_than_days < 0:
            raise ValidationError("older_than_days 不能为负")

        now = utc_now()
        victims: list[str] = []
        for item in self.store.list_all():
            if item.status is not target_status:
                continue
            if older_than_days is not None and (now - item.created_at) < timedelta(
                days=older_than_days
            ):
                continue
            victims.append(item.id)

        if dry_run:
            return victims

        victim_set = set(victims)
        referenced = [
            item.id
            for item in self.store.list_all()
            if item.superseded_by in victim_set and item.id not in victim_set
        ]
        if referenced and not force:
            raise ConflictError(
                f"仍有条目指向被清除项（{sorted(referenced)}）；确认拆链请用 force=True"
            )

        for item in list(self.store.list_all()):
            if item.superseded_by in victim_set and item.id not in victim_set:
                self._undo_supersede(item, reason=f"purge {item.superseded_by}")
            if item.corrects in victim_set:
                previous = item.corrects
                item.corrects = None
                item.revision += 1
                self.store.save(item)
                self.audit.log("unlink", item.id, f"清理失效的 corrects 指针 → {previous}")

        for victim in victims:
            self.store.delete(victim)
            self.audit.log("purge", victim, f"清除 {target_status.value} 条目")
        self.store.flush()
        return victims

    # ------------------------------------------------------------------ 纠错链

    def supersede(self, fake_id: str, supplement_id: str, *, force: bool = False) -> ContentItem:
        """显式建立「补充科普取代假科普」的关系。"""
        fake = self._require(fake_id)
        supplement = self._require(supplement_id)
        self._validate_supersede(fake, supplement_id, force=force)
        if supplement.corrects is not None and supplement.corrects != fake_id:
            raise ConflictError(
                f"{supplement_id} 已经在纠正 {supplement.corrects}；一条补充不应同时纠正两条假科普"
            )
        self._link_supersede(fake, supplement, now=utc_now())
        return fake

    def restore(self, item_id: str) -> ContentItem:
        """撤销取代：把条目恢复成 active，并断开对应的纠错链。"""
        fake = self._require(item_id)
        if not fake.superseded_by:
            raise ConflictError(f"{item_id} 没有被取代，无需恢复")
        supplement = self.store.get(fake.superseded_by)
        if supplement is not None and supplement.corrects == item_id:
            supplement.corrects = None
            supplement.revision += 1
            self.store.save(supplement)
        self._undo_supersede(fake, reason=f"手动恢复 {item_id}")
        return fake

    def correction_chain(self, item_id: str, *, depth: int = 50) -> dict[str, Any]:
        """返回以该条目为中心的纠错链。

        ``corrects``: 它纠正了谁（向上追溯）
        ``corrected_by``: 谁纠正了它（向下追溯）
        """
        item = self._require(item_id)
        visited = {item.id}
        corrected: list[ContentItem] = []
        node = item
        while node.corrects and len(corrected) < depth:
            parent = self.store.get(node.corrects)
            if parent is None or parent.id in visited:
                break
            visited.add(parent.id)
            corrected.append(parent)
            node = parent

        corrected_by: list[ContentItem] = []
        node = item
        while node.superseded_by and len(corrected_by) < depth:
            child = self.store.get(node.superseded_by)
            if child is None or child.id in visited:
                break
            visited.add(child.id)
            corrected_by.append(child)
            node = child

        return {
            "item": item,
            "corrects": corrected,
            "corrected_by": corrected_by,
            "chain_length": len(corrected) + len(corrected_by),
        }

    def corrections(self) -> list[dict[str, Any]]:
        """列出所有「A 被 B 取代」的关系及链路是否完整。"""
        pairs: list[dict[str, Any]] = []
        for fake in self.store.list_all():
            if not fake.superseded_by:
                continue
            supplement = self.store.get(fake.superseded_by)
            pairs.append(
                {
                    "fake": fake.id,
                    "supplement": fake.superseded_by,
                    "supplement_exists": supplement is not None,
                    "backlink_ok": supplement is not None and supplement.corrects == fake.id,
                    "fake_status": fake.status.value,
                }
            )
        return pairs

    # ------------------------------------------------------------------ 清理

    def cleanup(self, *, now: Any = None, dry_run: bool = False) -> CleanupReport:
        """执行一次生命周期清理。

        判定顺序：先看是否被取代（事件驱动），再看是否到期（时间驱动）。
        只有 ``active`` 条目会被处理，因此本方法幂等。
        """
        moment = as_utc(now) if now is not None else utc_now()
        report = CleanupReport(dry_run=dry_run)

        for item in list(self.store.list_all()):
            if item.status is not Status.ACTIVE:
                continue

            if item.superseded_by:
                reason = f"superseded_by:{item.superseded_by}"
            elif item.expire_at is not None and moment >= item.expire_at:
                reason = f"expired_at:{item.expire_at.isoformat()}"
            else:
                continue

            policy = self.policies.get(item.content_type)
            if dry_run:
                self._bucket(report, policy.action).append(item.id)
                continue

            action = self._apply_policy(item, policy, reason=reason)
            self._bucket(report, action).append(item.id)

        report.total_active = self.store.count(status=Status.ACTIVE)
        if not dry_run:
            self.store.flush()
        return report

    @staticmethod
    def _bucket(report: CleanupReport, action: "Action | str") -> list[str]:
        action = Action.parse(action)
        return {
            Action.DELETE: report.deleted,
            Action.ARCHIVE: report.archived,
            Action.DEPRECATE: report.deprecated,
            Action.NONE: report.expired,
        }[action]

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict[str, Any]:
        all_items = self.store.list_all()
        by_status: dict[str, int] = {}
        for item in all_items:
            by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
        by_type: dict[str, int] = {}
        for item in all_items:
            if item.status is Status.ACTIVE:
                by_type[item.content_type] = by_type.get(item.content_type, 0) + 1
        return {
            "total": len(all_items),
            "active": by_status.get(Status.ACTIVE.value, 0),
            "by_status": by_status,
            "by_type": by_type,
            "corrections": sum(1 for i in all_items if i.superseded_by),
            "audit": self.audit.stats(),
            "policies": self.policies.as_list(),
        }

    def integrity(self) -> dict[str, list[str]]:
        """悬空引用检查。测试与运维都可以拿它当健康检查。"""
        ids = {item.id for item in self.store.list_all()}
        dangling_superseded_by: list[str] = []
        dangling_corrects: list[str] = []
        for item in self.store.list_all():
            if item.superseded_by and item.superseded_by not in ids:
                dangling_superseded_by.append(item.id)
            if item.corrects and item.corrects not in ids:
                dangling_corrects.append(item.id)
        return {
            "dangling_superseded_by": sorted(dangling_superseded_by),
            "dangling_corrects": sorted(dangling_corrects),
        }

    # ------------------------------------------------------------------ 内部

    def _validate_supersede(self, fake: ContentItem, supplement_id: str, *, force: bool) -> None:
        if fake.id == supplement_id:
            raise ValidationError("条目不能取代自身")
        policy = self.policies.get(fake.content_type)
        if not policy.can_be_superseded and not force:
            raise ConflictError(
                f"类型 {fake.content_type} 的策略不允许被取代"
                "（可在 PolicyBook 中设置 can_be_superseded=True）"
            )
        if fake.superseded_by and fake.superseded_by != supplement_id and not force:
            raise ConflictError(f"{fake.id} 已被 {fake.superseded_by} 取代；覆盖请用 force=True")
        if fake.status is not Status.ACTIVE and not force:
            raise ConflictError(f"{fake.id} 当前状态为 {fake.status.value}，不能再被取代")

    def _link_supersede(self, fake: ContentItem, supplement: ContentItem, *, now: Any) -> None:
        previous = fake.superseded_by
        if previous and previous != supplement.id:
            # 重新纠错时，旧补充条目不能再声称纠正这条假科普
            old = self.store.get(previous)
            if old is not None and old.corrects == fake.id:
                old.corrects = None
                old.revision += 1
                self.store.save(old)
                self.audit.log("unlink", old.id, f"被 {supplement.id} 接管纠错")

        fake.superseded_by = supplement.id
        if supplement.id not in fake.related_ids:
            fake.related_ids.append(supplement.id)
        fake.expire_at = as_utc(now)
        policy = self.policies.get(fake.content_type)
        action = self._apply_policy(fake, policy, reason=f"superseded_by:{supplement.id}")
        self.audit.log("supersede", fake.id, f"被 {supplement.id} 取代")

        # 极少数情况下（force 覆盖了不可取代的类型且其动作是 delete），目标已被删除，
        # 此时不能再留下反向指针，否则纠错链会悬空。
        if action != Action.DELETE.value and supplement.corrects != fake.id:
            supplement.corrects = fake.id
            supplement.revision += 1
            self.store.save(supplement)

    def _apply_policy(self, item: ContentItem, policy: Any, *, reason: str) -> str:
        """执行策略动作。返回动作名（便于调用方分类统计）。"""
        action = Action.parse(policy.action)
        if action is Action.DELETE:
            # 若还有条目声称纠正它，先断开反向指针，避免删除后留下悬空 corrects。
            self._clear_inbound_corrects(item.id)
            self.store.delete(item.id)
            self.audit.log("delete", item.id, reason)
            return action.value

        item.status = {
            Action.ARCHIVE: Status.ARCHIVED,
            Action.DEPRECATE: Status.DEPRECATED,
            Action.NONE: Status.EXPIRED,
        }[action]
        self.store.save(item)
        self.audit.log("expire" if action is Action.NONE else action.value, item.id, reason)
        return action.value

    def _undo_supersede(self, fake: ContentItem, *, reason: str) -> None:
        previous = fake.superseded_by
        fake.superseded_by = None
        fake.related_ids = [rid for rid in fake.related_ids if rid != previous]
        fake.status = Status.ACTIVE
        fake.expire_at = self.policies.get(fake.content_type).retention.expire_at(fake.created_at)
        self.store.save(fake)
        self.audit.log("unsupersede", fake.id, reason)

    def _clear_inbound_corrects(self, item_id: str) -> None:
        for other in self.store.list_all():
            if other.corrects != item_id:
                continue
            other.corrects = None
            other.revision += 1
            self.store.save(other)
            self.audit.log("unlink", other.id, f"目标 {item_id} 被删除，清理 corrects")


def _validated_tags(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise ValidationError("tags 必须是字符串集合")
    tags: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValidationError("tags 只能包含字符串")
        text = value.strip()
        if text and text not in tags:
            tags.append(text)
    return tags
