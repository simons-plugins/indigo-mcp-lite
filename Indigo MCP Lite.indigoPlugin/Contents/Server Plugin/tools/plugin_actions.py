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
  ``Plugins/`` and ``Plugins (Disabled)/``. A ``<Action>`` element
  that parses to nothing usable (a genuine separator, but also any
  entry actually missing ``<Name>``/``<CallbackMethod>`` -- this
  parser cannot tell those apart) is counted into ``skipped_actions``
  / ``skipped_action_ids`` rather than silently dropped, and a
  bundle whose Actions.xml parses fine but declares zero callable
  actions says so via ``no_actions_reason`` rather than reading as
  the same bare empty result as a missing Actions.xml — same
  ``skipped_automations`` convention as ``automation_contents.py``.
  Both ``enabled`` and ``running`` are reported (they can differ — a
  crashed-but-enabled plugin is a real Indigo state, same as
  ``system.py``'s ``_serialize_plugin``), and an action with NO
  declared ``<ConfigUI>`` fields at all is flagged
  ``props_undeclared: true`` rather than reading as "takes no
  arguments" — very common for ``uiPath="hidden"`` actions, whose
  props are real but simply never enumerated in the XML.
- ``plugin_execute_action`` — the write. Props arrive as a plain
  JSON-safe dict and are converted to ``indigo.Dict`` before dispatch
  (the ConfigUI-values gotcha: a plain dict silently seeds nothing —
  see ``indigo-configui-values-must-be-indigo-dict``). An unknown prop
  key is rejected rather than sent WHEN the action actually declares
  fields to validate against, because a mistyped prop name is
  silently dropped during cross-plugin serialization and the action
  runs with missing data, no error raised — exactly the dishonest-
  looking-success this repo's degradation-path convention forbids.
  When the action declares NO fields at all, there is no allowlist to
  check against (an absent one, not an empty one) — refusing every
  call to such an action would make the whole hidden-action class
  uncallable, which is this issue's own origin case, so props pass
  through unchecked and the response says ``props_validated: false``
  rather than implying they were checked. ``device_id`` is validated
  for existence AND, when the action's ``deviceFilter`` is
  ``self``-scoped, that the device is actually owned by the target
  plugin (and matches the required ``deviceTypeId`` where the filter
  names one) — existence alone doesn't stop a device belonging to an
  unrelated plugin from being dispatched at the wrong target.

Deliberately NO allowlist/denylist of plugins or actions here —
object-level write denial is issue #69's job, not this one's.
"""

import os
import xml.etree.ElementTree as ET

from tools.lookup import _lookup_or_raise, _reject_unknown_args, _require_int_id
from tools.system import _lookup_plugin_or_raise, _require_plugin_id, _scan_plugin_bundles
from tools.zwave import _json_safe

_NON_PROP_FIELD_TYPES = ("label", "separator")

# <List class="..."> values that name one of Indigo's own built-in
# collections rather than a plugin callback. Unlike a plugin's own
# dynamic menu (class="self", or a bare <List method=...> with no
# class), these ARE resolvable by a caller — just via a different
# lite tool, not by reading the XML. Live census: indigo.devices 9,
# indigo.actionGroups 3, indigo.variables 1.
_RESOLVABLE_LIST_CLASSES = {
    "indigo.devices": "list_devices",
    "indigo.actionGroups": "list_action_groups",
    "indigo.variables": "list_variables",
}


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


def _classify_list(list_el):
    """Classify a ``<List>`` element found on a ``<Field>``.

    Returns ``("static", options)`` when it has ``<Option>`` children
    (real, enumerable values), or ``("dynamic", (source, tool))``
    otherwise — ANY ``<List>`` with no ``<Option>`` children is
    dynamic, whatever its ``class`` attribute, because its legal
    values come from a callback (or a live Indigo collection) rather
    than from the XML itself. The old rule only flagged
    ``class="self"`` as dynamic; everything else fell through to
    "looks like a plain textfield" — shape-identical to a real
    textfield, with no signal a model shouldn't invent a value for it.
    Live census of ``<List class=...>``: self 153, indigo.devices 9,
    indigo.actionGroups 3, indigo.variables 1, plugin 1 — plus at
    least one bare ``<List method=...>`` with no class at all (Sonos).
    ``source``/``tool``: for the three built-in Indigo collections
    (``_RESOLVABLE_LIST_CLASSES``) ``tool`` names the lite tool that
    actually lists the real values; for a plugin's own callback
    (``class="self"``, no class, or anything else) ``tool`` is None
    and ``source`` is the best identifier available (method name,
    falling back to the class attribute, falling back to
    ``"unknown"``) — unresolvable cross-plugin either way.
    """
    options = list_el.findall("Option")
    if options:
        return "static", [
            {"value": o.get("value"), "label": (o.text or "").strip()}
            for o in options
        ]
    cls = list_el.get("class")
    method = list_el.get("method")
    tool = _RESOLVABLE_LIST_CLASSES.get(cls)
    if tool:
        source = cls
    elif method:
        source = method
    else:
        source = cls or "unknown"
    return "dynamic", (source, tool)


def _parse_action_props(config_ui_el):
    """Split an ``<Action>``'s ``<ConfigUI><Field>`` children into
    (props, notes, has_dynamic_fields).

    ``label``/``separator`` fields are not props — a label field's
    text is genuinely useful calling guidance ("Duration in minutes
    (1-180). Default: 15") so it goes into ``notes`` instead. A field
    whose ``<List>`` has no ``<Option>`` children is a dynamic menu
    (see ``_classify_list``) and is flagged ``values: "dynamic"``
    rather than ever presented as free text a model could invent a
    value for; a field with a static ``<List><Option>`` set gets its
    real values enumerated.
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
            kind, classified = _classify_list(list_el)
            if kind == "static":
                if classified:
                    prop["values"] = classified
            else:
                source, tool = classified
                prop["values"] = "dynamic"
                prop["values_source"] = source
                if tool:
                    prop["values_source_tool"] = tool
                has_dynamic = True
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

    action = {
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
    if not props:
        # An absent/empty <ConfigUI> (or one with only label/separator
        # fields) is NOT "this action takes no arguments" — Indigo
        # doesn't require props to be declared there at all, and
        # uiPath="hidden" (programmatic-only) actions conventionally
        # declare nothing while still reading real props in their
        # callback. Live census: 61 hidden actions, 51 with zero
        # ConfigUI fields (MQTT Connector's fetchQueuedMessage reads
        # props.get("message_type") with a bare <ConfigUI/>).
        action["props_undeclared"] = True
        action["props_undeclared_reason"] = (
            "declares no ConfigUI fields; props may still be accepted "
            '(common for uiPath="hidden" actions)'
        )
    return action


def _parse_actions_xml(path):
    """Parse an Actions.xml file into ``(actions, skipped_count,
    skipped_ids)``.

    Raises ValueError (naming the path and the underlying error) on
    any read/parse failure — an unusable precondition is a FAILED
    call, never an empty list. An ``<Action>`` element
    ``_parse_action_element`` excludes (a genuine separator like
    ``<Action id="sep1"/>``, but also any entry that's just plain
    missing ``<Name>``/``<CallbackMethod>`` -- this parser cannot
    tell those two cases apart) is counted rather than silently
    dropped, the same ``skipped_automations`` convention
    ``automation_contents.py`` uses: a caller must be able to tell
    "nothing was skipped" from "something was and I can't see it"."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"Actions.xml at {path!r} could not be parsed: {exc}") from exc

    actions = []
    skipped_count = 0
    skipped_ids = []
    for action_el in tree.getroot().findall("Action"):
        parsed = _parse_action_element(action_el)
        if parsed is None:
            skipped_count += 1
            action_id = action_el.get("id")
            if action_id:
                skipped_ids.append(action_id)
            continue
        actions.append(parsed)
    return actions, skipped_count, skipped_ids


def _load_actions_for_dispatch(bundle_path, plugin_id, action_id):
    """Parse Actions.xml as the safety net before dispatch.

    An unknown action id produces a quiet no-op from
    ``executeAction``, so this parse is the only thing standing
    between a caller's typo and silence. Missing or malformed XML
    means the call cannot be validated at all — raise rather than
    dispatch unvalidated. Returns ``(actions, skipped_ids, path)`` —
    ``skipped_ids`` is forwarded (not just counted) so a caller naming
    an id that IS in the XML but couldn't be parsed as callable gets
    told that specifically, rather than being told no such action
    exists at all."""
    path = _actions_xml_path(bundle_path)
    if not os.path.isfile(path):
        raise ValueError(
            f"plugin {plugin_id!r} has no Actions.xml at {path!r}; the call "
            f"cannot be validated, so action {action_id!r} was NOT dispatched."
        )
    try:
        actions, _skipped_count, skipped_ids = _parse_actions_xml(path)
    except ValueError as exc:
        raise ValueError(
            f"{exc}; the call cannot be validated, so action {action_id!r} "
            "was NOT dispatched."
        ) from exc
    return actions, skipped_ids, path


def _list_plugin_actions_handler(args, indigo_module):
    """Discover one plugin's declared Actions.xml actions.

    Envelope always carries ``enabled`` and ``running`` so the caller
    knows up front whether a subsequent ``plugin_execute_action`` call
    will fail — they can differ (a crashed-but-enabled plugin is a
    real Indigo state). A bundle with no Actions.xml at all is a
    legitimate empty result (``no_actions_reason``); a bundle whose
    Actions.xml exists but can't be parsed is a failed call, not an
    empty one.
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
    running = bool(plugin.isRunning())
    actions_xml_path = _actions_xml_path(bundle_path)
    if not os.path.isfile(actions_xml_path):
        return {
            "results": [],
            "total_count": 0,
            "plugin_id": plugin_id,
            "enabled": enabled,
            "running": running,
            "actions_xml_path": actions_xml_path,
            "no_actions_reason": "plugin bundle declares no Actions.xml",
        }

    actions, skipped_count, skipped_ids = _parse_actions_xml(actions_xml_path)
    result = {
        "results": actions,
        "total_count": len(actions),
        "plugin_id": plugin_id,
        "enabled": enabled,
        "running": running,
        "actions_xml_path": actions_xml_path,
    }
    if not actions:
        # Parsed fine but declares zero callable actions (a real but
        # empty <Actions/>, or every entry got skipped) -- a distinct
        # fact from "no Actions.xml at all", so it gets a distinct
        # reason rather than reading as the same bare empty success.
        result["no_actions_reason"] = "Actions.xml declares no callable actions"
    if skipped_count:
        result["skipped_actions"] = skipped_count
        if skipped_ids:
            result["skipped_action_ids"] = skipped_ids
    return result


def _validate_prop_value(key, value):
    if isinstance(value, bool):
        return
    if isinstance(value, (str, int, float)):
        return
    raise ValueError(
        f"prop {key!r} must be a str/int/float/bool, got {type(value).__name__}"
    )


def _describe_device_filter_requirement(device_filter):
    """Human-readable summary of what a ``self``-scoped deviceFilter
    actually requires, for the self-correcting mismatch error."""
    alternatives = [f.strip() for f in device_filter.split(",") if f.strip()]
    type_ids = [a[len("self."):] for a in alternatives if a.startswith("self.")]
    if type_ids:
        return f"deviceTypeId in {type_ids}"
    return "any deviceTypeId"


def _device_matches_self_filter(dev, plugin_id, device_filter):
    """Return True if ``dev`` satisfies a ``self``-scoped deviceFilter.

    Live census: every ``deviceFilter`` in the workspace is ``self``
    or ``self.<typeId>`` — always scoped to a device owned by the
    target plugin. Existence alone (``_lookup_or_raise``) doesn't
    prove that: Netro's ``startZoneWithDelay`` (filter
    ``self.sprinkler``) would otherwise happily accept a Sonos
    ZonePlayer id and dispatch to the wrong plugin's device.
    Comma-separated filter lists are ORed (accept if any alternative
    matches).
    """
    alternatives = [f.strip() for f in device_filter.split(",") if f.strip()]
    dev_plugin_id = getattr(dev, "pluginId", None)
    dev_type_id = getattr(dev, "deviceTypeId", None)
    for alt in alternatives:
        if alt == "self":
            if dev_plugin_id == plugin_id:
                return True
        elif alt.startswith("self."):
            if dev_plugin_id == plugin_id and dev_type_id == alt[len("self."):]:
                return True
    return False


def _is_self_scoped_filter(device_filter):
    alternatives = [f.strip() for f in device_filter.split(",") if f.strip()]
    return any(alt == "self" or alt.startswith("self.") for alt in alternatives)


def _plugin_execute_action_handler(args, indigo_module):
    """Dispatch one declared Actions.xml action on another plugin via
    ``executeAction``.

    Check order is pinned by tests: cheap argument validation, then
    plugin resolution, then ``isEnabled()``/``isRunning()`` (a stopped
    OR crashed plugin is a failed call, never a silent no-op — these
    are genuinely different states, see ``system.py``'s
    ``_serialize_plugin``), then Actions.xml parsing (the only guard
    against a quiet no-op on an unknown action id), then action-id /
    device / prop validation — all before ``executeAction`` is ever
    called.
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

    # 2. isEnabled / isRunning -- a stopped or crashed plugin is a
    #    failed call, not a no-op. These are separate checks with
    #    separate wording because they're separate Indigo states.
    if not plugin.isEnabled():
        raise ValueError(
            f"plugin {plugin_id!r} is not enabled; action {action_id!r} "
            "was NOT performed. Enable it in Indigo before executing "
            "actions on it."
        )
    if not plugin.isRunning():
        raise ValueError(
            f"plugin {plugin_id!r} is enabled but not running (crashed or "
            f"still starting); action {action_id!r} was NOT performed."
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
    actions, skipped_ids, _actions_xml_path_value = _load_actions_for_dispatch(
        bundle_path, plugin_id, action_id
    )

    # 4. unknown action id -- list the real ones so the model self-
    #    corrects. An id that IS in the XML but couldn't be parsed as
    #    callable (missing Name/CallbackMethod) gets told that
    #    specifically, never lumped in with "doesn't exist".
    by_id = {a["id"]: a for a in actions}
    action = by_id.get(action_id)
    if action is None:
        if action_id in skipped_ids:
            raise ValueError(
                f"plugin {plugin_id!r} declares action {action_id!r} in its "
                "Actions.xml, but it is missing <Name>/<CallbackMethod> and "
                "could not be validated as callable; it was NOT dispatched."
            )
        note = (
            f" ({len(skipped_ids)} further declared action id(s) could not "
            "be parsed as callable and are not listed)"
            if skipped_ids else ""
        )
        raise ValueError(
            f"plugin {plugin_id!r} has no action {action_id!r}; known "
            f"action ids: {sorted(by_id)}{note}"
        )

    # 5. deviceFilter / device_id: existence, then ownership.
    if action["device_required"] and device_id is None:
        raise ValueError(
            f"action {action_id!r} requires a device (deviceFilter="
            f"{action['device_filter']!r}); pass device_id."
        )
    if device_id is not None:
        dev = _lookup_or_raise(indigo_module.devices, device_id, "device")
        device_filter = action["device_filter"]
        if device_filter and _is_self_scoped_filter(device_filter):
            if not _device_matches_self_filter(dev, plugin_id, device_filter):
                raise ValueError(
                    f"device {device_id} does not match action {action_id!r}'s "
                    f"deviceFilter {device_filter!r}: it belongs to plugin "
                    f"{getattr(dev, 'pluginId', None)!r} (deviceTypeId "
                    f"{getattr(dev, 'deviceTypeId', None)!r}), but this action "
                    f"requires a device owned by plugin {plugin_id!r} with "
                    f"{_describe_device_filter_requirement(device_filter)}."
                )

    # 6/7. unknown prop keys / declared-field bookkeeping. An empty
    # declared_fields set is an ABSENT allowlist, not an empty one --
    # see the module docstring -- so props pass through unvalidated
    # rather than refusing the only correct call for the whole
    # hidden-action class.
    declared_fields = {p["id"] for p in action["props"]}
    if declared_fields:
        unknown_props = set(raw_props) - declared_fields
        if unknown_props:
            raise ValueError(
                f"unknown prop(s) {sorted(unknown_props)} for action "
                f"{action_id!r}; declared fields: {sorted(declared_fields)}"
            )
        props_not_supplied = sorted(declared_fields - set(raw_props))
        props_validated = True
    else:
        props_not_supplied = []
        props_validated = False

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
        "props_validated": props_validated,
    }
    if not props_validated:
        payload["props_validated_reason"] = (
            "action declares no ConfigUI fields, so props could not be "
            "checked against a known set and were passed through unchecked"
        )
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
            "default). An action with NO ConfigUI fields at all is flagged "
            "props_undeclared:true rather than reading as taking no "
            "arguments -- common for uiPath=\"hidden\" actions, which may "
            "still accept props. A menu field with no enumerable options "
            "is flagged values:\"dynamic\": for indigo.devices / "
            "indigo.actionGroups / indigo.variables menus, "
            "values_source_tool names the lite tool that lists the real "
            "values (list_devices / list_action_groups / list_variables); "
            "otherwise values_source is the plugin's own callback, which "
            "can't be invoked cross-plugin. A static menu lists its real "
            "values. Both enabled and running are reported -- they can "
            "differ, and a crashed-but-enabled plugin still fails "
            "plugin_execute_action. Use plugin_execute_action to actually "
            "invoke one."
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
            "declared props for this plugin; an unknown action id is "
            "refused before dispatch. If the action declares ConfigUI "
            "fields, an unrecognized prop key is refused too; if it "
            "declares none at all (props_undeclared on "
            "list_plugin_actions -- common for uiPath=\"hidden\" actions) "
            "there is no allowlist to check against, so props pass "
            "through unchecked and the response says "
            "props_validated:false. device_id is checked for existence "
            "AND, when the action's deviceFilter is self-scoped, that the "
            "device actually belongs to this plugin (and matches the "
            "required deviceTypeId). props is a plain object, converted "
            "to indigo.Dict internally. A fire-and-forget action (Indigo "
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
