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
Every write is read back. ``accepted`` says only that Indigo took the
command without raising; ``verified`` says whether the read-back
showed the change, and is the field to trust.
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


def _same_color(stored, wanted):
    """Whether a library colour (``#4a90d9``, ``4A90D9``...) is ``wanted``.

    ``wanted`` is already normalised; the stored side is normalised the
    same way so a server that keeps a ``#`` or lower case does not read
    as a failed write.
    """
    return str(stored or "").strip().lstrip("#").upper() == wanted


def _optional_bool(args, key):
    """``args[key]`` as a bool; absent or None is False.

    Strings, including "true" and "yes", are refused rather than
    guessed at, as everywhere else in the repo.
    """
    raw = args.get(key)
    if raw is None:
        return False
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


def _is_refusal(exc):
    """Whether ``exc`` is one of the two documented tag-command refusals.

    The documented shapes are exactly ``ValueError`` (unknown or
    already-existing tag) and ``indigo.utils.ValidationError``
    (malformed value). Matched exactly: a ValueError SUBCLASS such as
    ``json.JSONDecodeError`` or ``UnicodeDecodeError`` is a fault in
    whatever ran after the command was sent, not a refusal.
    """
    return type(exc) is ValueError or type(exc).__name__ == "ValidationError"  # pylint: disable=unidiomatic-typecheck


def _dispatch(command, what, *args, **kwargs):
    """Call a server tag command, sorting its failures into the buckets.

    A documented refusal (see ``_is_refusal``) stays a ValueError the
    caller can act on. This code cannot tell whether Indigo checks a
    command before or after changing anything, so every other failure
    becomes a RuntimeError saying the command was dispatched and may
    have taken effect: read before retrying.
    """
    try:
        return command(*args, **kwargs)
    except Exception as exc:  # pylint: disable=broad-except
        if _is_refusal(exc):
            raise ValueError(f"Indigo refused {what}: {exc}") from exc
        raise RuntimeError(
            f"{what} WAS DISPATCHED and failed ({type(exc).__name__}: {exc}); it may or may not "
            "have taken effect. Read the current state back (list_tagged, or the built-in "
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
    """Every object of ``types`` carrying ``tag``, and how complete that is.

    Returns a dict: ``rows``; ``collections_unreadable`` (one
    ``{type, error}`` per collection that could not be iterated);
    ``attr_read_errors`` (objects whose ``tags`` could not be read) with
    the first failure as ``attr_read_error_example``; ``readable`` (objects
    whose tags were read); and ``complete``. Failures are reported,
    never skipped silently: either makes the row list incomplete, and a
    count used to warn about a delete must say when it is a lower bound.
    """
    scan = {"rows": [], "collections_unreadable": [], "attr_read_errors": 0,
            "attr_read_error_example": None, "readable": 0}
    for type_name in types:
        try:
            objects = list(getattr(indigo_module, OBJECT_TYPES[type_name][0]))
        except Exception as exc:  # pylint: disable=broad-except
            scan["collections_unreadable"].append({"type": type_name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for obj in objects:
            try:
                row = _row(type_name, obj) if tag in _object_tags(obj) else None
            except Exception as exc:  # pylint: disable=broad-except
                scan["attr_read_errors"] += 1
                if scan["attr_read_error_example"] is None:
                    scan["attr_read_error_example"] = f"{type(exc).__name__}: {exc}"
                continue
            scan["readable"] += 1
            if row is not None:
                scan["rows"].append(row)
    order = {name: i for i, name in enumerate(OBJECT_TYPES)}
    scan["rows"].sort(key=lambda r: (order[r["type"]], str(r["name"] or "").lower(), str(r["id"])))
    scan["complete"] = not scan["collections_unreadable"] and not scan["attr_read_errors"]
    return scan


def _require_readable_scan(scan, tag, types):
    """Raise unless the scan read at least one object's tags.

    When something failed and nothing was readable, "0 objects carry
    the tag" would be a confident wrong answer: a failed call instead.
    """
    if scan["complete"] or scan["readable"]:
        return
    unreadable = scan["collections_unreadable"]
    if len(unreadable) == len(types):
        raise ValueError(f"no object collection could be read ({unreadable}); cannot say what carries {tag!r}.")
    raise ValueError(
        f"no object's tags could be read (collections unreadable: {unreadable}; "
        f"{scan['attr_read_errors']} object(s)' tags unreadable, e.g. {scan['attr_read_error_example']}); "
        f"cannot say what carries {tag!r}."
    )


def _scan_report(scan, complete_key):
    """The completeness fields every scan-based payload carries, clean or not."""
    return {
        complete_key: scan["complete"],
        "collections_unreadable": scan["collections_unreadable"],
        "attr_read_errors": scan["attr_read_errors"],
        "attr_read_error_example": scan["attr_read_error_example"],
    }


def _carrier_text(scan):
    """``the N object(s)``, or a lower bound saying what could not be read."""
    count = len(scan["rows"])
    if scan["complete"]:
        return f"the {count} object(s)"
    cols = ", ".join(u["type"] for u in scan["collections_unreadable"]) or "no collections"
    return (f"at least {count} object(s) (count incomplete: {cols} unreadable, "
            f"{scan['attr_read_errors']} objects' tags unreadable)")


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
    out = {"action": "create", "tag": tag, "accepted": True}
    if after is None:
        out.update({"entry": None, "verified": None, "read_back_error": read_error})
        return out
    entry = after.get(tag)
    out["entry"] = _entry(entry) if entry is not None else None
    out["verified"] = entry is not None and _same_color(_entry(entry).get("color"), color)
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
    out = {"action": "update", "tag": final, "previous_tag": tag, "accepted": True, "renamed": renamed,
           "recolored": color is not None}
    after, read_error = _read_back_library(indigo_module)
    if after is None:
        out.update({"entry": None, "verified": None, "read_back_error": read_error})
        return out
    entry = after.get(final)
    out["entry"] = _entry(entry) if entry is not None else None
    verified = entry is not None
    if verified and color is not None:
        verified = _same_color(_entry(entry).get("color"), color)
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
    scan = _scan_carriers(indigo_module, tag, OBJECT_TYPES)
    _require_readable_scan(scan, tag, OBJECT_TYPES)
    carriers = scan["rows"]
    count = len(carriers)
    count_text = _carrier_text(scan)
    out = {"action": "delete", "tag": tag, "carrier_count": count}
    out.update(_scan_report(scan, "carrier_count_complete"))
    if confirm and not scan["complete"]:
        # The one irreversible path: never delete on a count known to be short.
        raise ValueError(
            f"Nothing was deleted. {tag!r} is carried by {count_text}; deleting on an incomplete "
            "count is refused because it cannot be undone. Retry when every collection is readable."
        )
    if not confirm:
        out.update({
            "accepted": False,
            "requires_confirmation": True,
            "carriers": [{"type": r["type"], "id": r["id"], "name": r["name"]}
                         for r in carriers[:CARRIER_LIST_CAP]],
            "carriers_truncated": count > CARRIER_LIST_CAP,
            "message": (
                f"Nothing was deleted. Deleting {tag!r} removes it from the library and from "
                f"{count_text} carrying it, and cannot be undone. Call again with confirm=true to delete."
            ),
        })
        return out
    _dispatch(indigo_module.server.delete_tag, f"delete_tag({tag!r}) on a tag carried by {count} object(s)", tag)
    out["accepted"] = True
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
    """Validate the whole ``objects`` list before anything is touched.

    Returns ``(entries, duplicates_ignored)``: the distinct ``(type, id)``
    pairs in first-seen order. A repeated entry is dropped up front so a
    dry run and the real call report the same rows.
    """
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
    distinct = list(dict.fromkeys(parsed))
    return distinct, len(parsed) - len(distinct)


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


def _change(command, collection, *, obj_id, tag, adding):
    """Dispatch one add/remove and read the object back; the row fragment.

    Indigo objects are copies, so the read-back re-fetches the object
    rather than re-reading the one already in hand. Only a read-back
    showing the change yields ``added``/``removed``; anything else is an
    ``error`` whose message says whether the change may have happened.
    """
    verb = "add_tag" if adding else "remove_tag"
    try:
        _dispatch(command, f"{verb}({obj_id}, {tag!r})", obj_id, tag)
    except (ValueError, RuntimeError) as exc:
        return {"outcome": "error", "message": str(exc)}
    try:
        has_after = tag in _object_tags(collection[obj_id])
    except Exception as exc:  # pylint: disable=broad-except
        return {"outcome": "error",
                "message": f"command accepted but read-back failed ({type(exc).__name__}: {exc}); "
                           "the change may have taken effect"}
    if has_after != adding:
        return {"outcome": "error", "message": "command accepted but read-back does not show the change"}
    return {"outcome": "added" if adding else "removed"}


def _tag_one(indigo_module, item, tag, *, adding, dry_run):
    """One object's row. Never raises an ``Exception``: one bad object
    must not abort the batch, whatever part of the work fails.

    ``dispatched`` says whether ``add_tag``/``remove_tag`` was sent, so a
    caller can tell "nothing touched" from "sent, state unknown". A
    dispatched row that is not ``error`` was confirmed by read-back.
    """
    type_name, obj_id = item
    out = {"type": type_name, "id": obj_id, "dispatched": False}
    try:
        found, fragment = _inspect(indigo_module, type_name, obj_id, tag)
        out.update(fragment)
        if found is None:
            return out
        collection, has_tag = found
        if has_tag == adding:
            out["outcome"] = "already" if adding else "absent"
        elif dry_run:
            out["outcome"] = "would_add" if adding else "would_remove"
        else:
            # Resolve the command first: a failure finding it sent nothing.
            command = getattr(getattr(indigo_module, OBJECT_TYPES[type_name][1]),
                              "add_tag" if adding else "remove_tag")
            out["dispatched"] = True
            out.update(_change(command, collection, obj_id=obj_id, tag=tag, adding=adding))
    except Exception as exc:  # pylint: disable=broad-except
        out.update({"outcome": "error", "message": f"failed: {type(exc).__name__}: {exc}; " + (
            "the change may have taken effect" if out["dispatched"] else "nothing was sent")})
    return out


def _needs_remove_confirmation(tag, action, entries, flags):
    """The requires_confirmation payload for a large remove, or None.

    Counts the distinct entries requested, not the objects that turn
    out to carry the tag: the gate runs before anything is read.
    """
    if action != "remove" or flags["dry_run"] or flags["confirm"] or len(entries) <= REMOVE_CONFIRM_THRESHOLD:
        return None
    return {
        "tag": tag, "action": action, "dry_run": False, "requires_confirmation": True,
        "verified": None,
        "message": (
            f"Nothing was changed. A remove of {tag!r} with {len(entries)} entries (more than "
            f"{REMOVE_CONFIRM_THRESHOLD}) needs confirm=true; dry_run=true previews it."
        ),
    }


def _batch_verified(results):
    """``verified`` for a batch: False if any row is ``error``; None if
    nothing was dispatched (a dry run, or every row not_found / already /
    absent); otherwise True, since a dispatched row is only non-error
    when its read-back confirmed it."""
    if any(row["outcome"] == "error" for row in results):
        return False
    if not any(row["dispatched"] for row in results):
        return None
    return True


def _tag_objects_handler(args, indigo_module):
    _require_tag_api(indigo_module)
    _reject_unknown_args(args, ("tag", "action", "objects", "dry_run", "confirm"))
    tag = _require_str(args.get("tag"), "tag")
    action = args.get("action")
    if action not in ("add", "remove"):
        raise ValueError(f"action must be 'add' or 'remove'; got {action!r}")
    entries, duplicates = _parse_objects(args.get("objects"))
    flags = {"dry_run": _optional_bool(args, "dry_run"), "confirm": _optional_bool(args, "confirm")}
    library = _read_library(indigo_module)
    if tag not in library:
        raise _unknown_tag(tag, library)
    counts = {"requested": len(args["objects"]), "duplicates_ignored": duplicates}
    pending = _needs_remove_confirmation(tag, action, entries, flags)
    if pending is not None:
        pending.update(counts)
        return pending
    results = [_tag_one(indigo_module, item, tag, adding=action == "add", dry_run=flags["dry_run"])
               for item in entries]
    summary = {"error": 0}
    for row in results:
        summary[row["outcome"]] = summary.get(row["outcome"], 0) + 1
    return {
        "tag": tag,
        "action": action,
        "dry_run": flags["dry_run"],
        "verified": _batch_verified(results),
        **counts,
        "summary": summary,
        "results": results,
    }


# -- list_tagged -------------------------------------------------------------------


def _pagination(args):
    """``(limit, offset)``; a bool is refused (it is not an integer here)."""
    raw = {"limit": args.get("limit", _DEFAULT_LIST_LIMIT), "offset": args.get("offset", 0)}
    for key, value in raw.items():
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer; got {value!r}")
    try:
        return _normalize_pagination(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit and offset must be integers ({exc})") from exc


def _list_tagged_handler(args, indigo_module):
    _require_tag_api(indigo_module)
    _reject_unknown_args(args, ("tag", "type", "limit", "offset"))
    tag = _require_str(args.get("tag"), "tag")
    type_name = args.get("type")
    if type_name is not None and type_name not in OBJECT_TYPES:
        raise ValueError(f"type must be one of {list(OBJECT_TYPES)}; got {type_name!r}")
    limit, offset = _pagination(args)
    library = _read_library(indigo_module)
    if tag not in library:
        # A mistyped tag is a failed call, not "nothing carries it".
        raise _unknown_tag(tag, library)
    types = [type_name] if type_name else list(OBJECT_TYPES)
    scan = _scan_carriers(indigo_module, tag, types)
    _require_readable_scan(scan, tag, types)
    rows = scan["rows"]
    total = len(rows)
    out = {
        "tag": tag,
        "results": rows[offset:offset + limit],
        "total_count": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }
    # Always present: an incomplete list must say so rather than pass as whole.
    out.update(_scan_report(scan, "total_count_complete"))
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
            "from all of them and cannot be undone); carrier_count_complete is false when "
            "some objects could not be read, and a confirmed delete is then refused. "
            "`accepted` means only that Indigo took the command; every change is read back "
            "from the library and `verified` says whether it shows (null if the read-back "
            "failed). Assign tags to objects with tag_objects."
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
            "outcome: added, already, removed, absent, not_found, or error (with a message), "
            "and `dispatched` says whether a command was sent for it (an error with "
            "dispatched: true may have taken effect). One failing object never stops the "
            "rest; repeated entries are dropped (duplicates_ignored). dry_run=true changes "
            "nothing and reports would_add / would_remove instead. A remove with more than "
            "25 entries needs confirm=true. Every change is read back from the object. "
            "`verified` is false if any row is error, null if nothing was sent, else true."
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
                "confirm": {"type": "boolean", "description": "needed for a remove with more than 25 entries"},
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
            "never an empty result. total_count_complete is false (with "
            "collections_unreadable / attr_read_errors) when some objects could not be read."
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
