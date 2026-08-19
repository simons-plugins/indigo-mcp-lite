---
parent: Decisions
nav_order: 2
title: "ADR-0002: Infer device references from plugin action prop values, reported as a distinct role"
status: "accepted"
date: 2026-08-19
decision-makers: solo (Simon)
consulted: none
informed: none
supersedes: none
superseded_by: none
---
# ADR-0002: Infer device references from plugin action prop values, reported as a distinct role

## Context and Problem Statement

`find_automation_references` answers "which automations touch this
device?" — the question asked before renaming or deleting anything.
It indexed a step's declared `<DeviceID>` field.

Plugin action steps frequently name their target device **inside their
own parameters** instead, surfaced as `props` since #54. A live census
found **164 of 320** props-carrying plugin steps have no sibling
`<DeviceID>` at all. Those devices returned **zero references** from
the one tool whose job is preventing exactly that false negative.

Indigo's own `getDependencies` has the identical gap — verified live:
it returns `actionGroups: []` for a thermostat that nine action groups
drive through ShellyMQTT `device-id` props. So this is not parity work;
it is a capability the platform does not have.

How should a reference that is *not declared anywhere* be found, and
how should it be reported?

## Decision Drivers

* **False negatives are the harm.** A missing reference gets an
  automation deleted. A spurious one costs a moment's review. The
  design must be asymmetric in that direction — but not so loose that
  the answer stops being trustworthy.
* **No per-plugin knowledge.** Any solution requiring a schema per
  plugin does not scale past the plugins we happen to have seen.
* **Key names cannot be pattern-matched.** Live keys include
  `device-id`, `device`, `dimmer_device_id`, `device1`…`device5`,
  `deepLinkDeviceId` — alongside id-shaped values that are *not*
  device references (`deepLinkPageId`, `uniqueIdentifier`, `variable`).
* **A caller must be able to tell inference from fact.** The tool is
  consumed by a model making a delete/keep decision.

## Considered Options

1. **Match prop values against the queried id**, ignoring key names.
2. **Allowlist of known device-referencing key names**, per plugin or
   by pattern.
3. **Leave it, document the blind spot** (the status quo from #57).

## Decision Outcome

Chosen option: **match prop values**, because it needs no per-plugin
knowledge and is provably safe.

Safety rests on a property confirmed by census rather than assumed:
**Indigo object ids are globally unique across every object type.**

```
ActionGroup 192 · Device 691 · TDTrigger 107 · Trigger 255
Variable 782 · ControlPage 4
total 2031 ids · distinct 2031 · collisions 0
```

So a prop value equal to a real device id **is** that device, whatever
the key is called. The false-positive candidates fall out for free —
`deepLinkPageId` holds a control-page id, which is not in the device
set. Option 2 was rejected because the key census shows no pattern to
match on; option 3 because the blind spot silently covers over half
the plugin action surface.

### The id set is never built

The design note for this work assumed building the known-id set per
parse. It is not needed: both tools take **one** id at a time, so
membership collapses to "does this id name a live object of the
requested kind?" — an O(1) probe.

That probe is load-bearing rather than decorative. An id that names
nothing has no uniqueness guarantee behind it and could collide with
an ordinary numeric parameter — a brightness level, a delay. Inference
is therefore gated on it, and only declared references are returned
when it fails.

### Reported as `acts_on_via_props`, never folded into `acts_on`

Inferred references get their own role plus `matched_props` naming the
parameters that matched. This is not cosmetic. A live example: a
notification step carrying `deepLinkDeviceId` genuinely references a
device, but it *deep-links* to it — it does not drive it. Folding that
into `acts_on` would make the tool assert something false, and the
evidence needed to judge would be gone. The separation keeps the tool
honest about what it knows versus what it inferred.

The complete list is still complete: `references` contains every
automation regardless of role, so one call answers "where is this used"
without the caller filtering roles or consulting a second tool. The
tool description says so explicitly, because an agent filtering to
`roles == ["acts_on"]` would reintroduce the very gap this closes.

### A skipped inference pass must announce itself

`resolve_name` — the obvious probe — is contracted as *best-effort,
degrades to `None`*, and returns `None` for **both** "no such object"
and "the lookup itself failed". Gating on it alone would let a
transient IOM problem produce a confident-looking zero. A dedicated
`_entity_presence` returns present / absent / unavailable, and when the
pass does not run the response carries a `props_inference` note saying
so and naming the fallback.

## Consequences

* Good: 13 devices on the development server that had zero declared
  references are now correctly reported as driven — one by eight
  action groups. `get_dependencies` cross-references this tool rather
  than telling callers to read raw props themselves.
* Good: no per-plugin maintenance. A new plugin's steps are indexed
  the day it is installed.
* Bad: an inferred reference can be a weaker relationship than
  "drives" (the deep-link case). Mitigated by the separate role and
  `matched_props`, not eliminated.
* Bad: a device deleted from Indigo but still referenced in the
  database file gets declared-only results, since the presence probe
  reads the live IOM. Acceptable: the tool's use is *pre*-deletion.
* Neutral: `%%d:<id>:<state>%%` substitution references in message
  bodies are deliberately **not** matched — a read-in-text reference
  is a different relationship needing its own role. Tracked as #63.

## Confirmation

An exhaustive audit over the live database: every known device id
searched as a substring of all 308 props-carrying steps, compared
against what the walker matched. Zero misses of this kind — the only
unmatched occurrences were the substitution syntax above.

`indigo-home-intelligence` carries a parallel reverse index feeding
its rule-write gate and has been ported to match (its ADR-0011), with
one deliberate divergence in how a skipped pass is signalled.
