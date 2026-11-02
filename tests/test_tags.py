"""Tests for the Indigo 2026.1 tag tools (manage_tags, tag_objects,
list_tagged).

One test per promise, phrased adversarially ("when could this report
success having changed nothing, or an empty answer that is wrong?").

The fake Indigo below is built from small real classes, not
MagicMock: MagicMock auto-vivifies, so an object's ``tags`` read back
after a write would be a fresh mock rather than the list the fake
command changed, and every read-back check would pass vacuously.

"This was not called" is made fatal with ``_MustNotBeCalled``, a
BaseException, so the module's own ``except Exception`` guards (which
keep one bad object from aborting a batch) cannot swallow it.
"""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest


class _MustNotBeCalled(BaseException):
    """Raised by a dependency the test forbids touching."""


def _forbid(what):
    def boom(*_args, **_kwargs):
        raise _MustNotBeCalled(what)
    return boom


# ---------------------------------------------------------------------
# fake Indigo 2026.1
# ---------------------------------------------------------------------

_NO_REMOTE = object()


class _Obj:
    def __init__(self, obj_id, name, tags=(), folder_id=0, remote_display=True):
        self.id = obj_id
        self.name = name
        self.folderId = folder_id
        if remote_display is not _NO_REMOTE:
            self.remoteDisplay = remote_display
        self._tags = list(tags)

    @property
    def tags(self):
        return list(self._tags)


class _BadName(_Obj):
    """An object whose ``name`` raises something other than AttributeError."""

    @property
    def name(self):
        raise RuntimeError("name exploded")

    @name.setter
    def name(self, _value):
        pass


class _BadTags(_Obj):
    """An object whose ``tags`` cannot be read."""

    @property
    def tags(self):
        raise RuntimeError("tags exploded")


class _Collection:
    def __init__(self, objs=(), raising_ids=(), iter_error=None):
        self.objs = {o.id: o for o in objs}
        self.raising_ids = set(raising_ids)
        self.iter_error = iter_error

    forbid_lookup = False

    def __getitem__(self, obj_id):
        if self.forbid_lookup:
            raise _MustNotBeCalled(f"lookup of {obj_id} in the wrong collection")
        if obj_id in self.raising_ids:
            raise RuntimeError("lookup exploded")
        return self.objs[obj_id]

    def __iter__(self):
        if self.iter_error is not None:
            raise self.iter_error
        return iter(list(self.objs.values()))


class _Namespace:
    def __init__(self, collection, server):
        self._collection = collection
        self._server = server

    def add_tag(self, obj_id, tag):
        if tag not in self._server.library:
            raise ValueError(f"no tag {tag}")
        obj = self._collection.objs[obj_id]
        if tag not in obj._tags:
            obj._tags.append(tag)

    def remove_tag(self, obj_id, tag):
        obj = self._collection.objs[obj_id]
        if tag in obj._tags:
            obj._tags.remove(tag)


class _Server:
    version = "2026.1.0"

    def __init__(self, fake):
        self._fake = fake
        self.library = {}

    @property
    def tags(self):
        return {name: dict(entry) for name, entry in self.library.items()}

    def add_library(self, name, color="4A90D9"):
        self.library[name] = {"color": color, "text_color": "0A1A2A", "border_color": "3A80C9"}

    def create_tag(self, name, color):
        if name in self.library:
            raise ValueError("exists")
        self.add_library(name, color)

    def update_tag(self, name, **kwargs):
        # Strict on purpose: only the documented kwargs, never None, at
        # least one. A lenient fake would hide a regression that sends
        # new_tag=None on a recolour.
        if not kwargs or set(kwargs) - {"new_tag", "new_color"}:
            raise TypeError(f"bad update_tag kwargs {kwargs}")
        if any(v is None for v in kwargs.values()):
            raise TypeError(f"update_tag got None in {kwargs}")
        new_tag, new_color = kwargs.get("new_tag"), kwargs.get("new_color")
        if name not in self.library:
            raise ValueError("unknown")
        entry = self.library.pop(name)
        if new_color is not None:
            entry["color"] = new_color
        final = new_tag or name
        self.library[final] = entry
        if new_tag:
            for coll in self._fake.collections():
                for obj in coll.objs.values():
                    obj._tags = [new_tag if t == name else t for t in obj._tags]

    def delete_tag(self, name):
        if name not in self.library:
            raise ValueError("unknown")
        del self.library[name]
        for coll in self._fake.collections():
            for obj in coll.objs.values():
                obj._tags = [t for t in obj._tags if t != name]


_COLLECTIONS = {
    "devices": "device", "variables": "variable", "triggers": "trigger",
    "schedules": "schedule", "actionGroups": "actionGroup", "controlPages": "controlPage",
}


class _FakeIndigo:
    def __init__(self):
        self.server = _Server(self)
        for coll_attr, ns_attr in _COLLECTIONS.items():
            coll = _Collection()
            setattr(self, coll_attr, coll)
            setattr(self, ns_attr, _Namespace(coll, self.server))

    def collections(self):
        return [getattr(self, a) for a in _COLLECTIONS]

    def put(self, coll_attr, *objs):
        coll = getattr(self, coll_attr)
        for obj in objs:
            coll.objs[obj.id] = obj


class _OldServer:
    version = "2025.2.5"
    tags = {}


class _Capture:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, description, input_schema, handler):
        self.tools[name] = handler


def _tools(fake, allow_delete=False):
    from tools import tags

    capture = _Capture()
    provider = allow_delete if callable(allow_delete) else (lambda: allow_delete)
    tags.register(capture, indigo_module=fake, allow_tag_delete=provider)
    return capture.tools


@pytest.fixture
def fake():
    f = _FakeIndigo()
    f.server.add_library("kitchen", "4A90D9")
    f.server.add_library("upstairs", "FF8800")
    return f


# ---------------------------------------------------------------------
# name / colour validation
# ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["garden", "holiday-lighting", "a-b-c", "x"])
def test_good_names_are_accepted(fake, name):
    out = _tools(fake)["manage_tags"](action="create", tag=name)
    assert out["accepted"] is True and out["verified"] is True
    assert name in fake.server.library


@pytest.mark.parametrize("name", [
    "Kitchen", "kitchen2", "kitchen--area", "-kitchen", "kitchen-", "kitchen area",
    "kitchen_area", "KITCHEN", "",
])
def test_bad_names_are_refused_with_the_rule(fake, name):
    fake.server.create_tag = _forbid("create_tag")
    with pytest.raises(ValueError) as exc:
        _tools(fake)["manage_tags"](action="create", tag=name)
    assert "lowercase letters separated by single dashes" in str(exc.value)
    if name:
        assert repr(name) in str(exc.value)


def test_bad_name_suggests_a_valid_one(fake):
    with pytest.raises(ValueError, match="Did you mean 'kitchen-area'"):
        _tools(fake)["manage_tags"](action="create", tag="Kitchen Area")


@pytest.mark.parametrize("given, stored", [
    ("#4a90d9", "4A90D9"), ("4A90D9", "4A90D9"), (" #ff8800 ", "FF8800"), ("abcdef", "ABCDEF"),
])
def test_colour_is_normalised(fake, given, stored):
    out = _tools(fake)["manage_tags"](action="create", tag="garden", color=given)
    assert fake.server.library["garden"]["color"] == stored
    assert out["entry"]["color"] == stored and out["verified"] is True


@pytest.mark.parametrize("bad", ["#12345", "GGGGGG", "#1234567", 123, "blue"])
def test_bad_colour_is_refused(fake, bad):
    fake.server.create_tag = _forbid("create_tag")
    with pytest.raises(ValueError, match="six hex digits"):
        _tools(fake)["manage_tags"](action="create", tag="garden", color=bad)


# ---------------------------------------------------------------------
# manage_tags: create / update
# ---------------------------------------------------------------------

def test_create_reads_back_the_library_entry(fake):
    out = _tools(fake)["manage_tags"](action="create", tag="garden", color="#00ff00")
    assert out == {
        "action": "create", "tag": "garden", "accepted": True,
        "entry": {"color": "00FF00", "text_color": "0A1A2A", "border_color": "3A80C9"},
        "verified": True,
    }


def test_create_without_colour_uses_the_default(fake):
    _tools(fake)["manage_tags"](action="create", tag="garden")
    assert fake.server.library["garden"]["color"] == "4A90D9"


def test_create_of_an_existing_tag_is_refused_naming_its_colour(fake):
    fake.server.create_tag = _forbid("create_tag")
    with pytest.raises(ValueError) as exc:
        _tools(fake)["manage_tags"](action="create", tag="upstairs", color="000000")
    assert "already exists" in str(exc.value) and "FF8800" in str(exc.value)


def test_create_whose_read_back_lacks_the_tag_is_not_verified(fake):
    fake.server.create_tag = lambda name, color: None  # accepted, changed nothing
    out = _tools(fake)["manage_tags"](action="create", tag="garden")
    assert out["verified"] is False and out["entry"] is None and "note" in out


def test_update_renames_and_carries_objects(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]))
    out = _tools(fake)["manage_tags"](action="update", tag="kitchen", new_tag="kitchen-area")
    assert out["renamed"] is True and out["recolored"] is False and out["verified"] is True
    assert out["tag"] == "kitchen-area" and out["previous_tag"] == "kitchen"
    assert "kitchen" not in fake.server.library
    assert fake.devices.objs[1].tags == ["kitchen-area"]


def test_update_recolours(fake):
    out = _tools(fake)["manage_tags"](action="update", tag="kitchen", color="#112233")
    assert out["recolored"] is True and out["renamed"] is False and out["verified"] is True
    assert fake.server.library["kitchen"]["color"] == "112233"


def test_update_passes_the_documented_kwargs(fake):
    seen = {}

    def update_tag(name, **kwargs):
        seen.update(kwargs, name=name)
        _Server.update_tag(fake.server, name, **kwargs)

    fake.server.update_tag = update_tag
    _tools(fake)["manage_tags"](action="update", tag="kitchen", new_tag="cooking", color="123456")
    assert seen == {"name": "kitchen", "new_tag": "cooking", "new_color": "123456"}


def test_update_needs_a_change(fake):
    fake.server.update_tag = _forbid("update_tag")
    with pytest.raises(ValueError, match="new_tag"):
        _tools(fake)["manage_tags"](action="update", tag="kitchen")


def test_update_refuses_a_bad_new_name_and_a_taken_one(fake):
    fake.server.update_tag = _forbid("update_tag")
    tools = _tools(fake)
    with pytest.raises(ValueError, match="not a valid tag name"):
        tools["manage_tags"](action="update", tag="kitchen", new_tag="Kitchen2")
    with pytest.raises(ValueError, match="already exists"):
        tools["manage_tags"](action="update", tag="kitchen", new_tag="upstairs")


def test_update_of_an_unknown_tag_lists_the_library(fake):
    fake.server.update_tag = _forbid("update_tag")
    with pytest.raises(ValueError) as exc:
        _tools(fake)["manage_tags"](action="update", tag="garage", color="000000")
    assert "'kitchen'" in str(exc.value) and "'upstairs'" in str(exc.value)


def test_update_whose_read_back_shows_no_change_is_not_verified(fake):
    fake.server.update_tag = lambda name, **kwargs: None
    out = _tools(fake)["manage_tags"](action="update", tag="kitchen", color="000000")
    assert out["verified"] is False


def test_unknown_action_is_refused(fake):
    with pytest.raises(ValueError, match="create"):
        _tools(fake)["manage_tags"](action="rename", tag="kitchen")


# ---------------------------------------------------------------------
# manage_tags: delete, gated twice
# ---------------------------------------------------------------------

def test_delete_with_pref_off_is_refused_even_with_confirm(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    with pytest.raises(ValueError) as exc:
        _tools(fake, allow_delete=False)["manage_tags"](action="delete", tag="kitchen", confirm=True)
    assert "allowTagDelete" in str(exc.value) and "Allow tag delete" in str(exc.value)
    assert "kitchen" in fake.server.library


def test_delete_without_confirm_counts_carriers_and_deletes_nothing(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]), _Obj(2, "Fan", tags=["upstairs"]))
    fake.put("variables", _Obj(10, "mode", tags=["kitchen", "upstairs"]))
    fake.put("controlPages", _Obj(20, "Kitchen page", tags=["kitchen"]))
    out = _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen")
    assert out["requires_confirmation"] is True and out["accepted"] is False
    assert out["carrier_count"] == 3
    # Completeness fields are present even when the scan was clean.
    assert out["carrier_count_complete"] is True and out["collections_unreadable"] == []
    assert out["attr_read_errors"] == 0 and out["attr_read_error_example"] is None
    assert "the 3 object(s)" in out["message"]
    assert {(c["type"], c["id"]) for c in out["carriers"]} == {
        ("device", 1), ("variable", 10), ("control_page", 20)}
    assert out["carriers_truncated"] is False
    assert "kitchen" in fake.server.library


def test_delete_confirmation_caps_the_carrier_list_but_not_the_count(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    fake.put("devices", *[_Obj(i, f"Lamp {i:02}", tags=["kitchen"]) for i in range(1, 31)])
    out = _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen")
    assert out["carrier_count"] == 30
    assert len(out["carriers"]) == 25 and out["carriers_truncated"] is True


def test_delete_confirmation_says_when_the_count_is_a_lower_bound(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]))
    fake.variables.iter_error = RuntimeError("variables unavailable")
    out = _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen")
    assert out["carrier_count"] == 1
    assert out["carrier_count_complete"] is False
    assert out["collections_unreadable"][0]["type"] == "variable"


def test_delete_with_both_gates_deletes_and_verifies(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen", "upstairs"]))
    out = _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen", confirm=True)
    assert out["accepted"] is True and out["verified"] is True and out["carrier_count"] == 1
    assert "kitchen" not in fake.server.library
    assert fake.devices.objs[1].tags == ["upstairs"]


def test_delete_of_an_unknown_tag_lists_the_library(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    with pytest.raises(ValueError, match="'upstairs'"):
        _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="garage", confirm=True)


def test_delete_pref_is_read_on_every_call(fake):
    pref = {"on": False}
    tools = _tools(fake, allow_delete=lambda: pref["on"])
    with pytest.raises(ValueError, match="allowTagDelete"):
        tools["manage_tags"](action="delete", tag="kitchen", confirm=True)
    pref["on"] = True
    assert tools["manage_tags"](action="delete", tag="kitchen", confirm=True)["accepted"] is True


def test_confirm_must_be_a_boolean(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    with pytest.raises(ValueError, match="confirm"):
        _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen", confirm="yes")


# ---------------------------------------------------------------------
# tag_objects
# ---------------------------------------------------------------------

def _batch_fake(fake):
    fake.put("devices", _Obj(1, "Lamp"), _Obj(2, "Fan", tags=["kitchen"]))
    fake.put("variables", _Obj(10, "mode"))
    fake.devices.raising_ids.add(3)
    return fake


def test_add_reports_each_outcome_and_one_failure_does_not_abort(fake):
    _batch_fake(fake)
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1},
        {"type": "device", "id": 3},       # lookup raises
        {"type": "device", "id": 2},       # already tagged
        {"type": "device", "id": 999},     # not found
        {"type": "variable", "id": 10},    # after the failure: still processed
    ])
    outcomes = [(r["type"], r["id"], r["outcome"]) for r in out["results"]]
    assert outcomes == [
        ("device", 1, "added"), ("device", 3, "error"), ("device", 2, "already"),
        ("device", 999, "not_found"), ("variable", 10, "added"),
    ]
    assert "lookup failed" in out["results"][1]["message"]
    assert [r["dispatched"] for r in out["results"]] == [True, False, False, False, True]
    assert out["summary"] == {"added": 2, "error": 1, "already": 1, "not_found": 1}
    # The writes that ran were confirmed, but one row failed: not verified.
    assert out["verified"] is False and out["dry_run"] is False and out["requested"] == 5
    assert fake.devices.objs[1].tags == ["kitchen"] and fake.variables.objs[10].tags == ["kitchen"]


def test_remove_reports_removed_and_absent(fake):
    _batch_fake(fake)
    out = _tools(fake)["tag_objects"](tag="kitchen", action="remove", objects=[
        {"type": "device", "id": 2}, {"type": "device", "id": 1}])
    assert [r["outcome"] for r in out["results"]] == ["removed", "absent"]
    assert fake.devices.objs[2].tags == []


def test_a_raising_add_tag_is_an_error_outcome_and_the_batch_continues(fake):
    _batch_fake(fake)
    real_add = fake.device.add_tag

    def flaky(obj_id, tag):
        if obj_id == 1:
            raise RuntimeError("server hiccup")
        real_add(obj_id, tag)

    fake.device.add_tag = flaky
    fake.put("devices", _Obj(4, "Heater"))
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1}, {"type": "device", "id": 4}])
    assert [r["outcome"] for r in out["results"]] == ["error", "added"]
    first = out["results"][0]
    assert first["dispatched"] is True
    assert "WAS DISPATCHED" in first["message"] and "may or may not have taken effect" in first["message"]
    assert out["verified"] is False


def test_read_back_mismatch_is_an_error_and_unverified(fake):
    _batch_fake(fake)
    fake.device.add_tag = lambda obj_id, tag: None  # accepted, changed nothing
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1}, {"type": "variable", "id": 10}])
    first = out["results"][0]
    assert first["outcome"] == "error"
    assert first["message"] == "command accepted but read-back does not show the change"
    assert out["results"][1]["outcome"] == "added"
    assert out["verified"] is False


def test_more_than_200_objects_is_refused_with_the_count(fake):
    fake.device.add_tag = _forbid("add_tag")
    objects = [{"type": "device", "id": i} for i in range(201)]
    with pytest.raises(ValueError, match="201"):
        _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=objects)


def test_200_objects_is_allowed(fake):
    objects = [{"type": "device", "id": i} for i in range(200)]
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=objects)
    assert out["summary"] == {"error": 0, "not_found": 200} and out["verified"] is None


def test_unknown_tag_is_refused_naming_manage_tags(fake):
    _batch_fake(fake)
    fake.device.add_tag = _forbid("add_tag")
    fake.server.create_tag = _forbid("create_tag")
    with pytest.raises(ValueError) as exc:
        _tools(fake)["tag_objects"](tag="garage", action="add", objects=[{"type": "device", "id": 1}])
    assert "manage_tags" in str(exc.value) and "garage" not in fake.server.library


def test_dry_run_calls_no_add_or_remove(fake):
    _batch_fake(fake)
    for ns in ("device", "variable"):
        getattr(fake, ns).add_tag = _forbid("add_tag")
        getattr(fake, ns).remove_tag = _forbid("remove_tag")
    tools = _tools(fake)
    added = tools["tag_objects"](tag="kitchen", action="add", dry_run=True, objects=[
        {"type": "device", "id": 1}, {"type": "device", "id": 2}, {"type": "device", "id": 999}])
    assert [r["outcome"] for r in added["results"]] == ["would_add", "already", "not_found"]
    assert added["dry_run"] is True and added["verified"] is None
    removed = tools["tag_objects"](tag="kitchen", action="remove", dry_run=True, objects=[
        {"type": "device", "id": 2}, {"type": "variable", "id": 10}])
    assert [r["outcome"] for r in removed["results"]] == ["would_remove", "absent"]
    assert fake.devices.objs[2].tags == ["kitchen"] and fake.devices.objs[1].tags == []


def test_removing_from_more_than_25_needs_confirm(fake):
    fake.put("devices", *[_Obj(i, f"L{i}", tags=["kitchen"]) for i in range(26)])
    objects = [{"type": "device", "id": i} for i in range(26)]
    real_remove = fake.device.remove_tag
    fake.device.remove_tag = _forbid("remove_tag")
    tools = _tools(fake)
    out = tools["tag_objects"](tag="kitchen", action="remove", objects=objects)
    assert out["requires_confirmation"] is True and out["requested"] == 26
    assert "26 entries" in out["message"]
    assert all(o.tags == ["kitchen"] for o in fake.devices.objs.values())
    fake.device.remove_tag = real_remove
    out = tools["tag_objects"](tag="kitchen", action="remove", objects=objects, confirm=True)
    assert out["summary"] == {"error": 0, "removed": 26} and out["verified"] is True


def test_removing_25_needs_no_confirm(fake):
    fake.put("devices", *[_Obj(i, f"L{i}", tags=["kitchen"]) for i in range(25)])
    out = _tools(fake)["tag_objects"](tag="kitchen", action="remove",
                                      objects=[{"type": "device", "id": i} for i in range(25)])
    assert out["summary"] == {"error": 0, "removed": 25}


@pytest.mark.parametrize("objects, match", [
    ([], "non-empty"),
    ("device:1", "non-empty"),
    ([{"type": "folder", "id": 1}], "objects\\[0\\].type"),
    ([{"type": "device", "id": "1"}], "objects\\[0\\].id"),
    ([{"type": "device", "id": -1}], "objects\\[0\\].id"),
    ([{"type": "device", "id": 1}, 5], "objects\\[1\\]"),
])
def test_malformed_objects_are_refused_before_anything_is_touched(fake, objects, match):
    fake.device.add_tag = _forbid("add_tag")
    with pytest.raises(ValueError, match=match):
        _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=objects)


def test_tag_objects_rejects_unknown_arguments(fake):
    with pytest.raises(ValueError, match="dryrun"):
        _tools(fake)["tag_objects"](tag="kitchen", action="add", dryrun=True,
                                    objects=[{"type": "device", "id": 1}])


# ---------------------------------------------------------------------
# list_tagged
# ---------------------------------------------------------------------

def _listing_fake(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"], folder_id=5),
             _Obj(2, "Hidden lamp", tags=["kitchen"], remote_display=False),
             _Obj(3, "Fan", tags=["upstairs"]))
    fake.put("variables", _Obj(10, "mode", tags=["kitchen"]))
    fake.put("triggers", _Obj(20, "Motion", tags=["kitchen"], remote_display=_NO_REMOTE))
    fake.put("actionGroups", _Obj(30, "Cook", tags=["kitchen"]))
    return fake


def test_list_tagged_spans_every_type_and_includes_unpublished_objects(fake):
    _listing_fake(fake)
    out = _tools(fake)["list_tagged"](tag="kitchen")
    assert [(r["type"], r["id"]) for r in out["results"]] == [
        ("device", 2), ("device", 1), ("variable", 10), ("trigger", 20), ("action_group", 30)]
    hidden = out["results"][0]
    assert hidden == {"type": "device", "id": 2, "name": "Hidden lamp", "folder_id": 0,
                      "remote_display": False}
    assert out["results"][1]["folder_id"] == 5
    assert out["results"][3]["remote_display"] is None  # triggers have no such setting
    assert out["total_count"] == 5 and out["has_more"] is False
    assert out["limit"] == 100 and out["offset"] == 0
    assert out["total_count_complete"] is True and out["collections_unreadable"] == []
    assert out["attr_read_errors"] == 0 and out["attr_read_error_example"] is None


def test_list_tagged_filters_by_type(fake):
    _listing_fake(fake)
    out = _tools(fake)["list_tagged"](tag="kitchen", type="device")
    assert [r["id"] for r in out["results"]] == [2, 1] and out["total_count"] == 2


def test_list_tagged_paginates(fake):
    _listing_fake(fake)
    tools = _tools(fake)
    page1 = tools["list_tagged"](tag="kitchen", limit=2)
    page2 = tools["list_tagged"](tag="kitchen", limit=2, offset=4)
    assert len(page1["results"]) == 2 and page1["has_more"] is True and page1["total_count"] == 5
    assert [r["id"] for r in page2["results"]] == [30] and page2["has_more"] is False


def test_list_tagged_unknown_tag_is_an_error_not_an_empty_list(fake):
    with pytest.raises(ValueError, match="'kitchen'"):
        _tools(fake)["list_tagged"](tag="kitchn")


def test_list_tagged_bad_type_is_refused(fake):
    with pytest.raises(ValueError, match="type must be one of"):
        _tools(fake)["list_tagged"](tag="kitchen", type="folder")


def test_list_tagged_reports_an_unreadable_collection(fake):
    _listing_fake(fake)
    fake.triggers.iter_error = RuntimeError("triggers unavailable")
    out = _tools(fake)["list_tagged"](tag="kitchen")
    assert out["total_count"] == 4
    assert out["collections_unreadable"] == [
        {"type": "trigger", "error": "RuntimeError: triggers unavailable"}]
    assert out["total_count_complete"] is False


def test_list_tagged_fails_when_its_only_collection_is_unreadable(fake):
    fake.devices.iter_error = RuntimeError("devices unavailable")
    with pytest.raises(ValueError, match="no object collection could be read"):
        _tools(fake)["list_tagged"](tag="kitchen", type="device")


def test_unreadable_library_is_a_failed_call(fake):
    class _Broken(_Server):
        @property
        def tags(self):
            raise RuntimeError("library unavailable")

    fake.server = _Broken(fake)
    with pytest.raises(ValueError, match="tag library"):
        _tools(fake)["list_tagged"](tag="kitchen")


# ---------------------------------------------------------------------
# pre-2026.1 server
# ---------------------------------------------------------------------

@pytest.mark.parametrize("tool, args", [
    ("manage_tags", {"action": "create", "tag": "kitchen"}),
    ("tag_objects", {"tag": "kitchen", "action": "add", "objects": [{"type": "device", "id": 1}]}),
    ("list_tagged", {"tag": "kitchen"}),
])
def test_pre_2026_1_server_fails_naming_the_version(fake, tool, args):
    fake.server = _OldServer()
    with pytest.raises(ValueError) as exc:
        _tools(fake, allow_delete=True)[tool](**args)
    assert str(exc.value) == "Tags need Indigo 2026.1 or later; this server is 2025.2.5."


# ---------------------------------------------------------------------
# wire: one tools/call per tool through the real MCPHandler
# ---------------------------------------------------------------------

def _call(handler, name, arguments):
    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}}),
    )
    assert response["status"] == 200, response
    result = json.loads(response["content"])["result"]
    return result, json.loads(result["content"][0]["text"])


def test_each_tag_tool_round_trips_through_mcp_handler(fake):
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    fake.put("devices", _Obj(1, "Lamp"))
    handler = MCPHandler(server_name="test", server_version="0")
    result = register_all(handler, indigo_module=fake, allow_tag_delete=lambda: True)
    assert result["slim"] is True  # the fake is 2026.1

    res, body = _call(handler, "manage_tags", {"action": "create", "tag": "garden", "color": "#00aa00"})
    assert not res.get("isError") and body["verified"] is True

    res, body = _call(handler, "tag_objects",
                      {"tag": "garden", "action": "add", "objects": [{"type": "device", "id": 1}]})
    assert not res.get("isError") and body["summary"] == {"error": 0, "added": 1}

    res, body = _call(handler, "list_tagged", {"tag": "garden"})
    assert not res.get("isError") and [r["id"] for r in body["results"]] == [1]

    # register_all threads the delete pref through.
    res, body = _call(handler, "manage_tags", {"action": "delete", "tag": "garden"})
    assert not res.get("isError") and body["requires_confirmation"] is True

    # A caller fault is an isError tool result, not a protocol error.
    res, body = _call(handler, "manage_tags", {"action": "create", "tag": "Bad Name"})
    assert res["isError"] is True and "lowercase" in body["error"]


# ---------------------------------------------------------------------
# plugin.py reads allowTagDelete live
# ---------------------------------------------------------------------

class _FakePluginBase:
    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        self.pluginId = plugin_id
        self.pluginPrefs = plugin_prefs
        self.logger = MagicMock(name="logger")
        self.indigo_log_handler = MagicMock(name="indigo_log_handler")


def test_plugin_reads_allow_tag_delete_on_every_call(monkeypatch):
    fake = MagicMock(name="indigo")
    fake.PluginBase = _FakePluginBase
    fake.server.version = "2026.1.0"
    fake.server.tags = {"kitchen": {"color": "4A90D9"}}
    for coll in ("devices", "variables", "triggers", "schedules", "actionGroups", "controlPages"):
        setattr(fake, coll, [])
    fake.server.delete_tag = _forbid("delete_tag")
    monkeypatch.setitem(sys.modules, "indigo", fake)
    path = (pathlib.Path(__file__).parent.parent / "Indigo MCP Lite.indigoPlugin"
            / "Contents" / "Server Plugin" / "plugin.py")
    spec = importlib.util.spec_from_file_location("_lite_plugin_tags", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    plugin = module.Plugin("com.simons-plugins.indigo-mcp-lite", "Indigo MCP Lite", "0", {"logLevel": "INFO"})
    plugin.startup()
    args = {"action": "delete", "tag": "kitchen"}

    res, body = _call(plugin.mcp_handler, "manage_tags", args)
    assert res["isError"] is True and "allowTagDelete" in body["error"]

    plugin.pluginPrefs["allowTagDelete"] = True  # no restart
    res, body = _call(plugin.mcp_handler, "manage_tags", args)
    assert not res.get("isError") and body["requires_confirmation"] is True


# ---------------------------------------------------------------------
# review round 1: tag_objects `verified`, `dispatched`, duplicates
# ---------------------------------------------------------------------

def test_all_error_batch_is_not_verified(fake):
    fake.devices.raising_ids.update({1, 2})
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1}, {"type": "device", "id": 2}])
    assert out["summary"] == {"error": 2}
    assert out["verified"] is False


def test_all_not_found_batch_has_nothing_to_verify(fake):
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1}, {"type": "variable", "id": 2}])
    assert out["summary"] == {"error": 0, "not_found": 2}
    assert out["verified"] is None


def test_already_and_absent_only_has_nothing_to_verify(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]), _Obj(2, "Fan"))
    fake.device.add_tag = _forbid("add_tag")
    fake.device.remove_tag = _forbid("remove_tag")
    tools = _tools(fake)
    assert tools["tag_objects"](tag="kitchen", action="add", objects=[{"type": "device", "id": 1}])["verified"] is None
    assert tools["tag_objects"](tag="kitchen", action="remove",
                                objects=[{"type": "device", "id": 2}])["verified"] is None


def test_mixed_success_and_error_is_not_verified(fake):
    fake.put("devices", _Obj(1, "Lamp"))
    fake.devices.raising_ids.add(2)
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1}, {"type": "device", "id": 2}])
    assert [r["outcome"] for r in out["results"]] == ["added", "error"]
    assert out["verified"] is False and out["summary"]["error"] == 1


def test_a_raising_name_mid_batch_is_an_error_row_not_an_aborted_call(fake):
    fake.put("devices", _Obj(1, "Lamp"), _BadName(2, "ignored"), _Obj(3, "Heater"))
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[
        {"type": "device", "id": 1}, {"type": "device", "id": 2}, {"type": "device", "id": 3}])
    rows = out["results"]
    assert [r["outcome"] for r in rows] == ["added", "error", "added"]
    assert rows[1]["dispatched"] is False
    assert "name exploded" in rows[1]["message"] and "nothing was sent" in rows[1]["message"]
    assert out["verified"] is False
    assert fake.devices.objs[1].tags == ["kitchen"] and fake.devices.objs[2].tags == []


def test_tags_unreadable_is_an_error_row_with_nothing_sent(fake):
    fake.put("devices", _BadTags(1, "Lamp"))
    fake.device.add_tag = _forbid("add_tag")
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[{"type": "device", "id": 1}])
    row = out["results"][0]
    assert row["outcome"] == "error" and row["dispatched"] is False and row["name"] == "Lamp"
    assert row["message"].startswith("tags unreadable: RuntimeError: tags exploded")
    assert out["verified"] is False


def test_read_back_failure_after_a_write_says_it_may_have_taken_effect(fake):
    class _OnceThenBroken(_Collection):
        calls = 0

        def __getitem__(self, obj_id):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("re-read failed")
            return super().__getitem__(obj_id)

    coll = _OnceThenBroken([_Obj(1, "Lamp")])
    fake.devices = coll
    fake.device = _Namespace(coll, fake.server)
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[{"type": "device", "id": 1}])
    row = out["results"][0]
    assert row["outcome"] == "error" and row["dispatched"] is True
    assert "read-back failed" in row["message"] and "may have taken effect" in row["message"]
    assert out["verified"] is False


def test_an_indigo_refusal_of_add_tag_is_an_error_row_naming_the_refusal(fake):
    fake.put("devices", _Obj(1, "Lamp"))

    def refuse(_obj_id, _tag):
        raise ValueError("no such tag")

    fake.device.add_tag = refuse
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[{"type": "device", "id": 1}])
    row = out["results"][0]
    assert row["outcome"] == "error" and row["dispatched"] is True
    assert row["message"].startswith("Indigo refused add_tag(1, 'kitchen')")


def test_remove_whose_read_back_still_shows_the_tag_is_not_verified(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]))
    fake.device.remove_tag = lambda obj_id, tag: None  # accepted, changed nothing
    out = _tools(fake)["tag_objects"](tag="kitchen", action="remove", objects=[{"type": "device", "id": 1}])
    assert out["results"][0]["outcome"] == "error"
    assert out["results"][0]["message"] == "command accepted but read-back does not show the change"
    assert out["verified"] is False


_SIX = [
    ("device", "devices", "device"),
    ("variable", "variables", "variable"),
    ("trigger", "triggers", "trigger"),
    ("schedule", "schedules", "schedule"),
    ("action_group", "actionGroups", "actionGroup"),
    ("control_page", "controlPages", "controlPage"),
]


@pytest.mark.parametrize("type_name, coll_attr, ns_attr", _SIX)
def test_add_then_remove_reaches_the_right_namespace_for_every_type(fake, type_name, coll_attr, ns_attr):
    fake.put(coll_attr, _Obj(7, "Thing"))
    calls = []
    for _type, other_coll, other_ns in _SIX:
        if other_coll != coll_attr:
            getattr(fake, other_coll).forbid_lookup = True
            getattr(fake, other_ns).add_tag = _forbid(f"{other_ns}.add_tag")
            getattr(fake, other_ns).remove_tag = _forbid(f"{other_ns}.remove_tag")
    ns = getattr(fake, ns_attr)
    real_add, real_remove = ns.add_tag, ns.remove_tag
    ns.add_tag = lambda obj_id, tag: (calls.append(("add_tag", obj_id, tag)), real_add(obj_id, tag))
    ns.remove_tag = lambda obj_id, tag: (calls.append(("remove_tag", obj_id, tag)), real_remove(obj_id, tag))
    tools = _tools(fake)
    objects = [{"type": type_name, "id": 7}]

    added = tools["tag_objects"](tag="kitchen", action="add", objects=objects)
    assert added["results"][0]["outcome"] == "added" and added["verified"] is True
    assert getattr(fake, coll_attr).objs[7].tags == ["kitchen"]
    removed = tools["tag_objects"](tag="kitchen", action="remove", objects=objects)
    assert removed["results"][0]["outcome"] == "removed" and removed["verified"] is True
    assert calls == [("add_tag", 7, "kitchen"), ("remove_tag", 7, "kitchen")]
    assert getattr(fake, coll_attr).objs[7].tags == []


def test_dry_run_of_26_removes_previews_without_asking_for_confirm(fake):
    fake.put("devices", *[_Obj(i, f"L{i}", tags=["kitchen"]) for i in range(26)])
    fake.device.remove_tag = _forbid("remove_tag")
    out = _tools(fake)["tag_objects"](tag="kitchen", action="remove", dry_run=True,
                                      objects=[{"type": "device", "id": i} for i in range(26)])
    assert "requires_confirmation" not in out
    assert out["summary"] == {"error": 0, "would_remove": 26} and out["verified"] is None


def test_the_remove_threshold_counts_entries_not_carriers(fake):
    # 26 entries, none carrying the tag: the gate runs before anything is read.
    fake.device.remove_tag = _forbid("remove_tag")
    out = _tools(fake)["tag_objects"](tag="kitchen", action="remove",
                                      objects=[{"type": "device", "id": i} for i in range(26)])
    assert out["requires_confirmation"] is True
    assert "26 entries" in out["message"] and "objects" not in out["message"].split("needs")[0]


def test_duplicate_entries_are_dropped_up_front(fake):
    fake.put("devices", _Obj(1, "Lamp"))
    objects = [{"type": "device", "id": 1}, {"type": "device", "id": 1}, {"type": "variable", "id": 1}]
    tools = _tools(fake)
    preview = tools["tag_objects"](tag="kitchen", action="add", dry_run=True, objects=objects)
    real = tools["tag_objects"](tag="kitchen", action="add", objects=objects)
    assert [r["outcome"] for r in preview["results"]] == ["would_add", "not_found"]
    assert [r["outcome"] for r in real["results"]] == ["added", "not_found"]
    for out in (preview, real):
        assert out["requested"] == 3 and out["duplicates_ignored"] == 1


def test_duplicates_ignored_is_zero_when_clean(fake):
    out = _tools(fake)["tag_objects"](tag="kitchen", action="add", objects=[{"type": "device", "id": 1}])
    assert out["duplicates_ignored"] == 0


# ---------------------------------------------------------------------
# review round 1: _dispatch buckets
# ---------------------------------------------------------------------

class ValidationError(Exception):
    """Same name as indigo.utils.ValidationError; matched by name."""


@pytest.mark.parametrize("exc", [OSError("socket closed"), RuntimeError("server hiccup"),
                                 UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")])
def test_a_non_refusal_failure_is_a_dispatched_runtime_error(fake, exc):
    def create_tag(_name, _color):
        raise exc

    fake.server.create_tag = create_tag
    with pytest.raises(RuntimeError) as err:
        _tools(fake)["manage_tags"](action="create", tag="garden")
    assert "WAS DISPATCHED" in str(err.value) and "may or may not have taken effect" in str(err.value)


def test_a_validation_error_is_a_refusal(fake):
    def create_tag(_name, _color):
        raise ValidationError("bad colour")

    fake.server.create_tag = create_tag
    with pytest.raises(ValueError, match="Indigo refused create_tag"):
        _tools(fake)["manage_tags"](action="create", tag="garden")


def test_a_plain_value_error_from_a_create_race_is_a_refusal(fake):
    def create_tag(name, _color):
        fake.server.add_library(name)  # someone else created it first
        raise ValueError("tag exists")

    fake.server.create_tag = create_tag
    with pytest.raises(ValueError, match="Indigo refused create_tag\\('garden'\\): tag exists"):
        _tools(fake)["manage_tags"](action="create", tag="garden")


def test_a_json_decode_error_after_the_write_is_not_a_refusal(fake):
    def create_tag(name, color):
        _Server.create_tag(fake.server, name, color)
        json.loads("{not json")

    fake.server.create_tag = create_tag
    with pytest.raises(RuntimeError, match="WAS DISPATCHED") as err:
        _tools(fake)["manage_tags"](action="create", tag="garden")
    assert "JSONDecodeError" in str(err.value)
    assert "garden" in fake.server.library  # it DID take effect


def test_create_passes_name_and_colour_positionally(fake):
    seen = []

    def create_tag(*args, **kwargs):
        seen.append((args, kwargs))
        _Server.create_tag(fake.server, *args)

    fake.server.create_tag = create_tag
    _tools(fake)["manage_tags"](action="create", tag="garden", color="#00ff00")
    assert seen == [(("garden", "00FF00"), {})]


@pytest.mark.parametrize("args, sent", [
    ({"new_tag": "cooking"}, {"new_tag": "cooking"}),
    ({"color": "#123456"}, {"new_color": "123456"}),
    ({"new_tag": "cooking", "color": "123456"}, {"new_tag": "cooking", "new_color": "123456"}),
    ({"new_tag": "cooking", "color": None}, {"new_tag": "cooking"}),
])
def test_update_sends_only_the_kwargs_given(fake, args, sent):
    seen = []

    def update_tag(name, **kwargs):
        seen.append((name, kwargs))
        _Server.update_tag(fake.server, name, **kwargs)

    fake.server.update_tag = update_tag
    out = _tools(fake)["manage_tags"](action="update", tag="kitchen", **args)
    assert seen == [("kitchen", sent)]
    assert out["accepted"] is True and out["verified"] is True


def test_the_fake_update_tag_rejects_none(fake):
    # Guards the guard: the strict fake must catch a None kwarg.
    with pytest.raises(TypeError):
        fake.server.update_tag("kitchen", new_tag=None, new_color="000000")


# ---------------------------------------------------------------------
# review round 1: library read-back
# ---------------------------------------------------------------------

def test_lower_case_hash_colour_in_the_library_still_verifies(fake):
    real_add = fake.server.add_library

    def add_library(name, color="4A90D9"):
        real_add(name, "#" + color.lower())

    fake.server.add_library = add_library
    tools = _tools(fake)
    created = tools["manage_tags"](action="create", tag="garden", color="4A90D9")
    assert created["verified"] is True

    def update_tag(name, **kwargs):
        fake.server.library[name]["color"] = "#" + kwargs["new_color"].lower()

    fake.server.update_tag = update_tag
    updated = tools["manage_tags"](action="update", tag="garden", color="112233")
    assert updated["verified"] is True


def test_rename_that_leaves_the_old_tag_is_not_verified(fake):
    def update_tag(name, **kwargs):
        fake.server.add_library(kwargs["new_tag"])  # copied, old one left behind

    fake.server.update_tag = update_tag
    out = _tools(fake)["manage_tags"](action="update", tag="kitchen", new_tag="cooking")
    assert out["accepted"] is True and out["verified"] is False and "note" in out


def test_delete_that_leaves_the_tag_is_not_verified(fake):
    fake.server.delete_tag = lambda name: None
    out = _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen", confirm=True)
    assert out["accepted"] is True and out["verified"] is False and "note" in out


class _ReadsThenBreaks(_Server):
    """A library that can be read ``good_reads`` times, then raises."""

    good_reads = 1

    @property
    def tags(self):
        self.good_reads -= 1
        if self.good_reads < 0:
            raise RuntimeError("library went away")
        return {name: dict(entry) for name, entry in self.library.items()}


@pytest.mark.parametrize("args", [
    {"action": "create", "tag": "garden"},
    {"action": "update", "tag": "kitchen", "color": "000000"},
    {"action": "delete", "tag": "kitchen", "confirm": True},
])
def test_a_failed_read_back_is_verified_null_with_the_reason(fake, args):
    server = _ReadsThenBreaks(fake)
    server.library = fake.server.library
    fake.server = server
    out = _tools(fake, allow_delete=True)["manage_tags"](**args)
    assert out["accepted"] is True and out["verified"] is None
    assert "library went away" in out["read_back_error"]


# ---------------------------------------------------------------------
# review round 1: delete preview completeness
# ---------------------------------------------------------------------

def test_delete_preview_with_every_collection_unreadable_is_a_failed_call(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    for coll in fake.collections():
        coll.iter_error = RuntimeError("unavailable")
    with pytest.raises(ValueError, match="no object collection could be read"):
        _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen")


def test_delete_preview_with_no_readable_object_is_a_failed_call(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    fake.put("devices", _BadTags(1, "Lamp"))
    with pytest.raises(ValueError, match="no object's tags could be read"):
        _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen")


def test_delete_preview_message_says_the_count_is_a_lower_bound(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]), _BadTags(2, "Fan"), _BadTags(3, "Heater"))
    fake.variables.iter_error = RuntimeError("variables unavailable")
    out = _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen")
    assert out["carrier_count_complete"] is False and out["attr_read_errors"] == 2
    assert out["attr_read_error_example"] == "RuntimeError: tags exploded"
    assert ("at least 1 object(s) (count incomplete: variable unreadable, 2 objects' tags unreadable)"
            in out["message"])


def test_confirmed_delete_on_an_incomplete_count_is_refused(fake):
    fake.server.delete_tag = _forbid("delete_tag")
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]))
    fake.variables.iter_error = RuntimeError("variables unavailable")
    with pytest.raises(ValueError) as exc:
        _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen", confirm=True)
    text = str(exc.value)
    assert "Nothing was deleted" in text and "at least 1 object(s)" in text and "Retry" in text
    assert "kitchen" in fake.server.library


def test_a_delete_that_fails_after_dispatch_names_the_carrier_count(fake):
    fake.put("devices", _Obj(1, "Lamp", tags=["kitchen"]), _Obj(2, "Fan", tags=["kitchen"]))

    def delete_tag(_name):
        raise OSError("connection reset")

    fake.server.delete_tag = delete_tag
    with pytest.raises(RuntimeError) as exc:
        _tools(fake, allow_delete=True)["manage_tags"](action="delete", tag="kitchen", confirm=True)
    assert "WAS DISPATCHED" in str(exc.value) and "carried by 2 object(s)" in str(exc.value)


def test_delete_without_a_pref_callable_is_refused(fake):
    from tools import tags

    capture = _Capture()
    tags.register(capture, indigo_module=fake)
    fake.server.delete_tag = _forbid("delete_tag")
    with pytest.raises(ValueError, match="allowTagDelete"):
        capture.tools["manage_tags"](action="delete", tag="kitchen", confirm=True)


# ---------------------------------------------------------------------
# review round 1: list_tagged completeness and pagination
# ---------------------------------------------------------------------

def test_list_tagged_counts_unreadable_tags(fake):
    _listing_fake(fake)
    fake.put("devices", _BadTags(4, "Mystery"), _BadTags(5, "Mystery 2"))
    out = _tools(fake)["list_tagged"](tag="kitchen")
    assert out["total_count"] == 5 and out["total_count_complete"] is False
    assert out["attr_read_errors"] == 2 and out["collections_unreadable"] == []
    assert out["attr_read_error_example"] == "RuntimeError: tags exploded"


def test_list_tagged_with_no_readable_object_is_a_failed_call(fake):
    fake.put("devices", _BadTags(1, "Lamp"))
    with pytest.raises(ValueError, match="no object's tags could be read"):
        _tools(fake)["list_tagged"](tag="kitchen")


def test_list_tagged_on_an_empty_server_is_an_honest_empty_list(fake):
    out = _tools(fake)["list_tagged"](tag="kitchen")
    assert out["results"] == [] and out["total_count"] == 0 and out["total_count_complete"] is True


def test_list_tagged_has_more_is_false_exactly_at_the_end(fake):
    _listing_fake(fake)
    out = _tools(fake)["list_tagged"](tag="kitchen", limit=2, offset=3)
    assert len(out["results"]) == 2 and out["total_count"] == 5 and out["has_more"] is False


def test_list_tagged_offset_past_the_end(fake):
    _listing_fake(fake)
    out = _tools(fake)["list_tagged"](tag="kitchen", offset=50)
    assert out["results"] == [] and out["total_count"] == 5 and out["has_more"] is False


@pytest.mark.parametrize("args", [{"limit": "abc"}, {"limit": True}, {"offset": False}])
def test_list_tagged_refuses_non_integer_pagination(fake, args):
    with pytest.raises(ValueError, match="must be an integer|must be integers"):
        _tools(fake)["list_tagged"](tag="kitchen", **args)


# ---------------------------------------------------------------------
# review round 1: booleans and the version precondition
# ---------------------------------------------------------------------

_ABSENT = object()


@pytest.mark.parametrize("given, expected", [
    (_ABSENT, False), (None, False), (True, True), (False, False), ("true", ValueError), ("yes", ValueError),
    (1, ValueError),
])
def test_dry_run_flag_parsing(fake, given, expected):
    fake.put("devices", _Obj(1, "Lamp"))
    args = {"tag": "kitchen", "action": "add", "objects": [{"type": "device", "id": 1}]}
    if given is not _ABSENT:
        args["dry_run"] = given
    tool = _tools(fake)["tag_objects"]
    if expected is ValueError:
        with pytest.raises(ValueError, match="dry_run must be true or false"):
            tool(**args)
        assert fake.devices.objs[1].tags == []
    else:
        assert tool(**args)["dry_run"] is expected


@pytest.mark.parametrize("tool, args", [
    ("manage_tags", {"action": "explode", "tag": "Bad Name", "color": "nope", "bogus": 1}),
    ("tag_objects", {"tag": "", "action": "smash", "objects": "none", "bogus": 1}),
    ("list_tagged", {"type": "folder", "limit": "abc", "bogus": 1}),
])
def test_the_version_message_wins_over_invalid_arguments(fake, tool, args):
    fake.server = _OldServer()
    with pytest.raises(ValueError) as exc:
        _tools(fake, allow_delete=True)[tool](**args)
    assert str(exc.value) == "Tags need Indigo 2026.1 or later; this server is 2025.2.5."


class _NoVersionServer:
    tags = {}


@pytest.mark.parametrize("server", [_NoVersionServer(), None])
def test_an_unknown_version_is_named_as_such(fake, server):
    if server is None:
        del fake.server
    else:
        fake.server = server
    with pytest.raises(ValueError) as exc:
        _tools(fake)["list_tagged"](tag="kitchen")
    assert str(exc.value) == "Tags need Indigo 2026.1 or later; this server is an unknown version."


# ---------------------------------------------------------------------
# review round 1: more wire round trips
# ---------------------------------------------------------------------

def _wire(fake):
    from mcp_handler import MCPHandler
    from tool_registry import register_all

    handler = MCPHandler(server_name="test", server_version="0")
    register_all(handler, indigo_module=fake, allow_tag_delete=lambda: True)
    return handler


def _call_error(handler, name, arguments):
    response = handler.handle_request(
        http_method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}}),
    )
    payload = json.loads(response["content"])
    assert "result" not in payload, payload
    return payload["error"]


def test_a_dispatch_fault_reaches_the_wire_as_an_internal_error(fake):
    def create_tag(_name, _color):
        raise OSError("socket closed")

    fake.server.create_tag = create_tag
    error = _call_error(_wire(fake), "manage_tags", {"action": "create", "tag": "garden"})
    assert error["code"] == -32603 and "WAS DISPATCHED" in error["message"]


def test_remove_dry_run_and_confirm_round_trip(fake):
    fake.put("devices", *[_Obj(i, f"L{i}", tags=["kitchen"]) for i in range(26)])
    handler = _wire(fake)
    objects = [{"type": "device", "id": i} for i in range(26)]

    res, body = _call(handler, "tag_objects", {"tag": "kitchen", "action": "remove", "objects": objects,
                                               "dry_run": True})
    assert not res.get("isError") and body["summary"] == {"error": 0, "would_remove": 26}

    res, body = _call(handler, "tag_objects", {"tag": "kitchen", "action": "remove", "objects": objects})
    assert not res.get("isError") and body["requires_confirmation"] is True

    res, body = _call(handler, "tag_objects", {"tag": "kitchen", "action": "remove", "objects": objects,
                                               "confirm": True})
    assert not res.get("isError") and body["summary"] == {"error": 0, "removed": 26}
    assert body["verified"] is True and all(r["dispatched"] for r in body["results"])


def test_delete_confirm_and_pagination_round_trip(fake):
    _listing_fake(fake)
    handler = _wire(fake)

    res, body = _call(handler, "list_tagged", {"tag": "kitchen", "limit": 2, "offset": 2})
    assert not res.get("isError") and [r["id"] for r in body["results"]] == [10, 20]
    assert body["has_more"] is True and body["total_count_complete"] is True

    res, body = _call(handler, "manage_tags", {"action": "delete", "tag": "kitchen", "confirm": True})
    assert not res.get("isError") and body["accepted"] is True and body["verified"] is True
    assert body["carrier_count"] == 5 and "kitchen" not in fake.server.library
