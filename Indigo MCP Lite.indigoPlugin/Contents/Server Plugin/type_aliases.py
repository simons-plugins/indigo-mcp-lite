"""Static synonym mapping for FTS5 lexical bridging.

Maps Indigo's ``deviceTypeId`` strings (e.g. ``"dimmer"``,
``"thermostatHeating"``) to space-separated common-noun synonyms
the user might naturally type when searching. Used by ``indexer.py``
to populate the ``aliases`` column of each indexed device — bm25
weights this column lower than ``name`` (see
``tools/find_devices.py``) so a real name match still ranks higher,
but a query like ``light`` will surface every device with
``deviceTypeId="dimmer"`` even though no device actually has the
word "light" in its name.

Hand-curated. Growing this is a one-line PR — tune based on real
miss patterns from jarvis (see Task 6.14 in the Phase 6 plan and
issue #3 for the user-extensible synonym dict).

See design doc §7.2.
"""


TYPE_ALIASES = {
    # Lights
    "dimmer":               "light lamp bulb fixture lighting",
    "relay":                "switch outlet plug socket",
    "shelly-plus-1-pm":     "switch relay outlet meter",
    "shelly-plus-plug":     "plug socket outlet",
    "shelly-plus-2-pm":     "switch relay outlet meter dual",

    # Climate
    "thermostat":           "climate hvac heat cool temperature",
    "thermostatHeating":    "radiator trv valve heater",
    "thermostatCooling":    "ac aircon cooler",
    "shelly-trv":           "radiator trv valve heater thermostat",

    # Sensors
    "sensor":               "detector reading monitor",
    "leakSensor":           "leak moisture water damp flood wet",
    "smokeSensor":          "smoke fire alarm",
    "motionSensor":         "motion movement pir occupancy",
    "doorSensor":           "door window contact open close",
    "temperatureSensor":    "temperature thermometer warmth",
    "humiditySensor":       "humidity moisture damp",
    "luminanceSensor":      "light brightness lux ambient",
    "radarSensor":          "radar presence motion mmwave",
    "alarmZone":            "alarm zone intrusion security",
    "masqSensor":           "sensor presence detector",

    # Security
    "lock":                 "deadbolt door entry",
    "alarm":                "security siren intrusion",

    # Outdoor
    "sprinkler":            "irrigation watering hose garden",
    "poolPump":             "pool spa pump filtration",

    # Audio/AV
    "speaker":              "audio music sound stereo",
    "tv":                   "television display screen",

    # Coverings
    "shade":                "blind curtain shutter cover",

    # Power / network
    "smartPlug":            "outlet socket plug power",
    "energyMeter":          "energy power consumption usage",
    "virtualGroupEnergyMeter": "energy power consumption group total",
    "lanPing":              "ping network online host",

    # Misc
    "fan":                  "fan ventilator extractor",
    "garageDoor":           "garage door opener",
    "irrigationController": "irrigation sprinkler watering",
    "camera":               "camera cctv video",
    "timer":                "timer countdown",
    "component-switch":     "switch toggle component",
    "component-input":      "input button trigger",
    "auto_lights_zone":     "auto lights zone area room",
    "area":                 "area zone room presence",
    "VoiceMonkeyDevice":    "voice command alexa",
    "unifiAccessPoint":     "wifi access point ap network",
    "unifiClient":          "wifi client device network",
    "unifiDevice":          "switch network unifi",
}
