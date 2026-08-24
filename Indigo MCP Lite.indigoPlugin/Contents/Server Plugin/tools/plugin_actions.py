"""Generic cross-plugin action tools (issue #71).

Indigo hard-blocks writing another plugin's ``pluginProps`` from
anywhere but the owning plugin: "pluginProps can only be replaced
from plugins that own those props (not scripts or the interactive
shell)" (hit live 2026-08-24 scripting z2m device CT ranges -- see
issue #71's origin note). The two sanctioned cross-plugin write
surfaces are the owning plugin's own UI and its declared
``Actions.xml`` actions via ``plugin.executeAction`` -- the same
mechanism ``tools/auto_lights.py`` already rides for one plugin. This
module generalizes that mechanism to *every* action-bearing plugin:

- ``list_plugin_actions`` -- read-only discovery. ``getPlugin()``
  returns a live plugin object exposing ``executeAction``/
  ``isInstalled``/``isEnabled``/``isRunning``/``restart``/
  ``restartAndDebug``/``pluginFolderPath`` (the bundle's install
  path -- correct even for a plugin sitting in
  ``Plugins (Disabled)/``, confirmed live jarvis 2026-08-24) but
  nothing that enumerates its declared actions, so discovery means
  parsing the bundle's own ``Contents/Server Plugin/Actions.xml``
  with stdlib ``xml.etree.ElementTree``. An ``<Action>`` element that
  parses to nothing addressable -- a genuine separator, an entry
  missing ``<Name>``/``<CallbackMethod>``, or one that parsed fine
  but carries no ``id`` at all (this parser cannot always tell those
  apart, and an id-less action can't be looked up by id regardless)
  -- is counted into ``skipped_actions`` / ``skipped_action_ids``
  rather than silently dropped, and a bundle whose Actions.xml parses
  fine but declares zero callable actions says so via
  ``no_actions_reason`` rather than reading as the same bare empty
  result as a missing Actions.xml -- same ``skipped_automations``
  convention as ``automation_contents.py``. Both ``enabled`` and
  ``running`` are reported (they can differ -- a crashed-but-enabled
  plugin is a real Indigo state, same as ``system.py``'s
  ``_serialize_plugin``), and an action with NO declared
  ``<ConfigUI>`` fields at all is flagged ``props_undeclared: true``
  rather than reading as "takes no arguments" -- very common for
  ``uiPath="hidden"`` actions, whose props are real but simply never
  enumerated in the XML (a ``<ConfigUI>`` built entirely from a
  ``<Template file="...">`` include gets its own distinct reason,
  since that's a different fact -- fields genuinely exist, they just
  aren't readable without resolving the include).

- ``plugin_execute_action`` -- the write. Props arrive as a plain
  JSON-safe dict and are converted to ``indigo.Dict`` before dispatch
  (the ConfigUI-values gotcha: a plain dict silently seeds nothing --
  see ``indigo-configui-values-must-be-indigo-dict``).

  **What our pre-dispatch checks actually are, honestly**: Indigo
  itself already guards both halves of a bad call -- ``executeAction``
  on an unknown action id raises ``InvalidAction`` ("plugin does not
  have a <CallbackMethod> specified for action id ...") and on a
  disabled/stopped plugin raises ``PluginDisabled`` ("plugin X is not
  enabled") -- confirmed live, jarvis, 2026-08-24. So the
  action-id/enabled/running pre-checks in this module are NOT a
  safety net against silence; they are a BETTER-ERROR layer -- they
  name the real action ids and the real plugin state up front instead
  of leaving Indigo's own exception text as the only signal.
  ``InvalidAction`` is itself CONDITIONAL, though (also confirmed
  live): it only raises when ``waitUntilDone=True``. With
  ``waitUntilDone=False`` a bad action id returns None with NO
  exception at all. So whenever this module dispatches without having
  confirmed the action id itself (the degraded path below), it forces
  ``waitUntilDone=True`` regardless of what the caller asked for and
  discloses the override (``wait_until_done_overridden``) rather than
  silently changing the caller's requested semantics. The one
  genuinely silent failure Indigo does NOT guard at all is a mistyped
  prop name: it is dropped without complaint during cross-plugin
  serialization and the action runs with missing data
  (``docs/plugin-dev/concepts/actions.md``, a field note explicitly
  "not covered by the canonical reference"; confirmed live against
  lite's own hidden ``mcp`` action -- with a genuinely undeclared
  ConfigUI, real props DO arrive at the callback intact, so the
  permissive path below is sound, not merely hoped-for).

  An unknown prop key is rejected rather than sent WHEN the action
  actually declares fields to validate against, for exactly that
  reason. When the action declares NO fields at all (an absent
  allowlist, not an empty one -- refusing every call to such an
  action would make the whole hidden-action class uncallable, this
  issue's own origin case), props pass through unchecked and the
  response says ``props_validated: false``, with a warning that an
  unrecognised prop name would be silently dropped by Indigo and the
  call would still report success. When Actions.xml itself can't be
  read or parsed at all, the same honesty applies one level up -- but
  narrowly: since neither the action's declared fields NOR its
  ``deviceFilter`` are then knowable at all, this module only
  attempts the dispatch when the caller supplied no props and no
  ``device_id`` -- the shape where Indigo's own ``InvalidAction`` can
  at least backstop a bad action id (once ``waitUntilDone`` is forced
  true). That backstop is narrower than it sounds, though: it catches
  a wrong action id, NOT a valid action invoked without a device it
  actually requires -- ``executeAction`` defaults ``deviceId`` to 0
  regardless, and the target plugin's own callback can then fail
  silently in ITS event log, invisible to this module, so a
  ``completed_unverified`` result on this path is not a guarantee the
  action actually ran. Otherwise (props or device_id supplied) this
  module refuses outright, naming what could not be checked. This
  preserves the origin use case exactly -- that case always has a
  readable Actions.xml with the action legitimately declaring zero
  fields, the different (and confirmed-safe) path described above.

  ``device_id`` is validated for existence AND, against any
  ``self``/``self.<typeId>`` alternative in the action's
  ``deviceFilter`` (comma-separated alternatives are ORed -- a match
  on ANY self-scoped alternative confirms ownership, even inside a
  filter that also mixes in a non-``self`` alternative this module
  can't check), that the device is actually owned by the target
  plugin (and matches one of the required ``deviceTypeId``s, unless a
  bare ``self`` alternative is present, which accepts any type). Only
  when EVERY alternative is self-scoped and NONE match is this a hard
  rejection -- a mixed filter that doesn't match its self-scoped
  alternative(s) is left unvalidated instead, rather than falsely
  rejected: it may still match the non-``self`` alternative this
  module can't check. Existence alone doesn't stop a
  device belonging to an unrelated plugin from being dispatched at
  the wrong target -- and unlike a bad action id, Indigo has NO
  backstop for a wrong device, so this check is a real guard, not
  merely a better error. ``device_validated`` in the payload says
  whether ownership was actually confirmed. A static
  ``<List><Option>`` enum's values are enforced the same way its keys
  are; a dynamic menu's values are correctly unknowable and left
  unenforced.

  A call that reaches ``executeAction`` and then fails (the dispatch
  call itself raising, or JSON-serialising its return value failing)
  is reported as WAS DISPATCHED and may have partially completed,
  NOT as "NOT dispatched" -- and is raised as something other than
  ValueError/TypeError so ``mcp_handler``'s back-off bucket applies
  rather than its self-correct-and-retry one; a non-idempotent action
  (run a sprinkler zone, send a push) must not be blindly retried.

Deliberately NO allowlist/denylist of plugins or actions here --
object-level write denial is issue #69's job, not this one's.
"""

import logging
import os
import xml.etree.ElementTree as ET

from tools.lookup import _lookup_or_raise, _reject_unknown_args, _require_int_id
from tools.system import _require_plugin_id
from tools.zwave import _json_safe

# Default logger when register() isn't threaded one (e.g. a direct call
# in tests) or is explicitly given None -- "Plugin" matches the name
# Indigo itself logs under and the fallback automation_contents.py /
# mcp_handler.py already use, so a message actually lands in Indigo's
# Event Log rather than a logger nobody reads.
_LOGGER = logging.getLogger("Plugin")

# type="button"/"label"/"separator" fields are UI chrome, not props.
_NON_PROP_FIELD_TYPES = ("label", "separator", "button")

# <List class="..."> values that name one of Indigo's own built-in
# collections rather than a plugin callback. Unlike a plugin's own
# dynamic menu (class="self", or a bare <List method=...> with no
# class), these ARE resolvable by a caller -- just via a different
# lite tool, not by reading the XML. Live census: indigo.devices 9,
# indigo.actionGroups 3, indigo.variables 1.
_RESOLVABLE_LIST_CLASSES = {
    "indigo.devices": "list_devices",
    "indigo.actionGroups": "list_action_groups",
    "indigo.variables": "list_variables",
}


class _ActionsXmlError(ValueError):
    """Raised ONLY by ``_parse_actions_xml`` on a read/parse failure.

    A dedicated type (still a ``ValueError`` subclass, so nothing
    outside this module changes behaviour) rather than bare
    ``ValueError`` so ``_try_validate_action``'s parse-failure ->
    attempt-dispatch-anyway conversion can catch EXACTLY this failure
    mode. Catching bare ``ValueError`` there would let any future
    ``ValueError`` raised inside ``_parse_actions_xml`` (or anything
    it calls) silently acquire "proceed with the write" semantics.
    """


class _ActionsXmlIOError(RuntimeError):
    """Raised ONLY by ``_actions_xml_exists`` when the existence check
    itself fails for a reason other than "genuinely absent" -- a
    stale NFS handle, permission denial. Deliberately NOT a
    ``ValueError``: ``mcp_handler`` routes ``ValueError``/``TypeError``
    to its self-correct-and-retry bucket, but no argument change fixes
    a stale filesystem handle, so this must reach the back-off bucket
    instead -- the same reasoning this module already applies to a
    post-dispatch failure.
    """


def _actions_xml_path(bundle_path):
    return os.path.join(bundle_path, "Contents", "Server Plugin", "Actions.xml")


def _resolve_bundle_path(plugin):
    """Return the plugin's bundle path, or None if it's missing or
    not an absolute path.

    ``getattr(plugin, "pluginFolderPath", "")`` defaulting to ``""``
    would otherwise silently ``os.path.join`` into a RELATIVE path
    (``"Contents/Server Plugin/Actions.xml"``), resolved against the
    plugin host process's current working directory -- in principle
    validating one plugin's action ids against a completely different
    bundle's Actions.xml if the cwd happened to contain one. Must be
    a named degradation, never a silent join.
    """
    path = getattr(plugin, "pluginFolderPath", "") or ""
    if not path or not os.path.isabs(path):
        return None
    return path


def _actions_xml_exists(path):
    """Check for Actions.xml at ``path`` without swallowing a real
    I/O fault as "absent".

    ``os.path.isfile`` returns False for BOTH "genuinely doesn't
    exist" and "couldn't even check" (permission denial, a stale
    network handle going away mid-session -- this workspace runs
    bundles off ``/Volumes/...``, where that happens), which is the
    exact same lie PR #73 review round 3 deleted from the old
    filesystem-scan bundle lookup, relocated here. Only
    ``FileNotFoundError``/``NotADirectoryError`` mean "absent" -- that
    covers a genuinely missing file AND an unmounted volume itself
    (``os.stat`` on an unmounted ``/Volumes/...`` mount point returns
    ENOENT, so it reads as "absent" too, not a fault); any other
    ``OSError`` (permission denial, a stale handle) is a real I/O
    fault, raised as ``_ActionsXmlIOError`` (never a plain
    ``ValueError`` -- no argument change fixes a stale filesystem
    handle, so this must reach ``mcp_handler``'s back-off bucket, not
    its self-correct-and-retry one) naming the path and errno, rather
    than read as a (false) positive claim that the plugin has no
    Actions.xml.
    """
    try:
        os.stat(path)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise _ActionsXmlIOError(
            f"could not check for Actions.xml at {path!r}: {exc} "
            f"(errno {exc.errno})"
        ) from exc


def _resolve_plugin(indigo_module, plugin_id):
    """Resolve ``plugin_id`` via ``getPlugin`` and confirm it is
    actually installed.

    Confirmed live (jarvis, 2026-08-24): ``getPlugin`` never raises --
    even a bogus id (``com.totally.bogus.plugin.id``) returns a live
    plugin object with ``isInstalled()``/``isEnabled()``/
    ``isRunning()`` all False and ``pluginFolderPath`` == "".
    Treating a lookup exception as the not-installed signal (the
    SDK docs' own framing, and this module's original design) is dead
    code that never fires in production -- a typo'd id would instead
    fall through to the enabled/running gate and produce a message
    that falsely asserts installation of something that never
    existed. ``isInstalled()`` is the only real signal.
    """
    plugin = indigo_module.server.getPlugin(plugin_id)
    if not plugin.isInstalled():
        raise ValueError(
            f"no plugin with id {plugin_id!r} (not installed); use "
            "list_plugins to see installed plugin ids"
        )
    return plugin


def _parse_field(field_el):
    """Parse one ``<Field>`` into ``(field_id, field_type, label,
    description, default, hidden)``.

    ``label``/``description`` are the stripped text of nested
    ``<Label>``/``<Description>`` elements, or "" if absent --
    ``<Description>`` (13 live fields) was dropped entirely before
    this fix, while ``<Label>`` text was deliberately kept as calling
    guidance; the same convention now applies to both, since
    Description often carries the more useful sentence. ``default``
    reads ``defaultValue`` first, falling back to a bare ``default``
    attribute (26 live fields use the latter and reported
    ``default: null`` before this fix). ``hidden`` is true only for
    the literal string ``"true"`` (e.g. a runtime-computed field like
    MyPeople's ``description``, ``hidden="true"``, 4 live + 8 in SDK
    examples).
    """
    field_id = field_el.get("id")
    field_type = field_el.get("type", "textfield")
    label_el = field_el.find("Label")
    label = (label_el.text or "").strip() if label_el is not None else ""
    desc_el = field_el.find("Description")
    description = (desc_el.text or "").strip() if desc_el is not None else ""
    if "defaultValue" in field_el.attrib:
        default = field_el.get("defaultValue")
    else:
        default = field_el.get("default")
    hidden = field_el.get("hidden") == "true"
    return field_id, field_type, label, description, default, hidden


def _classify_list(list_el):
    """Classify a ``<List>`` element found on a ``<Field>``.

    Returns ``("static", options)`` when it has ``<Option>`` children
    (real, enumerable values), or ``("dynamic", (source, tool))``
    otherwise -- ANY ``<List>`` with no ``<Option>`` children is
    dynamic, whatever its ``class`` attribute, because its legal
    values come from a callback (or a live Indigo collection) rather
    than from the XML itself. Live census of ``<List class=...>``:
    self 153, indigo.devices 9, indigo.actionGroups 3,
    indigo.variables 1, plugin 1 -- plus at least one bare
    ``<List method=...>`` with no class at all (Sonos). ``source``/
    ``tool``: for the three built-in Indigo collections
    (``_RESOLVABLE_LIST_CLASSES``) ``tool`` names the lite tool that
    actually lists the real values; for a plugin's own callback
    (``class="self"``, no class, or anything else) ``tool`` is None
    and ``source`` is the best identifier available (method name,
    falling back to the class attribute, falling back to
    ``"unknown"``) -- unresolvable cross-plugin either way.
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
    (props, notes, has_dynamic_fields, skipped_fields, skipped_options).

    ``label``/``separator``/``button`` fields are not props -- a
    label field's ``<Label>`` text is genuinely useful calling
    guidance ("Duration in minutes (1-180). Default: 15") so it goes
    into ``notes`` instead, and so does any field's ``<Description>``
    (real prop fields included -- it often carries the more useful
    sentence). A field whose ``<List>`` has no ``<Option>`` children
    is a dynamic menu (see ``_classify_list``) and is flagged
    ``values: "dynamic"`` rather than ever presented as free text a
    model could invent a value for; a field with a static
    ``<List><Option>`` set gets its real values enumerated. A field
    with ``hidden="true"`` is runtime-computed by the plugin -- kept
    IN ``props`` (flagged ``hidden: true``), not excluded, but a
    caller should never be expected to supply it (see
    ``props_not_supplied`` in the handler). A field with no ``id`` at
    all can't be referenced by a caller, so it's dropped rather than
    polluting ``declared_fields`` with an unusable key -- but counted
    into ``skipped_fields`` (unlike an id-less ``<Action>``, which has
    its own ``skipped_actions`` counter, an id-less ``<Field>`` had no
    counter at all before this fix, making an action whose fields all
    lack ids indistinguishable from a genuinely bare ``<ConfigUI/>``).
    An ``<Option>`` with no ``value`` attribute is excluded from a
    static list's enumerated values entirely (the write handler's
    allowlist must never treat it as the literal string ``"None"``)
    but counted into ``skipped_options`` -- the same
    discovery-says-legal/dispatch-refuses gap ``skipped_fields`` fixes
    for fields, one level down, at the option level.
    """
    props = []
    notes = []
    has_dynamic = False
    skipped_fields = 0
    skipped_options = 0
    if config_ui_el is None:
        return props, notes, has_dynamic, skipped_fields, skipped_options

    for field_el in config_ui_el.findall("Field"):
        field_id, field_type, label, description, default, hidden = _parse_field(field_el)

        if field_type in _NON_PROP_FIELD_TYPES:
            if label:
                notes.append(label)
            if description:
                notes.append(description)
            continue

        if description:
            notes.append(description)

        if field_id is None:
            skipped_fields += 1
            continue

        prop = {"id": field_id, "type": field_type, "label": label, "default": default}
        if hidden:
            prop["hidden"] = True
        list_el = field_el.find("List")
        if list_el is not None:
            kind, classified = _classify_list(list_el)
            if kind == "static":
                if classified:
                    prop["values"] = classified
                    skipped_options += sum(
                        1 for opt in classified if opt["value"] is None
                    )
            else:
                source, tool = classified
                prop["values"] = "dynamic"
                prop["values_source"] = source
                if tool:
                    prop["values_source_tool"] = tool
                has_dynamic = True
        props.append(prop)

    return props, notes, has_dynamic, skipped_fields, skipped_options


def _parse_action_element(action_el):
    """Parse one ``<Action>`` element, or return None for a separator
    (``<Action id="sep1"/>`` -- no ``<Name>``/``<CallbackMethod>``, a
    UI spacer rather than a callable action)."""
    name_el = action_el.find("Name")
    callback_el = action_el.find("CallbackMethod")
    if name_el is None or callback_el is None:
        return None

    ui_path = action_el.get("uiPath")
    config_ui_el = action_el.find("ConfigUI")
    props, notes, has_dynamic, skipped_fields, skipped_options = _parse_action_props(
        config_ui_el
    )

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
    if skipped_fields:
        action["skipped_fields"] = skipped_fields
    if skipped_options:
        action["skipped_options"] = skipped_options
    # Computed UNCONDITIONALLY (not just when props is empty) -- a
    # ConfigUI can mix a <Template file="..."/> include with its own
    # readable <Field> elements (post-merge review round 2: the old
    # props-empty-only check meant that shape got no flag at all, so
    # verified could read true off a field set the include might still
    # extend, and a legitimate prop from the include was hard-refused
    # as "unknown"). Present on the action whenever a Template exists,
    # regardless of how many real fields sit alongside it.
    uses_template = (
        config_ui_el is not None and config_ui_el.find("Template") is not None
    )
    if uses_template:
        action["uses_template"] = True
    if not props:
        # An absent/empty <ConfigUI> (or one with only label/
        # separator/button/id-less fields) is NOT "this action takes
        # no arguments" -- Indigo doesn't require props to be
        # declared there at all, and uiPath="hidden" (programmatic-
        # only) actions conventionally declare nothing while still
        # reading real props in their callback. Live census: 61
        # hidden actions, 51 with zero ConfigUI fields (MQTT
        # Connector's fetchQueuedMessage reads
        # props.get("message_type") with a bare <ConfigUI/>).
        action["props_undeclared"] = True
        if uses_template:
            action["props_undeclared_reason"] = (
                "ConfigUI is a <Template> include -- its fields exist but "
                "aren't readable without resolving the include, so props "
                "may still be required"
            )
        elif skipped_fields:
            # Every <Field> present lacked an id -- "declares no
            # ConfigUI fields" would be FALSE here (fields genuinely
            # exist, in the same object as skipped_fields saying so);
            # a third, accurate variant, the same reasoning the
            # <Template> branch above already gets.
            action["props_undeclared_reason"] = (
                f"ConfigUI declares {skipped_fields} field(s), but none "
                "carry an id, so none can be referenced by a caller; "
                "props may still be required"
            )
        else:
            action["props_undeclared_reason"] = (
                "declares no ConfigUI fields; props may still be accepted "
                '(common for uiPath="hidden" actions)'
            )
    return action


def _parse_actions_xml(path):
    """Parse an Actions.xml file into ``(actions, skipped_count,
    skipped_ids)``.

    Raises ``_ActionsXmlError`` (naming the path and the underlying
    error) on any read/parse failure -- an unusable precondition is a
    FAILED call, not a result that merely looks empty. An ``<Action>``
    element that parses to nothing addressable -- a genuine separator
    like ``<Action id="sep1"/>``, an entry missing ``<Name>``/
    ``<CallbackMethod>`` (indistinguishable from a separator to
    ``_parse_action_element``), or one that parsed fine but carries no
    ``id`` attribute at all (can't be looked up by id, so treated the
    same way to keep ``by_id``/``sorted()`` safe downstream) -- is
    counted rather than silently dropped, the same
    ``skipped_automations`` convention ``automation_contents.py``
    uses: a caller must be able to tell "nothing was skipped" from
    "something was and I can't see it"."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise _ActionsXmlError(
            f"Actions.xml at {path!r} could not be parsed: {exc}"
        ) from exc

    actions = []
    skipped_count = 0
    skipped_ids = []
    for action_el in tree.getroot().findall("Action"):
        parsed = _parse_action_element(action_el)
        if parsed is None or parsed.get("id") is None:
            skipped_count += 1
            action_id = action_el.get("id")
            if action_id:
                skipped_ids.append(action_id)
            continue
        actions.append(parsed)
    return actions, skipped_count, skipped_ids


def _try_validate_action(plugin, plugin_id, action_id):
    """Attempt to resolve ``action_id`` against the plugin's declared
    Actions.xml.

    Returns ``(action, True, None)`` when Actions.xml is readable and
    the id resolves to a real action. Raises ValueError immediately
    when Actions.xml is readable and the id is KNOWN bad (genuinely
    absent, or present only as a skipped/unparseable entry) -- that
    is a known-bad dispatch, not one worth attempting.

    Degrades to ``(None, False, reason)`` -- rather than refusing the
    whole call -- when the bundle path is unusable or Actions.xml
    can't be found/parsed at all. Actions.xml was never Indigo's own
    guard against a bad action id (``executeAction`` itself is,
    raising ``InvalidAction`` when ``waitUntilDone=True`` --
    confirmed live jarvis 2026-08-24), so an unreadable Actions.xml is
    not a safety gap, only a worse error message if the id turns out
    to be wrong. A genuine I/O fault checking for the file (as opposed
    to it being absent) is NOT degraded this way -- see
    ``_actions_xml_exists`` -- since that signals something may be
    wrong beyond just "this one file is missing".
    """
    bundle_path = _resolve_bundle_path(plugin)
    if bundle_path is None:
        return None, False, (
            "plugin's pluginFolderPath "
            f"({getattr(plugin, 'pluginFolderPath', None)!r}) is missing "
            "or not an absolute path"
        )

    path = _actions_xml_path(bundle_path)
    try:
        exists = _actions_xml_exists(path)
    except _ActionsXmlIOError as exc:
        # A real I/O fault (not "genuinely absent") checking for
        # Actions.xml -- unlike every other refusal in this function,
        # this one is pre-dispatch but propagates as a hard failure
        # (see _actions_xml_exists), so it needs the same dispatch
        # guarantee every other pre-dispatch refusal in this module
        # carries.
        raise _ActionsXmlIOError(
            f"{exc}; action {action_id!r} was NOT dispatched."
        ) from exc
    if not exists:
        return None, False, f"no Actions.xml at {path!r}"

    try:
        actions, _skipped_count, skipped_ids = _parse_actions_xml(path)
    except _ActionsXmlError as exc:
        return None, False, str(exc)

    by_id = {a["id"]: a for a in actions}
    action = by_id.get(action_id)
    if action is not None:
        return action, True, None

    if action_id in skipped_ids:
        raise ValueError(
            f"plugin {plugin_id!r} declares action {action_id!r} in its "
            "Actions.xml, but it is missing <Name>/<CallbackMethod> (or "
            "an id) and could not be validated as callable; it was NOT "
            "dispatched."
        )
    note = (
        f" ({len(skipped_ids)} further declared action id(s) could not "
        "be parsed as callable and are not listed)"
        if skipped_ids else ""
    )
    raise ValueError(
        f"plugin {plugin_id!r} has no action {action_id!r}; known action "
        f"ids: {sorted(by_id)}{note}"
    )


def _list_plugin_actions_handler(args, indigo_module):
    """Discover one plugin's declared Actions.xml actions.

    Envelope always carries ``enabled`` and ``running`` so the caller
    knows up front whether a subsequent ``plugin_execute_action`` call
    will fail -- they can differ (a crashed-but-enabled plugin is a
    real Indigo state). A bundle with no Actions.xml at all is a
    legitimate empty result (``no_actions_reason``); a bundle whose
    Actions.xml exists but can't be parsed, or whose bundle path can't
    even be determined or checked, is a failed call, not an empty one
    -- this tool is read-only discovery with nothing to fall back to,
    so unlike ``plugin_execute_action`` it always refuses outright
    rather than degrading (see that handler's docstring).
    """
    _reject_unknown_args(args, ("plugin_id",))
    plugin_id = _require_plugin_id(args)
    plugin = _resolve_plugin(indigo_module, plugin_id)

    enabled = bool(plugin.isEnabled())
    running = bool(plugin.isRunning())

    bundle_path = _resolve_bundle_path(plugin)
    if bundle_path is None:
        raise ValueError(
            f"plugin {plugin_id!r} is installed but its pluginFolderPath "
            f"({getattr(plugin, 'pluginFolderPath', None)!r}) is missing "
            "or not an absolute path; cannot locate its Actions.xml."
        )

    actions_xml_path = _actions_xml_path(bundle_path)
    try:
        exists = _actions_xml_exists(actions_xml_path)
    except _ActionsXmlIOError as exc:
        # Every other refusal in this handler names plugin_id; the
        # bare _actions_xml_exists message only names the path (post-
        # merge review round 2, LOW).
        raise _ActionsXmlIOError(f"plugin {plugin_id!r}: {exc}") from exc
    if not exists:
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
    """Human-readable summary of what a deviceFilter actually
    requires, for the self-correcting mismatch error.

    Handles a bare ``"self"`` alternative correctly: it accepts ANY
    deviceTypeId, making a sibling ``self.<type>`` alternative
    redundant, so ``"self, self.foo"`` must describe itself as
    accepting any type -- not "typeId in ['foo']", which it used to.
    Also names any non-``self`` alternatives, so the description
    stays correct even if this is ever called for a filter that
    wasn't fully self-scoped (the current caller only calls it for
    one that is, but correctness here shouldn't depend on that).
    """
    alternatives = [f.strip() for f in device_filter.split(",") if f.strip()]
    bare_self = any(alt == "self" for alt in alternatives)
    type_ids = [a[len("self."):] for a in alternatives if a.startswith("self.")]
    non_self = [a for a in alternatives if a != "self" and not a.startswith("self.")]

    if bare_self:
        requirement = "any deviceTypeId owned by this plugin"
    elif type_ids:
        requirement = f"deviceTypeId in {type_ids}, owned by this plugin"
    else:
        requirement = "a device matching this plugin's deviceFilter"
    if non_self:
        requirement += f" (or one of these unvalidated alternatives: {non_self})"
    return requirement


def _device_matches_self_filter(dev, plugin_id, device_filter):
    """Return True if ``dev`` satisfies a ``self``-scoped deviceFilter.

    Live census: every ``deviceFilter`` in the workspace is ``self``
    or ``self.<typeId>`` -- always scoped to a device owned by the
    target plugin. Existence alone (``_lookup_or_raise``) doesn't
    prove that: Netro's ``startZoneWithDelay`` (filter
    ``self.sprinkler``) would otherwise happily accept a Sonos
    ZonePlayer id and dispatch to the wrong plugin's device.
    Comma-separated filter lists are ORed (accept if any alternative
    matches) -- called for ANY filter that contains at least one
    self-scoped alternative, not just a wholly self-scoped one (see
    the caller): a mixed filter's non-``self`` alternatives are simply
    never matched by the loop below, so trying the self-scoped ones
    first is always safe and can only turn a false "unvalidated" into
    a true "validated", never the reverse.
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
    """True only when EVERY comma-separated alternative is self-
    scoped.

    A mixed filter (e.g. ``"self.zone, indigo.relay"``) is left
    entirely unvalidated rather than partially checked -- with the
    old ``any()`` version, a real ``indigo.relay`` device (which
    legitimately matches the alternative this module can't validate)
    got hard-rejected by ``_device_matches_self_filter``, which only
    ever tests self-scoped alternatives, with an error whose every
    clause was false (it told the caller to find a device that
    doesn't exist -- no ``indigo.relay``-typed device is ever owned
    by this plugin, by definition).
    """
    alternatives = [f.strip() for f in device_filter.split(",") if f.strip()]
    return bool(alternatives) and all(
        alt == "self" or alt.startswith("self.") for alt in alternatives
    )


def _device_validation_gap_reason(device_filter):
    """Explain why device OWNERSHIP could not be validated, for the
    device_validated_reason payload field -- distinguishes "no
    deviceFilter attribute at all" from "deviceFilter="" (present but
    empty)" from "wholly non-self" from "mixed", so the message is
    accurate to which of those four is actually true rather than a
    single generic sentence for all of them. The first two are worth
    telling apart: ``device_required`` is true whenever the attribute
    is merely PRESENT (see ``_parse_action_element``), so
    ``deviceFilter=""`` is the one shape where "declares no
    deviceFilter" would contradict device_required having just forced
    the caller to supply a device_id at all."""
    if device_filter is None:
        return (
            "action declares no deviceFilter, so only the device's "
            "existence was checked, not ownership"
        )
    if device_filter == "":
        return (
            "action declares an empty deviceFilter (deviceFilter=\"\"), "
            "which requires a device_id but specifies no filter criteria "
            "to validate ownership against, so only the device's "
            "existence was checked, not ownership"
        )
    alternatives = [f.strip() for f in device_filter.split(",") if f.strip()]
    if any(alt == "self" or alt.startswith("self.") for alt in alternatives):
        return (
            "action's deviceFilter mixes in a non-self-scoped "
            "alternative this module cannot validate, so only the "
            "device's existence was checked, not ownership"
        )
    return (
        "action's deviceFilter is not self-scoped, so only the "
        "device's existence was checked, not ownership"
    )


def _plugin_execute_action_handler(args, indigo_module, logger=None):
    """Dispatch one declared Actions.xml action on another plugin via
    ``executeAction``.

    Check order, pinned by tests: cheap argument validation; plugin
    resolution (``isInstalled``, see ``_resolve_plugin``);
    ``isEnabled``/``isRunning`` (a stopped or crashed plugin is a
    failed call, never attempted -- these are genuinely different
    states, see ``system.py``'s ``_serialize_plugin``); best-effort
    Actions.xml validation (degrades to "attempt the dispatch anyway"
    ONLY when the caller gave no props and no device_id -- see the
    module docstring's narrowing); device requirement/ownership; prop
    key/value validation -- all before ``executeAction`` is ever
    called. A failure AFTER that point (the dispatch call itself, or
    serialising its return value) is reported as WAS DISPATCHED, NOT
    as not-performed -- see the module docstring's closing paragraph.
    ``logger`` (optional, threaded from ``register``) carries that
    same disclosure for a control-flow exception (KeyboardInterrupt/
    SystemExit), which must keep its own type and so can no longer
    carry the disclosure in its message text -- see the
    ``except BaseException`` blocks below. Falls back to the module
    ``_LOGGER`` when omitted OR explicitly passed ``None`` (post-merge
    review round 2: a bare ``if logger is not None:`` guard meant the
    disclosure was silently dropped -- not the exception, not a log,
    not a payload field -- anywhere nothing was threaded through,
    which a test then pinned as acceptable rather than a gap to close).
    """
    logger = logger or _LOGGER
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

    # Plugin resolves + is actually installed. getPlugin itself never
    # raises (confirmed live 2026-08-24) -- see _resolve_plugin.
    plugin = _resolve_plugin(indigo_module, plugin_id)

    # isEnabled / isRunning -- Indigo's own PluginDisabled covers a
    # disabled plugin (confirmed live: executeAction on a disabled
    # plugin raises "PluginDisabled: plugin X is not enabled"), but
    # not a crashed-yet-enabled one, which would otherwise surface as
    # an unexplained executeAction failure with no named cause.
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

    # Best-effort Actions.xml validation -- a BETTER-ERROR layer, not
    # a safety net (Indigo's own InvalidAction already guards a bad
    # action id -- confirmed live, though only when waitUntilDone is
    # true, see below). Degrades to attempting the dispatch when the
    # XML itself can't be read/parsed, rather than refusing the call
    # outright.
    action, action_validated, action_validated_reason = _try_validate_action(
        plugin, plugin_id, action_id
    )

    # Narrow the degraded path: with the action unvalidated, NEITHER
    # its declared fields NOR its deviceFilter are knowable, and
    # unlike a bad action id, Indigo has NO backstop at all for a
    # mistyped prop or a wrong device -- deviceFilter is enforced
    # client-side by the UI, not by executeAction, and a mistyped prop
    # is silently dropped regardless. So only dispatch here when
    # there's nothing device/prop-shaped that could go wrong silently;
    # otherwise refuse, naming what could not be checked. The origin
    # use case (a hidden action with real but undeclared props) is
    # unaffected -- that path always has a READABLE Actions.xml with
    # the action legitimately declaring zero fields, i.e. action is
    # NOT None there.
    if action is None and (raw_props or device_id is not None):
        unchecked = []
        if raw_props:
            unchecked.append("props")
        if device_id is not None:
            unchecked.append("device_id")
        raise ValueError(
            f"plugin {plugin_id!r}'s action {action_id!r} could not be "
            f"validated ({action_validated_reason}), so its declared "
            f"fields and deviceFilter are unknown; refusing to dispatch "
            f"with {' and '.join(unchecked)} that could not be checked. "
            "Retry with no props/device_id, or make Actions.xml readable "
            "first."
        )

    # deviceFilter / device_id: requirement, then existence, then
    # ownership. action is guaranteed not None below whenever
    # device_id is not None -- the gate above already refused
    # device_id on the degraded (action is None) path.
    if action is not None and action["device_required"] and device_id is None:
        raise ValueError(
            f"action {action_id!r} requires a device (deviceFilter="
            f"{action['device_filter']!r}); pass device_id."
        )
    device_validated = None
    device_validated_reason = None
    if device_id is not None:
        dev = _lookup_or_raise(indigo_module.devices, device_id, "device")
        device_filter = action["device_filter"]
        # Try the self-scoped alternatives FIRST, even for a mixed
        # filter (e.g. "self.zone, indigo.relay") -- a match there is
        # still a real, checkable confirmation regardless of what else
        # the filter allows. Only a filter that is FULLY self-scoped
        # (_is_self_scoped_filter) and still doesn't match is a hard
        # rejection: every alternative was checkable and none matched.
        # A mixed filter that doesn't match the self-scoped
        # alternative(s) is left unvalidated, not rejected -- it may
        # still match the non-self alternative this module can't
        # check.
        if device_filter and _device_matches_self_filter(dev, plugin_id, device_filter):
            device_validated = True
        elif device_filter and _is_self_scoped_filter(device_filter):
            raise ValueError(
                f"device {device_id} does not match action "
                f"{action_id!r}'s deviceFilter {device_filter!r}: it "
                f"belongs to plugin {getattr(dev, 'pluginId', None)!r} "
                f"(deviceTypeId {getattr(dev, 'deviceTypeId', None)!r}), "
                f"but this action requires "
                f"{_describe_device_filter_requirement(device_filter)}."
            )
        else:
            device_validated = False
            device_validated_reason = _device_validation_gap_reason(device_filter)

    # Prop key/value validation. An action that couldn't be validated
    # at all, or one that validated but declares zero ConfigUI fields,
    # has no allowlist to check against -- an ABSENT one, not an empty
    # one -- so props pass through unchecked rather than refusing the
    # call for the whole hidden-action class (this issue's own origin
    # case). Confirmed live (2026-08-24) against lite's own hidden mcp
    # action that props genuinely do cross the boundary intact for a
    # ConfigUI-less action -- this path is sound, not just permissive.
    declared_fields = {p["id"]: p for p in action["props"]} if action is not None else {}
    uses_template = bool(action is not None and action.get("uses_template"))
    props_unenforceable = []
    if declared_fields:
        unknown_props = set(raw_props) - set(declared_fields)
        if unknown_props and not uses_template:
            raise ValueError(
                f"unknown prop(s) {sorted(unknown_props)} for action "
                f"{action_id!r}; declared fields: {sorted(declared_fields)}"
            )
        # unknown_props can still be non-empty here ONLY when
        # uses_template: the visible field set is known-incomplete (a
        # <Template> include may declare more), so an "unknown" key may
        # legitimately belong to it -- skipped below rather than
        # refused, same reasoning as the no-declared-fields branch.
        for key, value in raw_props.items():
            if key not in declared_fields:
                continue
            allowed_values = declared_fields[key].get("values")
            if isinstance(allowed_values, list):  # static enum only
                allowed = {
                    str(opt["value"]) for opt in allowed_values
                    if opt["value"] is not None
                }
                if not allowed and allowed_values:
                    # Every <Option> in this static list lacked a
                    # value attribute -- the enum is UNENFORCEABLE,
                    # not "every value is invalid". Refusing here
                    # would leave the model with allowed: [] and
                    # nothing to retry toward (an unactionable refusal
                    # loop); pass the value through unchecked instead,
                    # same as a genuinely dynamic menu -- but record
                    # it, since props_validated:true must not then
                    # also imply this field's value was actually
                    # checked.
                    props_unenforceable.append(key)
                    continue
                if str(value) not in allowed:
                    unread = len(allowed_values) - len(allowed)
                    unread_note = ""
                    if unread == 1:
                        unread_note = (
                            " (1 further declared option could not be "
                            "read -- its <Option> carries no value "
                            "attribute)"
                        )
                    elif unread > 1:
                        unread_note = (
                            f" ({unread} further declared options could "
                            "not be read -- their <Option> elements carry "
                            "no value attribute)"
                        )
                    raise ValueError(
                        f"prop {key!r} = {value!r} is not a valid value for "
                        f"action {action_id!r}; allowed: {sorted(allowed)}"
                        f"{unread_note}"
                    )
        hidden_ids = {fid for fid, f in declared_fields.items() if f.get("hidden")}
        props_not_supplied = sorted(set(declared_fields) - set(raw_props) - hidden_ids)
        props_validated = True
        # The declared field set is "fully known" only when nothing
        # about it is incomplete -- no unenforceable enum, no id-less
        # fields this parser had to skip, no <Template> include that
        # may declare more. Only then is props_not_supplied a
        # trustworthy positive "nothing missing" claim (post-merge
        # review round 2: props_not_supplied: [] alongside
        # skipped_options > 0 was a self-contradiction -- "nothing
        # missing" next to "some values couldn't even be checked"),
        # and only then does this call count toward `verified` below.
        incomplete_notes = []
        if props_unenforceable:
            incomplete_notes.append(
                f"prop(s) {sorted(props_unenforceable)}: every <Option> on "
                "that field lacked a value attribute, so its legal values "
                "are unknown and the supplied value was passed through "
                "unchecked"
            )
        if action.get("skipped_fields"):
            incomplete_notes.append(
                f"{action['skipped_fields']} declared field(s) have no id "
                "and could not be referenced or validated at all"
            )
        if uses_template:
            incomplete_notes.append(
                "ConfigUI mixes a <Template> include with readable fields "
                "-- the include's own fields are unknown, so an "
                "unrecognised prop name may legitimately belong to it and "
                "was passed through unchecked"
            )
        if incomplete_notes:
            props_validated_reason = (
                "props_validated is true only for the fields this module "
                "could actually see -- the declared field set is "
                "known-incomplete: " + "; ".join(incomplete_notes)
            )
        else:
            props_validated_reason = None
    else:
        props_not_supplied = None
        props_validated = False
        if action is None:
            props_validated_reason = (
                "action itself could not be validated "
                f"({action_validated_reason}), so its declared props are "
                "unknown"
            )
        else:
            # Prefer the action's own parse-time reason (set by
            # _parse_action_element -- generic, <Template>-include, or
            # all-fields-id-less) over a single hardcoded sentence: for
            # a <Template file="..."/> ConfigUI, "declares no ConfigUI
            # fields" is FALSE and dangerous -- fields genuinely exist
            # and may be required, and this would tell the caller the
            # opposite. The dispatch-specific caveat about silent
            # discarding is appended regardless of which reason
            # applies.
            props_validated_reason = (
                f"{action['props_undeclared_reason']} -- an unrecognised "
                "prop name is silently discarded by Indigo (no error "
                "raised), so this call can report success even if a "
                "required field was omitted or a prop name was mistyped"
            )

    # A single completeness gate the payload and `verified` both rely
    # on -- props_validated alone said only "everything we COULD check,
    # checked out", which is not the same claim as "we could check
    # everything".
    props_fully_known = (
        props_validated
        and not props_unenforceable
        and not (action is not None and action.get("skipped_fields"))
        and not uses_template
    )

    # Confirmed live (2026-08-24): executeAction("bogusId",
    # waitUntilDone=False) returns None with NO exception at all --
    # InvalidAction only raises with waitUntilDone=True. The whole
    # justification for dispatching on the degraded path (action is
    # None) is that Indigo's own InvalidAction catches a bad id; that
    # only holds with waitUntilDone=True, so force it here rather than
    # silently letting a typo'd id through with a success-shaped
    # response, and disclose the override instead of hiding it.
    effective_wait_until_done = wait_until_done
    wait_until_done_overridden = False
    if not action_validated and not wait_until_done:
        effective_wait_until_done = True
        wait_until_done_overridden = True

    indigo_props = indigo_module.Dict(raw_props)
    kwargs = {"props": indigo_props, "waitUntilDone": effective_wait_until_done}
    if device_id is not None:
        kwargs["deviceId"] = device_id

    try:
        result = plugin.executeAction(action_id, **kwargs)
    except Exception as exc:
        # executeAction WAS called -- a non-idempotent action (run a
        # sprinkler zone, send a push notification) may have already
        # partially or fully completed. Raising anything other than
        # ValueError/TypeError routes this to mcp_handler's back-off
        # bucket (JSON-RPC internal error) rather than its
        # self-correct-and-retry one -- retrying blindly is the wrong
        # response to a fault after a write. {type(exc).__name__} so a
        # bare exception with no message doesn't render as
        # "...before this error: ." with nothing after the colon.
        raise RuntimeError(
            f"plugin {plugin_id!r} action {action_id!r} WAS DISPATCHED to "
            f"executeAction and may have partially or fully completed "
            f"before this error: {type(exc).__name__}: {exc}. Do NOT "
            f"blindly retry -- check the device/plugin state and the "
            f"{plugin_id!r} plugin's own event log first."
        ) from exc
    except BaseException:
        # Same already-happened guarantee as above, but a control-flow
        # exception (KeyboardInterrupt/SystemExit -- Indigo shutting
        # the plugin host down mid-call) MUST keep its own type so the
        # host can act on it: wrapping it into RuntimeError (a plain
        # Exception subclass) would let mcp_handler's `except
        # Exception` bucket catch and swallow it, report a JSON-RPC
        # -32603 error, and let the request loop continue as though
        # nothing happened -- Indigo could no longer shut the plugin
        # down cleanly, and a Ctrl-C would be misreported to the model
        # as a dispatch fault on this one action. The exception itself
        # can no longer carry the WAS-DISPATCHED disclosure in its
        # message text, so it goes to the logger instead, then the
        # exception is re-raised completely unchanged.
        try:
            logger.warning(
                "plugin %r action %r WAS DISPATCHED to executeAction and "
                "may have partially or fully completed before a "
                "control-flow exception interrupted this call; NOT "
                "retried automatically -- check the device/plugin state "
                "and the %r plugin's own event log first.",
                plugin_id, action_id, plugin_id,
            )
        except BaseException:
            # The disclosure must never displace the exception it
            # discloses -- a broken logger (a full disk, a misconfigured
            # handler) raising here would otherwise propagate INSTEAD
            # of the original control-flow exception, and could even
            # arrive as a TypeError/ValueError that mcp_handler then
            # routes to its self-correct-and-retry bucket -- reporting
            # a host shutdown to the model as an input error inviting a
            # retry, which is worse than not logging at all.
            pass
        raise

    payload = {
        "plugin_id": plugin_id,
        "action_id": action_id,
        "device_id": device_id,
        # The EFFECTIVE value actually passed to executeAction, not
        # necessarily what the caller asked for -- when overridden
        # (see wait_until_done_overridden below), the caller's
        # original request goes in wait_until_done_requested instead.
        # Reporting the ignored request here made the payload
        # self-contradictory: wait_until_done:false alongside a
        # result value the tool description says is unreachable when
        # false.
        "wait_until_done": effective_wait_until_done,
        "action_validated": action_validated,
        "props_validated": props_validated,
    }
    # Both wait_until_done_overridden facts in ONE block (post-merge
    # review round 2, LOW: these were two separate `if
    # wait_until_done_overridden:` blocks with unrelated payload keys
    # sandwiched between them).
    if wait_until_done_overridden:
        payload["wait_until_done_requested"] = wait_until_done
        payload["wait_until_done_overridden"] = True
        payload["wait_until_done_overridden_reason"] = (
            "action could not be validated, so waitUntilDone was forced "
            "true: Indigo's own InvalidAction only raises for a bad "
            "action id when waitUntilDone is true (confirmed live); with "
            "false it returns None with no error at all"
        )
    if not action_validated:
        payload["action_validated_reason"] = action_validated_reason
        # A third unknowable alongside the action's declared fields:
        # with the action itself unvalidated, whether it REQUIRES a
        # device at all is also unknown. executeAction defaults
        # deviceId to 0 regardless, so a device-requiring action
        # dispatched with none at all can reach this point -- the
        # target plugin's own callback then fails silently in ITS
        # event log, invisible here, so completed_unverified below may
        # mean the action never really ran.
        payload["device_requirement_unknown_reason"] = (
            "action could not be validated, so whether it requires a "
            "device at all is unknown; if it does, executeAction was "
            "still called with none -- a completed_unverified result "
            "here does not guarantee the action's callback actually ran "
            "(check the target plugin's own event log)"
        )
    if device_validated is not None:
        payload["device_validated"] = device_validated
        if not device_validated:
            payload["device_validated_reason"] = device_validated_reason
    # props_not_supplied is a POSITIVE "nothing missing" claim, so it
    # is only included when the declared field set is fully known --
    # gated on props_fully_known, not merely props_validated (post-
    # merge review round 2: props_not_supplied: [] alongside
    # skipped_options > 0 contradicted itself). props_validated_reason
    # is included whenever it's set, independent of the props_validated
    # boolean, since the field set can now be incomplete (and need
    # explaining) even while props_validated is true.
    if props_fully_known:
        payload["props_not_supplied"] = props_not_supplied
    if props_validated_reason is not None:
        payload["props_validated_reason"] = props_validated_reason
    if props_unenforceable:
        payload["props_unenforceable"] = sorted(props_unenforceable)
        payload["props_unenforceable_reason"] = (
            "every <Option> on these field(s) lacked a value attribute, "
            "so their legal values are unknown; the supplied value(s) "
            "were passed through unchecked rather than refused"
        )
    if action is not None and action.get("skipped_fields"):
        payload["skipped_fields"] = action["skipped_fields"]
    if action is not None and action.get("skipped_options"):
        payload["skipped_options"] = action["skipped_options"]

    # ONE verified computation covering props (fully known AND
    # checked), device ownership, applied to every branch below --
    # not just the "completed"/"completed_unverified" choice.
    # Previously completed_unverified fired only on props_validated
    # being false, never on device_validated being false (a wrong
    # device has NO Indigo-side backstop at all, unlike props, which
    # at least get a partial check) nor on the field set merely being
    # INCOMPLETE despite props_validated:true (an unenforceable enum,
    # id-less fields, or a mixed <Template> -- post-merge review round
    # 2). And the dispatched/returned branches never carried any
    # marker at all, though "dispatched" (no wait, no completion
    # signal whatsoever) is the weakest claim of the three.
    # device_validated is None (device_id not supplied, or its
    # deviceFilter didn't require one) does not itself make the call
    # unverified -- only an explicit False does.
    verified = props_fully_known and device_validated is not False
    payload["verified"] = verified

    if result is None:
        if not effective_wait_until_done:
            # Queued and not awaited at all -- no completion signal
            # whatsoever, not even a raised error could ever surface.
            payload["result"] = "dispatched"
        elif verified:
            payload["result"] = "completed"
        else:
            # Ran to completion (we waited), but the action's props
            # and/or the target device's ownership could not be
            # confirmed beforehand -- distinct from "completed" so a
            # model reading only the top-line result field can't
            # mistake this for a fully verified call.
            payload["result"] = "completed_unverified"
    else:
        payload["result"] = "returned"
        try:
            payload["value"] = _json_safe(result)
        except Exception as exc:
            # The action itself already succeeded (executeAction
            # returned a value); only serialising it failed. Same
            # already-happened guarantee as the executeAction except
            # below, including {type(exc).__name__} so a bare
            # exception with no message (e.g. a raw KeyboardInterrupt)
            # doesn't render as "...serialised: ." with nothing after
            # the colon.
            raise RuntimeError(
                f"plugin {plugin_id!r} action {action_id!r} executed "
                "successfully (executeAction returned a value) but that "
                f"return value could not be serialised: "
                f"{type(exc).__name__}: {exc}. The action itself already "
                "ran -- do not retry it."
            ) from exc
        except BaseException:
            # Same already-happened guarantee, but a control-flow
            # exception (KeyboardInterrupt/SystemExit) MUST keep its
            # own type so the host can act on it -- wrapping it into
            # RuntimeError would let mcp_handler's Exception bucket
            # catch and swallow it, and the request loop would
            # continue as though nothing happened. The exception can
            # no longer carry the disclosure in its text, so it goes
            # to the logger instead, then the exception is re-raised
            # completely unchanged.
            try:
                logger.warning(
                    "plugin %r action %r executed successfully "
                    "(executeAction returned a value) but a control-flow "
                    "exception interrupted serialising the return value; "
                    "the action itself already ran -- do not retry it.",
                    plugin_id, action_id,
                )
            except BaseException:
                # See the dispatch-site except block above: the
                # disclosure must never displace the exception it
                # discloses.
                pass
            raise
    return payload


def register(handler, *, indigo_module, logger=None, **_):
    """Register the plugin-actions tools onto the given MCPHandler.

    ``logger`` (optional) threads into ``plugin_execute_action`` so a
    control-flow exception (KeyboardInterrupt/SystemExit) that must
    keep its own type on re-raise can still have its WAS-DISPATCHED
    disclosure recorded somewhere.
    """
    handler.register_tool(
        name="list_plugin_actions",
        description=(
            "List one plugin's declared Actions.xml actions: id, name, "
            "callback method, whether it's hidden (uiPath=\"hidden\" -- "
            "programmatic-only, not meant for menus), whether it requires "
            "a device, its ConfigUI fields as props (id/type/label/"
            "default), and notes (calling guidance pulled from label "
            "fields and <Description> elements that isn't itself a prop). "
            "An action with NO ConfigUI fields at all is flagged "
            "props_undeclared:true rather than reading as taking no "
            "arguments -- common for uiPath=\"hidden\" actions, which may "
            "still accept props. A field with hidden:true is "
            "runtime-computed by the plugin -- don't invent a value for "
            "it. A menu field with no enumerable options is flagged "
            "values:\"dynamic\": for indigo.devices / indigo.actionGroups "
            "/ indigo.variables menus, values_source_tool names the lite "
            "tool that lists the real values (list_devices / "
            "list_action_groups / list_variables); otherwise "
            "values_source is the plugin's own callback, unresolvable "
            "cross-plugin. A static menu lists its real values, enforced "
            "at dispatch time. Top-level enabled and running are both "
            "reported -- they can differ, and a crashed-but-enabled "
            "plugin still fails plugin_execute_action. Use "
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
            "declared props for this plugin. When the action's fields "
            "could be checked (props_validated:true), declared fields "
            "you don't supply are fine (defaults/optional) but are "
            "listed back in props_not_supplied -- check that field "
            "before treating the call as fully configured; the key is "
            "absent when props_validated is false, since there was no "
            "known field set to compare against. An unrecognized prop key (or an "
            "out-of-range value for a static enum field) is refused ONLY "
            "when the action declares fields to check against "
            "(props_validated:true); one with none declared has no "
            "allowlist, so props pass through unchecked -- "
            "props_validated:false with a reason then warns that a "
            "mistyped prop name is silently dropped by Indigo, not "
            "refused. If Actions.xml itself couldn't be read "
            "(action_validated:false), the call only proceeds when NO "
            "props and NO device_id were given (waitUntilDone is then "
            "forced true so Indigo's own InvalidAction can catch a bad "
            "action id -- wait_until_done_overridden:true discloses "
            "that, with the caller's original request in "
            "wait_until_done_requested); otherwise it's refused rather "
            "than sending unchecked props/device_id -- and even the one "
            "shape that IS attempted has an unknowable third gap: "
            "whether the action requires a device at all "
            "(device_requirement_unknown_reason), since executeAction "
            "still gets called with none either way. device_id is "
            "checked for existence AND, against any self-scoped "
            "alternative in the action's deviceFilter, that the device "
            "actually belongs to this plugin (device_validated in the "
            "payload says which); a wrong device has NO Indigo-side "
            "backstop, unlike a wrong action id. wait_until_done "
            "(default true, and always reflects the value ACTUALLY "
            "used -- not the caller's request when overridden) is "
            "passed to executeAction's waitUntilDone: true blocks until "
            "the action finishes -- a None return then means "
            "result:\"completed\" when verified:true (both props AND "
            "device ownership confirmed, or not applicable), or "
            "result:\"completed_unverified\" when verified:false; false "
            "means Indigo queued the action without waiting at all "
            "(result:\"dispatched\" on a None return -- weaker still, no "
            "completion signal whatsoever, not even an error could ever "
            "surface; verified is reported on every branch, including "
            "this one). props is a plain object, converted to "
            "indigo.Dict internally. A non-None return reports "
            "result:\"returned\" with the value."
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
        handler=lambda **args: _plugin_execute_action_handler(
            args, indigo_module, logger=logger
        ),
    )
