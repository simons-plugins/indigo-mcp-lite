"""Generic cross-plugin action tools (issue #71).

Indigo hard-blocks writing another plugin's ``pluginProps`` from
anywhere but the owning plugin ("pluginProps can only be replaced
from plugins that own those props"). The two sanctioned cross-plugin
write surfaces are the owning plugin's own UI and its declared
``Actions.xml`` actions via ``plugin.executeAction`` — the same
mechanism ``tools/auto_lights.py`` already rides for one plugin. This
module generalizes that mechanism to *every* action-bearing plugin:

- ``list_plugin_actions`` — read-only discovery. Indigo's API has no
  way to enumerate a plugin's actions (``getPlugin()`` exposes only
  ``executeAction``/``isEnabled``/``isInstalled``/``isRunning``/
  ``restart``), so discovery means parsing the target bundle's own
  ``Contents/Server Plugin/Actions.xml`` with stdlib
  ``xml.etree.ElementTree``. The bundle path comes from
  ``tools.system._scan_plugin_bundles``, which already scans both
  ``Plugins/`` and ``Plugins (Disabled)/``.
- ``plugin_execute_action`` — the write. Props arrive as a plain
  JSON-safe dict and are converted to ``indigo.Dict`` before dispatch
  (the ConfigUI-values gotcha: a plain dict silently seeds nothing —
  see ``indigo-configui-values-must-be-indigo-dict``). An unknown prop
  key is rejected rather than sent, because a mistyped prop name is
  silently dropped during cross-plugin serialization and the action
  runs with missing data, no error raised — exactly the dishonest-
  looking-success this repo's degradation-path convention forbids.

Deliberately NO allowlist/denylist of plugins or actions here —
object-level write denial is issue #69's job, not this one's.
"""

import os
import xml.etree.ElementTree as ET

from tools.lookup import _lookup_or_raise, _reject_unknown_args, _require_int_id
from tools.system import _lookup_plugin_or_raise, _require_plugin_id, _scan_plugin_bundles
from tools.zwave import _json_safe

_NON_PROP_FIELD_TYPES = ("label", "separator")


def _actions_xml_path(bundle_path):
    return os.path.join(bundle_path, "Contents", "Server Plugin", "Actions.xml")


def _find_bundle_path(indigo_module, plugin_id):
    """Return the installed bundle path for ``plugin_id``, or None if
    the filesystem scan turns up nothing with that id."""
    for pid, bundle in _scan_plugin_bundles(indigo_module):
        if pid == plugin_id:
            return bundle
    return None


def _resolve_plugin(indigo_module, plugin_id):
    """Resolve ``plugin_id`` via ``getPlugin``, adding a pointer at
    ``list_plugins`` to the friendly error so a caller can self-correct
    without a second round trip."""
    try:
        return _lookup_plugin_or_raise(indigo_module, plugin_id)
    except ValueError as exc:
        raise ValueError(
            f"{exc}; use list_plugins to see installed plugin ids"
        ) from exc


def _parse_field(field_el):
    """Parse one ``<Field>`` into ``(field_id, field_type, label,
    default)``. ``label`` is the stripped text of a nested ``<Label>``
    element, or "" if absent."""
    field_id = field_el.get("id")
    field_type = field_el.get("type", "textfield")
    label_el = field_el.find("Label")
    label = (label_el.text or "").strip() if label_el is not None else ""
    default = field_el.get("defaultValue")
    return field_id, field_type, label, default


def _parse_action_props(config_ui_el):
    """Split an ``<Action>``'s ``<ConfigUI><Field>`` children into
    (props, notes, has_dynamic_fields).

    ``label``/``separator`` fields are not props — a label field's
    text is genuinely useful calling guidance ("Duration in minutes
    (1-180). Default: 15") so it goes into ``notes`` instead. A field
    whose ``<List>`` is ``class="self"`` names a plugin callback we
    cannot invoke cross-plugin — its legal values are unknowable here,
    so it's flagged ``values: "dynamic"`` rather than ever presented
    as free text a model could invent a value for. A field with a
    static ``<List><Option>`` set gets its real values enumerated.
    """
    props = []
    notes = []
    has_dynamic = False
    if config_ui_el is None:
        return props, notes, has_dynamic

    for field_el in config_ui_el.findall("Field"):
        field_id, field_type, label, default = _parse_field(field_el)

        if field_type in _NON_PROP_FIELD_TYPES:
            if label:
                notes.append(label)
            continue

        prop = {"id": field_id, "type": field_type, "label": label, "default": default}
        list_el = field_el.find("List")
        if list_el is not None:
            method = list_el.get("method")
            if list_el.get("class") == "self" and method:
                prop["values"] = "dynamic"
                prop["values_source"] = method
                has_dynamic = True
            else:
                options = list_el.findall("Option")
                if options:
                    prop["values"] = [
                        {"value": o.get("value"), "label": (o.text or "").strip()}
                        for o in options
                    ]
        props.append(prop)

    return props, notes, has_dynamic


def _parse_action_element(action_el):
    """Parse one ``<Action>`` element, or return None for a separator
    (``<Action id="sep1"/>`` — no ``<Name>``/``<CallbackMethod>``, a
    UI spacer rather than a callable action)."""
    name_el = action_el.find("Name")
    callback_el = action_el.find("CallbackMethod")
    if name_el is None or callback_el is None:
        return None

    ui_path = action_el.get("uiPath")
    props, notes, has_dynamic = _parse_action_props(action_el.find("ConfigUI"))

    return {
        "id": action_el.get("id"),
        "name": (name_el.text or "").strip(),
        "callback_method": (callback_el.text or "").strip(),
        "ui_path": ui_path,
        "hidden": ui_path == "hidden",
        "device_required": "deviceFilter" in action_el.attrib,
        "device_filter": action_el.get("deviceFilter"),
        "props": props,
        "notes": notes,
        "has_dynamic_fields": has_dynamic,
    }


def _parse_actions_xml(path):
    """Parse an Actions.xml file into a list of action dicts.

    Raises ValueError (naming the path and the underlying error) on
    any read/parse failure — an unusable precondition is a FAILED
    call, never an empty list. Separator actions are excluded."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"Actions.xml at {path!r} could not be parsed: {exc}") from exc

    actions = []
    for action_el in tree.getroot().findall("Action"):
        parsed = _parse_action_element(action_el)
        if parsed is not None:
            actions.append(parsed)
    return actions


def _load_actions_for_dispatch(bundle_path, plugin_id, action_id):
    """Parse Actions.xml as the safety net before dispatch.

    An unknown action id produces a quiet no-op from
    ``executeAction``, so this parse is the only thing standing
    between a caller's typo and silence. Missing or malformed XML
    means the call cannot be validated at all — raise rather than
    dispatch unvalidated."""
    path = _actions_xml_path(bundle_path)
    if not os.path.isfile(path):
        raise ValueError(
            f"plugin {plugin_id!r} has no Actions.xml at {path!r}; the call "
            f"cannot be validated, so action {action_id!r} was NOT dispatched."
        )
    try:
        return _parse_actions_xml(path), path
    except ValueError as exc:
        raise ValueError(
            f"{exc}; the call cannot be validated, so action {action_id!r} "
            "was NOT dispatched."
        ) from exc


def _list_plugin_actions_handler(args, indigo_module):
    """Discover one plugin's declared Actions.xml actions.

    Envelope always carries ``enabled`` so the caller knows up front
    that a subsequent ``plugin_execute_action`` call will fail. A
    bundle with no Actions.xml at all is a legitimate empty result
    (``no_actions_reason``); a bundle whose Actions.xml exists but
    can't be parsed is a failed call, not an empty one.
    """
    _reject_unknown_args(args, ("plugin_id",))
    plugin_id = _require_plugin_id(args)
    plugin = _resolve_plugin(indigo_module, plugin_id)

    bundle_path = _find_bundle_path(indigo_module, plugin_id)
    if bundle_path is None:
        raise ValueError(
            f"plugin {plugin_id!r} resolved via getPlugin but no installed "
            "bundle was found by the filesystem scan; cannot list its actions."
        )

    enabled = bool(plugin.isEnabled())
    actions_xml_path = _actions_xml_path(bundle_path)
    if not os.path.isfile(actions_xml_path):
        return {
            "results": [],
            "total_count": 0,
            "plugin_id": plugin_id,
            "enabled": enabled,
            "actions_xml_path": actions_xml_path,
            "no_actions_reason": "plugin bundle declares no Actions.xml",
        }

    actions = _parse_actions_xml(actions_xml_path)
    return {
        "results": actions,
        "total_count": len(actions),
        "plugin_id": plugin_id,
        "enabled": enabled,
        "actions_xml_path": actions_xml_path,
    }


def _validate_prop_value(key, value):
    if isinstance(value, bool):
        return
    if isinstance(value, (str, int, float)):
        return
    raise ValueError(
        f"prop {key!r} must be a str/int/float/bool, got {type(value).__name__}"
    )


def _plugin_execute_action_handler(args, indigo_module):
    """Dispatch one declared Actions.xml action on another plugin via
    ``executeAction``.

    Check order is pinned by tests: cheap argument validation, then
    plugin resolution, then ``isEnabled()`` (a stopped plugin is a
    failed call, never a silent no-op), then Actions.xml parsing (the
    only guard against a quiet no-op on an unknown action id), then
    action-id / device / prop validation — all before ``executeAction``
    is ever called.
    """
    _reject_unknown_args(
        args, ("plugin_id", "action_id", "props", "device_id", "wait_until_done")
    )
    plugin_id = _require_plugin_id(args)

    action_id = args.get("action_id")
    if not isinstance(action_id, str) or not action_id:
        raise ValueError("action_id must be a non-empty string")

    raw_props = args.get("props")
    if raw_props is None:
        raw_props = {}
    if not isinstance(raw_props, dict):
        raise ValueError(f"props must be an object, got {type(raw_props).__name__}")
    for key, value in raw_props.items():
        _validate_prop_value(key, value)

    device_id = None
    if args.get("device_id") is not None:
        device_id = _require_int_id(args, key="device_id")

    wait_until_done = args.get("wait_until_done", True)
    if not isinstance(wait_until_done, bool):
        raise ValueError("wait_until_done must be true or false")

    # 1. plugin resolves
    plugin = _resolve_plugin(indigo_module, plugin_id)

    # 2. isEnabled -- a stopped plugin is a failed call, not a no-op.
    if not plugin.isEnabled():
        raise ValueError(
            f"plugin {plugin_id!r} is installed but not enabled/running; "
            f"action {action_id!r} was NOT performed. Enable it in Indigo "
            "before executing actions on it."
        )

    # 3. parse Actions.xml -- the only safety net against dispatching
    #    an unknown action id, which executeAction silently no-ops.
    bundle_path = _find_bundle_path(indigo_module, plugin_id)
    if bundle_path is None:
        raise ValueError(
            f"plugin {plugin_id!r} resolved via getPlugin but no installed "
            f"bundle was found; cannot validate action {action_id!r} and it "
            "was NOT dispatched."
        )
    actions, _actions_xml_path_value = _load_actions_for_dispatch(
        bundle_path, plugin_id, action_id
    )

    # 4. unknown action id -- list the real ones so the model self-corrects.
    by_id = {a["id"]: a for a in actions}
    action = by_id.get(action_id)
    if action is None:
        raise ValueError(
            f"plugin {plugin_id!r} has no action {action_id!r}; known "
            f"action ids: {sorted(by_id)}"
        )

    # 5. deviceFilter / device_id
    if action["device_required"] and device_id is None:
        raise ValueError(
            f"action {action_id!r} requires a device (deviceFilter="
            f"{action['device_filter']!r}); pass device_id."
        )
    if device_id is not None:
        _lookup_or_raise(indigo_module.devices, device_id, "device")

    # 6/7. unknown prop keys / declared-field bookkeeping
    declared_fields = {p["id"] for p in action["props"]}
    unknown_props = set(raw_props) - declared_fields
    if unknown_props:
        raise ValueError(
            f"unknown prop(s) {sorted(unknown_props)} for action "
            f"{action_id!r}; declared fields: {sorted(declared_fields)}"
        )
    props_not_supplied = sorted(declared_fields - set(raw_props))

    indigo_props = indigo_module.Dict(raw_props)
    kwargs = {"props": indigo_props, "waitUntilDone": wait_until_done}
    if device_id is not None:
        kwargs["deviceId"] = device_id

    try:
        result = plugin.executeAction(action_id, **kwargs)
    except Exception as exc:
        raise ValueError(
            f"plugin {plugin_id!r} action {action_id!r} failed: {exc}"
        ) from exc

    payload = {
        "plugin_id": plugin_id,
        "action_id": action_id,
        "device_id": device_id,
        "props_not_supplied": props_not_supplied,
    }
    if result is None:
        payload["result"] = "dispatched"
    else:
        payload["result"] = "returned"
        payload["value"] = _json_safe(result)
    return payload


def register(handler, *, indigo_module, **_):
    """Register the plugin-actions tools onto the given MCPHandler."""
    handler.register_tool(
        name="list_plugin_actions",
        description=(
            "List one plugin's declared Actions.xml actions: id, name, "
            "callback method, whether it's hidden (uiPath=\"hidden\" -- "
            "programmatic-only, not meant for menus), whether it requires "
            "a device, and its ConfigUI fields as props (id/type/label/"
            "default; a dynamic menu is flagged values:\"dynamic\" with "
            "its source callback since its legal values can't be read "
            "cross-plugin; a static menu lists its real values). Use "
            "plugin_execute_action to actually invoke one."
        ),
        input_schema={
            "type": "object",
            "required": ["plugin_id"],
            "properties": {"plugin_id": {"type": "string"}},
        },
        handler=lambda **args: _list_plugin_actions_handler(args, indigo_module),
    )
    handler.register_tool(
        name="plugin_execute_action",
        description=(
            "Execute one declared Actions.xml action on another plugin "
            "via indigo.server.getPlugin(plugin_id).executeAction(...) -- "
            "the sanctioned cross-plugin write surface (Indigo blocks "
            "writing another plugin's pluginProps directly). Use "
            "list_plugin_actions first to see the real action ids and "
            "declared props for this plugin; an unknown action id or an "
            "unrecognized prop key is refused before dispatch rather than "
            "silently dropped. props is a plain object, converted to "
            "indigo.Dict internally. A fire-and-forget action (Indigo "
            "returns None) reports result:\"dispatched\", not a confirmed "
            "effect."
        ),
        input_schema={
            "type": "object",
            "required": ["plugin_id", "action_id"],
            "properties": {
                "plugin_id": {"type": "string"},
                "action_id": {"type": "string"},
                "props": {"type": "object"},
                "device_id": {"type": "integer"},
                "wait_until_done": {"type": "boolean"},
            },
        },
        handler=lambda **args: _plugin_execute_action_handler(args, indigo_module),
    )
