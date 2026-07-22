"""Synonym groups for FTS5 alias expansion.

Complements ``type_aliases.py`` (deviceTypeId → vocabulary) with
*text-derived* expansion: when a word from any group below appears in
a device's name, model, or folder, the rest of the group is appended
to its ``aliases`` FTS column. That lets "lounge" find devices filed
under "Living Room", "telly" find the TV socket, and "boiler" find
heating gear — the synonym-recall benefit of semantic search, with
no embeddings, network calls, or dependencies.

Vocabulary is freshly authored for this plugin (UK-flavoured where
household speech and Indigo naming habits diverge). Groups are sets
of interchangeable words; multi-word phrases are allowed and matched
on the space-normalised text.

Growing this is a one-line PR — tune based on real miss patterns.
"""

SYNONYM_GROUPS = (
    # Rooms / places
    {"lounge", "living room", "sitting room", "front room"},
    {"kitchen", "cooking"},
    {"bedroom", "bed room"},
    {"bathroom", "bath", "washroom", "ensuite", "en suite"},
    {"toilet", "loo", "wc", "cloakroom"},
    {"garden", "yard", "outside", "outdoor", "exterior"},
    {"garage", "carport"},
    {"hallway", "hall", "landing", "corridor", "entrance"},
    {"study", "office", "den"},
    {"utility", "laundry"},
    {"loft", "attic"},
    {"cellar", "basement"},
    {"conservatory", "sunroom"},
    {"driveway", "drive"},
    {"porch", "doorstep", "front door"},
    {"patio", "terrace", "decking"},
    {"pantry", "larder"},

    # Heating / climate
    {"radiator", "trv", "rad"},
    {"boiler", "heating", "central heating"},
    {"thermostat", "stat", "temperature control"},
    {"underfloor", "ufh"},
    {"hot water", "cylinder", "immersion"},

    # Lighting
    {"lamp", "light", "bulb", "luminaire"},
    {"spotlight", "spots", "downlight", "downlighter"},
    {"strip", "led strip", "lightstrip"},
    {"pendant", "ceiling light"},

    # Media / appliances
    {"tv", "telly", "television"},
    {"hifi", "stereo", "speakers", "audio"},
    {"fridge", "refrigerator", "freezer"},
    {"washing machine", "washer"},
    {"dishwasher", "dish washer"},
    {"kettle", "coffee"},
    {"hoover", "vacuum"},

    # Openings / security
    {"curtains", "blinds", "shades", "shutters"},
    {"gate", "barrier"},
    {"doorbell", "bell", "chime"},
    {"cctv", "camera", "cam"},
    {"alarm", "siren", "sounder"},

    # Power / energy
    {"socket", "plug", "outlet", "power point"},
    {"meter", "consumption", "usage", "energy monitor"},
    {"solar", "pv", "panels"},
    {"battery", "batteries"},

    # Water / outdoors
    {"irrigation", "sprinkler", "watering"},
    {"pond", "fountain", "water feature"},
    {"leak", "flood", "moisture"},

    # Occupancy
    {"presence", "occupancy", "motion", "pir"},
)


def expand(text):
    """Return space-joined synonym words for any group members found
    in ``text`` (case-insensitive). The matched words themselves are
    not repeated — they're already indexed from their source column.
    Deterministic output order (group order, then sorted within a
    group) so reindexing is stable."""
    haystack = f" {' '.join(str(text).lower().split())} "
    out = []
    seen = set()
    for group in SYNONYM_GROUPS:
        hit = any(f" {term} " in haystack or (
            " " not in term and term in haystack.split()
        ) for term in group)
        if not hit:
            continue
        for term in sorted(group):
            if f" {term} " in haystack:
                continue  # already present in the source text
            if term not in seen:
                seen.add(term)
                out.append(term)
    return " ".join(out)
