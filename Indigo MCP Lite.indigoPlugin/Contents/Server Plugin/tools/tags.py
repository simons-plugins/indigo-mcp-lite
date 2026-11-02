"""Tag tools for Indigo 2026.1+ (API 3.9): create, apply and list tags.

Indigo 2026.1 added tags: labels any device, variable, trigger,
schedule, action group or control page can carry, drawn from one
server-wide library. The built-in MCP server at ``/v3/api/mcp`` READS
tags (and filters its ``list_*`` tools by them) but cannot create,
rename, recolour, delete or assign them, so these three tools are
extension tools in the sense of lite ADR-0003 and are registered on
every server.

The API, from https://docs.indigodomo.com/2026.1/scripting/reference/server-commands/
("Tag Library") and the per-type base-class pages ("Add Tag" /
"Remove Tag"):

- ``indigo.server.tags`` -- a COPY of the library,
  ``{name: {"color", "text_color", "border_color"}}``. Mutating it
  changes nothing on the server.
- ``indigo.server.create_tag(name, color)`` -- raises ``ValueError``
  if the tag exists.
- ``indigo.server.update_tag(name, new_tag=..., new_color=...)`` -- at
  least one of the two; renaming onto another tag's name raises.
- ``indigo.server.delete_tag(name)`` -- removes the tag from the
  library AND from every object carrying it. Cannot be undone.
- ``indigo.<namespace>.add_tag(id_or_obj, name)`` /
  ``remove_tag(id_or_obj, name)`` for the six namespaces in
  ``OBJECT_TYPES``. ``add_tag`` raises if the tag is not in the
  library and is a no-op if the object already has it; ``remove_tag``
  is a no-op if the object does not have it.
- Each object's ``tags`` is a read-only list of names.
- A malformed name or colour raises ``indigo.utils.ValidationError``;
  an unknown tag raises ``ValueError``.

Every argument is validated here before anything reaches the server,
so a refusal names the rule and the offending value instead of
surfacing whatever the server says. On a server without the tag API
(before 2026.1) every call is a failed call naming the version, never
an empty result.

Error buckets follow the rest of the repo: ``ValueError`` for anything
the caller can fix (``isError`` tool result); ``RuntimeError`` for a
fault after a command was dispatched, where blind retrying is wrong.
Every write is read back, and the payload says whether the read-back
confirmed it (``verified``).
"""

import re

from tools.lookup import _coerce_int, _normalize_pagination, _reject_unknown_args
from tools.zwave import _json_safe

#: Indigo's tag-name rule: lowercase letters separated by single dashes.
TAG_NAME_RE = re.compile(r"^[a-z]+(-[a-z]+)*$")
_COLOR_RE = re.compile(r"^[0-9A-F]{6}$")

#: Colour used when ``manage_tags(action="create")`` is given none.
DEFAULT_COLOR = "4A90D9"

#: Most objects one ``tag_objects`` call will touch.
MAX_OBJECTS = 200

#: Removing a tag from more objects than this needs ``confirm=true``.
REMOVE_CONFIRM_THRESHOLD = 25

#: Most carrier rows a delete confirmation lists (the count is exact).
CARRIER_LIST_CAP = 25

_DEFAULT_LIST_LIMIT = 100

#: Tool-facing type name -> (collection attribute, command namespace).
OBJECT_TYPES = {
    "device": ("devices", "device"),
    "variable": ("variables", "variable"),
    "trigger": ("triggers", "trigger"),
    "schedule": ("schedules", "schedule"),
    "action_group": ("actionGroups", "actionGroup"),
    "control_page": ("controlPages", "controlPage"),
}

_NAME_RULE = (
    "tag names are lowercase letters separated by single dashes "
    "(pattern ^[a-z]+(-[a-z]+)*$, e.g. 'kitchen', 'holiday-lighting'): "
    "no capitals, digits, spaces, punctuation, or leading/trailing/doubled dashes"
)


# -- preconditions ---------------------------------------------------------


def _require_tag_api(indigo_module):
    """Raise a friendly ValueError unless the server has the tag API.

    ``create_tag`` arrived with tags in Indigo 2026.1 (API 3.9), so its
    absence means an older server. That is a failed call, not an empty
    library: "no tags" and "this server cannot have tags" mean
    different things to the caller.
    """
    server = getattr(indigo_module, "server", None)
    if server is not None and hasattr(server, "create_tag"):
        return
    version = getattr(server, "version", None) if server is not None else None
    if not isinstance(version, str) or not version:
        version = "an unknown version"
    raise ValueError(f"Tags need Indigo 2026.1 or later; this server is {version}.")


def _suggest_name(name):
    """A valid tag name close to ``name``, or None."""
    candidate = re.sub(r"[^a-z]+", "-", name.lower()).strip("-")
    if candidate and candidate != name and TAG_NAME_RE.match(candidate):
        return candidate
    return None


def _require_tag_name(value, key):
    """``value`` as a valid tag name, or a ValueError quoting the rule."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required: a tag name ({_NAME_RULE})")
    if not TAG_NAME_RE.match(value):
        hint = _suggest_name(value)
        did_you_mean = f" Did you mean {hint!r}?" if hint else ""
        raise ValueError(f"{key} {value!r} is not a valid tag name: {_NAME_RULE}.{did_you_mean}")
    return value


def _require_str(value, key):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required and must be a non-empty string")
    return value


def _normalize_color(value):
    """``#4a90d9`` / ``4A90D9`` -> ``4A90D9``; anything else is refused."""
    if not isinstance(value, str):
        raise ValueError(f"color must be six hex digits such as '4A90D9' (a leading # is allowed); got {value!r}")
    cleaned = value.strip().lstrip("#").upper()
    if not _COLOR_RE.match(cleaned):
        raise ValueError(f"color must be six hex digits such as '4A90D9' (a leading # is allowed); got {value!r}")
    return cleaned


def _optional_bool(args, key):
    raw = args.get(key, False)
    if not isinstance(raw, bool):
        raise ValueError(f"{key} must be true or false; got {raw!r}")
    return raw


# -- library -----------------------------------------------------------------


def _read_library(indigo_module):
    """The tag library as a plain dict, or a ValueError if unreadable.

    Before a write an unreadable library is an unusable precondition:
    "is the tag there?" cannot be answered, so nothing is attempted.
    """
    try:
        raw = indigo_module.server.tags
        if not hasattr(raw, "items"):
            raise TypeError(f"expected a dictionary, got {type(raw).__name__}")
        return {str(name): entry for name, entry in raw.items()}
    except Exception as exc:  # pylint: disable=broad-except
        raise ValueError(
            f"Indigo's tag library (indigo.server.tags) could not be read ({type(exc).__name__}: {exc}); "
            "nothing was changed."
        ) from exc


def _read_back_library(indigo_module):
    """``(library, None)`` or ``(None, error text)`` after a write.

    A write has already happened, so a failed re-read must not turn
    into an exception that hides it: the caller reports
    ``verified: null`` with the reason instead.
    """
    try:
        return _read_library(indigo_module), None
    except ValueError as exc:
        return None, str(exc.__cause__ or exc)


def _entry(entry):
    """One library entry as ``{color, text_color, border_color}``."""
    if not hasattr(entry, "get"):
        return {"color": _json_safe(entry)}
    return {key: _json_safe(entry.get(key)) for key in ("color", "text_color", "border_color")}


def _unknown_tag(tag, library):
    return ValueError(
        f"tag {tag!r} is not in the tag library; known tags: {sorted(library)}. "
        "Create it first with manage_tags(action='create')."
    )


# -- dispatch ----------------------------------------------------------------


def _dispatch(command, what, *args, **kwargs):
    """Call a server tag command, sorting its failures into the buckets.

    Indigo validates a tag command before changing anything: a
    malformed value raises ``ValidationError`` and an unknown or
    already-existing tag raises ``ValueError``. Either is a refusal
    the caller can act on, so it stays a ValueError. Anything else is
    a fault after dispatch and becomes a RuntimeError telling the
    caller to read before retrying.
    """
    try:
        return command(*args, **kwargs)
    except Exception as exc:  # pylint: disable=broad-except
        if isinstance(exc, ValueError) or type(exc).__name__ == "ValidationError":
            raise ValueError(f"Indigo refused {what}: {exc}") from exc
        raise RuntimeError(
            f"{what} WAS DISPATCHED and failed ({type(exc).__name__}: {exc}); it may or may not "
            "have taken effect. Read the tag library back (list_tagged, or the built-in "
            "server's tag listing) before retrying."
        ) from exc


# -- object scans -------------------------------------------------------------


def _object_tags(obj):
    """An object's tags as a list of str; raises if unreadable."""
    tags = obj.tags
    if isinstance(tags, (str, bytes)) or not hasattr(tags, "__iter__"):
        raise TypeError(f"tags is {type(tags).__name__}, not a list")
    return [str(t) for t in tags]


def _row(type_name, obj):
    return {
        "type": type_name,
        "id": _json_safe(getattr(obj, "id", None)),
        "name": _json_safe(getattr(obj, "name", None)),
        "folder_id": _json_safe(getattr(obj, "folderId", None)),
        # Triggers have no Remote Display setting; None there.
        "remote_display": _json_safe(getattr(obj, "remoteDisplay", None)),
    }


def _scan_carriers(indigo_module, tag, types):
    """Every object of ``types`` carrying ``tag``.

    Returns ``(rows, collections_unreadable, attr_read_errors)``. A
    collection that cannot be iterated, and an object whose ``tags``
    cannot be read, are reported rather than skipped silently: either
    makes the row list incomplete, and a count used to warn about a
    delete must say when it is a lower bound.
    """
    rows, unreadable, attr_errors = [], [], 0
    for type_name in types:
        collection_attr = OBJECT_TYPES[type_name][0]
        try:
            objects = list(getattr(indigo_module, collection_attr))
        except Exception as exc:  # pylint: disable=broad-except
            unreadable.append({"type": type_name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for obj in objects:
            try:
                carries = tag in _object_tags(obj)
            except Exception:  # pylint: disable=broad-except
                attr_errors += 1
                continue
            if carries:
                rows.append(_row(type_name, obj))
    order = {name: i for i, name in enumerate(OBJECT_TYPES)}
    rows.sort(key=lambda r: (order[r["type"]], str(r["name"] or "").lower(), str(r["id"])))
    return rows, unreadable, attr_errors


# -- manage_tags ----------------------------------------------------------------


def _manage_tags_handler(args, indigo_module, allow_delete):
    _require_tag_api(indigo_module)
    _reject_unknown_args(args, ("action", "tag", "color", "new_tag", "confirm"))
    action = args.get("action")
    if action not in ("create", "update", "delete"):
        raise ValueError(f"action must be 'create', 'update' or 'delete'; got {action!r}")
    confirm = _optional_bool(args, "confirm")
    if action == "create":
        return _create(args, indigo_module)
    if action == "update":
        return _update(args, indigo_module)
    return _delete(args, indigo_module, allow_delete, confirm)


def _create(args, indigo_module):
    if args.get("new_tag") is not None:
        raise ValueError("new_tag applies only to action='update'")
    tag = _require_tag_name(args.get("tag"), "tag")
    color = DEFAULT_COLOR if args.get("color") is None else _normalize_color(args.get("color"))
    library = _read_library(indigo_module)
    if tag in library:
        existing = _entry(library[tag]).get("color")
        raise ValueError(
            f"tag {tag!r} already exists (color {existing}); nothing was changed. "
            "Use action='update' to rename or recolour it."
        )
    _dispatch(indigo_module.server.create_tag, f"create_tag({tag!r})", tag, color)
    after, read_error = _read_back_library(indigo_module)
    out = {"action": "create", "tag": tag, "created": True}
    if after is None:
        out.update({"entry": None, "verified": None, "read_back_error": read_error})
        return out
    entry = after.get(tag)
    out["entry"] = _entry(entry) if entry is not None else None
    out["verified"] = entry is not None and str(_entry(entry).get("color") or "").upper() == color
    if not out["verified"]:
        out["note"] = "create_tag was accepted but the library read back does not show the tag with this colour."
    return out


def _update(args, indigo_module):
    tag = _require_str(args.get("tag"), "tag")
    new_tag = args.get("new_tag")
    if new_tag is not None:
        new_tag = _require_tag_name(new_tag, "new_tag")
    color = None if args.get("color") is None else _normalize_color(args.get("color"))
    if new_tag is None and color is None:
        raise ValueError("action='update' needs new_tag (to rename), color (to recolour), or both")
    library = _read_library(indigo_module)
    if tag not in library:
        raise _unknown_tag(tag, library)
    if new_tag is not None and new_tag != tag and new_tag in library:
        raise ValueError(
            f"cannot rename {tag!r} to {new_tag!r}: a tag with that name already exists; nothing was changed."
        )
    kwargs = {}
    if new_tag is not None:
        kwargs["new_tag"] = new_tag
    if color is not None:
        kwargs["new_color"] = color
    _dispatch(indigo_module.server.update_tag, f"update_tag({tag!r})", tag, **kwargs)
    final = new_tag or tag
    renamed = final != tag
    out = {"action": "update", "tag": final, "previous_tag": tag, "renamed": renamed,
           "recolored": color is not None}
    after, read_error = _read_back_library(indigo_module)
    if after is None:
        out.update({"entry": None, "verified": None, "read_back_error": read_error})
        return out
    entry = after.get(final)
    out["entry"] = _entry(entry) if entry is not None else None
    verified = entry is not None
    if verified and color is not None:
        verified = str(_entry(entry).get("color") or "").upper() == color
    if verified and renamed:
        verified = tag not in after
    out["verified"] = verified
    if not verified:
        out["note"] = "update_tag was accepted but the library read back does not show the change."
    return out


def _delete(args, indigo_module, allow_delete, confirm):
    if args.get("new_tag") is not None or args.get("color") is not None:
        raise ValueError("action='delete' takes only tag and confirm")
    tag = _require_str(args.get("tag"), "tag")
    if not allow_delete():
        raise ValueError(
            "Deleting tags is turned off. A delete strips the tag from every object carrying it and "
            "cannot be undone, so it needs the 'Allow tag delete' plugin preference (allowTagDelete) "
            "turned on in Plugins > Indigo MCP Lite > Configure... Nothing was deleted."
        )
    library = _read_library(indigo_module)
    if tag not in library:
        raise _unknown_tag(tag, library)
    carriers, unreadable, attr_errors = _scan_carriers(indigo_module, tag, OBJECT_TYPES)
    out = {"action": "delete", "tag": tag, "carrier_count": len(carriers)}
    if unreadable or attr_errors:
        out["carrier_count_complete"] = False
        out["collections_unreadable"] = unreadable
        out["attr_read_errors"] = attr_errors
    if not confirm:
        out.update({
            "deleted": False,
            "requires_confirmation": True,
            "carriers": [{"type": r["type"], "id": r["id"], "name": r["name"]}
                         for r in carriers[:CARRIER_LIST_CAP]],
            "carriers_truncated": len(carriers) > CARRIER_LIST_CAP,
            "message": (
                f"Nothing was deleted. Deleting {tag!r} removes it from the library and from the "
                f"{len(carriers)} object(s) carrying it, and cannot be undone. Call again with "
                "confirm=true to delete."
            ),
        })
        return out
    _dispatch(indigo_module.server.delete_tag, f"delete_tag({tag!r})", tag)
    out["deleted"] = True
    after, read_error = _read_back_library(indigo_module)
    if after is None:
        out.update({"verified": None, "read_back_error": read_error})
    else:
        out["verified"] = tag not in after
        if not out["verified"]:
            out["note"] = "delete_tag was accepted but the tag is still in the library read back."
    return out


# -- tag_objects -------------------------------------------------------------------


def _parse_objects(raw):
    """Validate the whole ``objects`` list before anything is touched."""
    if not isinstance(raw, list) or not raw:
        raise ValueError('objects must be a non-empty list of {"type": ..., "id": ...}')
    if len(raw) > MAX_OBJECTS:
        raise ValueError(f"objects has {len(raw)} entries; at most {MAX_OBJECTS} per call. Split it into batches.")
    parsed = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'objects[{index}] must be an object {{"type": ..., "id": ...}}; got {item!r}')
        type_name = item.get("type")
        if type_name not in OBJECT_TYPES:
            raise ValueError(f"objects[{index}].type must be one of {list(OBJECT_TYPES)}; got {type_name!r}")
        obj_id = _coerce_int(item.get("id"))
        if obj_id is None or obj_id < 0:
            raise ValueError(f"objects[{index}].id must be a non-negative integer; got {item.get('id')!r}")
        parsed.append((type_name, obj_id))
    return parsed


def _inspect(indigo_module, type_name, obj_id, tag):
    """Look one object up and read its tags.

    Returns ``((collection, has_tag), fragment)`` or ``(None,
    fragment)`` when the object cannot be used; ``fragment`` holds the
    row fields learned so far (name, or the outcome and message). A
    ``KeyError`` is "no such object"; anything else means the lookup
    itself failed, which is a different answer.
    """
    try:
        collection = getattr(indigo_module, OBJECT_TYPES[type_name][0])
        obj = collection[obj_id]
    except KeyError:
        return None, {"outcome": "not_found"}
    except Exception as exc:  # pylint: disable=broad-except
        return None, {"outcome": "error", "message": f"lookup failed: {type(exc).__name__}: {exc}"}
    fragment = {"name": _json_safe(getattr(obj, "name", None))}
    try:
        has_tag = tag in _object_tags(obj)
    except Exception as exc:  # pylint: disable=broad-except
        fragment.update({"outcome": "error", "message": f"tags unreadable: {type(exc).__name__}: {exc}"})
        return None, fragment
    return (collection, has_tag), fragment


def _change(indigo_module, collection, *, type_name, obj_id, tag, adding):
    """Dispatch one add/remove and read the object back.

    Returns ``(fragment, confirmed)``. Indigo objects are copies, so
    the read-back re-fetches the object rather than re-reading the
    one already in hand.
    """
    verb = "add_tag" if adding else "remove_tag"
    try:
        getattr(getattr(indigo_module, OBJECT_TYPES[type_name][1]), verb)(obj_id, tag)
    except Exception as exc:  # pylint: disable=broad-except
        return {"outcome": "error", "message": f"{verb} failed: {type(exc).__name__}: {exc}"}, False
    try:
        has_after = tag in _object_tags(collection[obj_id])
    except Exception as exc:  # pylint: disable=broad-except
        return {"outcome": "error",
                "message": f"command accepted but read-back failed: {type(exc).__name__}: {exc}"}, False
    if has_after != adding:
        return {"outcome": "error", "message": "command accepted but read-back does not show the change"}, False
    return {"outcome": "added" if adding else "removed"}, True


def _tag_one(indigo_module, item, tag, *, adding, dry_run):
    """One object's row, and whether a dispatched change was confirmed
    (True), refuted or unconfirmed (False), or never attempted (None).
    Never raises an ``Exception``: one bad object must not abort the
    batch."""
    type_name, obj_id = item
    out = {"type": type_name, "id": obj_id}
    found, fragment = _inspect(indigo_module, type_name, obj_id, tag)
    out.update(fragment)
    if found is None:
        return out, None
    collection, has_tag = found
    if has_tag == adding:
        out["outcome"] = "already" if adding else "absent"
    elif dry_run:
        out["outcome"] = "would_add" if adding else "would_remove"
    else:
        fragment, confirmed = _change(indigo_module, collection, type_name=type_name,
                                      obj_id=obj_id, tag=tag, adding=adding)
        out.update(fragment)
        return out, confirmed
    return out, None


def _needs_remove_confirmation(tag, action, objects, args):
    """The requires_confirmation payload for a large remove, or None."""
    if action != "remove" or args["dry_run"] or args["confirm"] or len(objects) <= REMOVE_CONFIRM_THRESHOLD:
        return None
    return {
        "tag": tag, "action": action, "dry_run": False, "requires_confirmation": True,
        "requested": len(objects), "verified": None,
        "message": (
            f"Nothing was changed. Removing {tag!r} from {len(objects)} objects (more than "
            f"{REMOVE_CONFIRM_THRESHOLD}) needs confirm=true; dry_run=true previews it."
        ),
    }


def _tag_objects_handler(args, indigo_module):
    _require_tag_api(indigo_module)
    _reject_unknown_args(args, ("tag", "action", "objects", "dry_run", "confirm"))
    tag = _require_str(args.get("tag"), "tag")
    action = args.get("action")
    if action not in ("add", "remove"):
        raise ValueError(f"action must be 'add' or 'remove'; got {action!r}")
    objects = _parse_objects(args.get("objects"))
    flags = {"dry_run": _optional_bool(args, "dry_run"), "confirm": _optional_bool(args, "confirm")}
    library = _read_library(indigo_module)
    if tag not in library:
        raise _unknown_tag(tag, library)
    pending = _needs_remove_confirmation(tag, action, objects, flags)
    if pending is not None:
        return pending
    results, confirmations = [], []
    for item in objects:
        row, confirmed = _tag_one(indigo_module, item, tag, adding=action == "add", dry_run=flags["dry_run"])
        results.append(row)
        if confirmed is not None:
            confirmations.append(confirmed)
    summary = {}
    for row in results:
        summary[row["outcome"]] = summary.get(row["outcome"], 0) + 1
    return {
        "tag": tag,
        "action": action,
        "dry_run": flags["dry_run"],
        # null on a dry run: nothing was changed, so nothing to verify.
        "verified": None if flags["dry_run"] else all(confirmations),
        "requested": len(objects),
        "summary": summary,
        "results": results,
    }


# -- list_tagged -------------------------------------------------------------------


def _list_tagged_handler(args, indigo_module):
    _require_tag_api(indigo_module)
    _reject_unknown_args(args, ("tag", "type", "limit", "offset"))
    tag = _require_str(args.get("tag"), "tag")
    type_name = args.get("type")
    if type_name is not None and type_name not in OBJECT_TYPES:
        raise ValueError(f"type must be one of {list(OBJECT_TYPES)}; got {type_name!r}")
    try:
        limit, offset = _normalize_pagination(
            {"limit": args.get("limit", _DEFAULT_LIST_LIMIT), "offset": args.get("offset", 0)}
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit and offset must be integers ({exc})") from exc
    library = _read_library(indigo_module)
    if tag not in library:
        # A mistyped tag is a failed call, not "nothing carries it".
        raise _unknown_tag(tag, library)
    types = [type_name] if type_name else list(OBJECT_TYPES)
    rows, unreadable, attr_errors = _scan_carriers(indigo_module, tag, types)
    if unreadable and len(unreadable) == len(types):
        raise ValueError(
            f"no object collection could be read ({unreadable}); cannot say what carries {tag!r}."
        )
    total = len(rows)
    out = {
        "tag": tag,
        "results": rows[offset:offset + limit],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }
    if unreadable or attr_errors:
        # The list is incomplete; say so rather than pass it off as whole.
        out["collections_unreadable"] = unreadable
        out["attr_read_errors"] = attr_errors
    return out


# -- registration --------------------------------------------------------------------

_TYPE_ENUM = list(OBJECT_TYPES)


def register(handler, *, indigo_module, allow_tag_delete=None, **_):
    """Register ``manage_tags``, ``tag_objects`` and ``list_tagged``.

    ``allow_tag_delete`` is a zero-argument callable returning the
    ``allowTagDelete`` plugin preference; it is called on every delete
    so a changed preference applies without a restart. Absent, deletes
    are refused.
    """
    allow_delete = allow_tag_delete or (lambda: False)
    handler.register_tool(
        name="manage_tags",
        description=(
            "Create, rename/recolour, or delete a tag in Indigo's tag library (Indigo 2026.1+). "
            "Tag names are lowercase letters separated by single dashes ('kitchen', "
            "'holiday-lighting'); colours are six hex digits ('4A90D9' or '#4a90d9', default "
            "4A90D9). create refuses a tag that already exists; update needs new_tag and/or "
            "color. delete is gated twice: the plugin's 'Allow tag delete' preference must be "
            "on, AND confirm must be true. Without confirm it deletes nothing and returns "
            "requires_confirmation with how many objects carry the tag (deleting strips it "
            "from all of them and cannot be undone). Every change is read back from the "
            "library and reported as `verified`. Assign tags to objects with tag_objects."
        ),
        input_schema={
            "type": "object",
            "required": ["action", "tag"],
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "delete"]},
                "tag": {"type": "string", "description": "tag name (the existing name for update/delete)"},
                "color": {"type": "string", "description": "six hex digits, optional leading #"},
                "new_tag": {"type": "string", "description": "update only: the new name"},
                "confirm": {"type": "boolean", "description": "delete only: true to actually delete"},
            },
        },
        handler=lambda **args: _manage_tags_handler(args, indigo_module, allow_delete),
    )
    handler.register_tool(
        name="tag_objects",
        description=(
            "Add a tag to, or remove it from, up to 200 Indigo objects in one call (Indigo "
            "2026.1+). objects is a list of {type, id} with type one of device, variable, "
            "trigger, schedule, action_group, control_page. The tag must already exist "
            "(create it with manage_tags; it is never created here). Each object gets an "
            "outcome: added, already, removed, absent, not_found, or error (with a message); "
            "one failing object never stops the rest. dry_run=true changes nothing and "
            "reports would_add / would_remove instead. Removing from more than 25 objects "
            "needs confirm=true. Every change is read back from the object; `verified` is "
            "false if any read-back does not show it (that object's outcome is then error)."
        ),
        input_schema={
            "type": "object",
            "required": ["tag", "action", "objects"],
            "properties": {
                "tag": {"type": "string"},
                "action": {"type": "string", "enum": ["add", "remove"]},
                "objects": {
                    "type": "array",
                    "maxItems": MAX_OBJECTS,
                    "items": {
                        "type": "object",
                        "required": ["type", "id"],
                        "properties": {
                            "type": {"type": "string", "enum": _TYPE_ENUM},
                            "id": {"type": "integer"},
                        },
                    },
                },
                "dry_run": {"type": "boolean"},
                "confirm": {"type": "boolean", "description": "needed to remove from more than 25 objects"},
            },
        },
        handler=lambda **args: _tag_objects_handler(args, indigo_module),
    )
    handler.register_tool(
        name="list_tagged",
        description=(
            "Every Indigo object carrying a tag (Indigo 2026.1+), across all six types or one "
            "`type`, as {type, id, name, folder_id, remote_display} rows with pagination. "
            "Unlike the built-in MCP server's list_devices(tag=...), this includes objects "
            "WITHOUT Remote Display turned on (remote_display: false), so it shows what the "
            "built-in server's tag filter cannot see. remote_display is null for triggers, "
            "which have no such setting. An unknown tag is an error listing the library, "
            "never an empty result."
        ),
        input_schema={
            "type": "object",
            "required": ["tag"],
            "properties": {
                "tag": {"type": "string"},
                "type": {"type": "string", "enum": _TYPE_ENUM},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        handler=lambda **args: _list_tagged_handler(args, indigo_module),
    )
