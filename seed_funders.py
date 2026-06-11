"""Curated starter list of funders relevant to Eye Spy.

EINs verified against the ProPublica Nonprofit Explorer API (June 2026).
Two groups:
  - Jacksonville / Northeast Florida foundations (geographic fit)
  - National funders focused on blindness / vision / sight (mission fit)
"""

EYESPY_EIN = "922490137"
EYESPY_NAME = "Eye Spy Foundation Inc"

SEED_FUNDERS = [
    # --- Jacksonville / Northeast Florida ---
    {"ein": "596368632", "name": "Jessie Ball duPont Fund", "tag": "Jacksonville"},
    {"ein": "596150746", "name": "The Community Foundation for Northeast Florida", "tag": "Jacksonville"},
    {"ein": "205440166", "name": "The Chartrand Foundation", "tag": "Jacksonville"},
    {"ein": "592891582", "name": "Lucy Gooding Charitable Foundation Trust", "tag": "Jacksonville"},
    {"ein": "593249687", "name": "Jacksonville Jaguars Foundation", "tag": "Jacksonville"},
    {"ein": "592981682", "name": "DuBow Family Foundation", "tag": "Jacksonville"},
    {"ein": "830432246", "name": "North Florida Lions Eye Foundation", "tag": "Jacksonville + Vision"},
    # --- National vision / blindness funders ---
    {"ein": "131740463", "name": "Lavelle Fund for the Blind", "tag": "Vision"},
    {"ein": "136120440", "name": "Readers Digest Partners for Sight Foundation", "tag": "Vision"},
    {"ein": "316034001", "name": "Delta Gamma Foundation (Service for Sight)", "tag": "Vision"},
    {"ein": "650286170", "name": "The Gibney Family Foundation", "tag": "Vision"},
]

# Keywords used to flag grants likely relevant to blind / low-vision work
VISION_KEYWORDS = ["blind", "vision", "visually", "sight", "braille", "eye ",
                   "low-vision", "low vision", "macular", "retin", "glaucoma", "optic"]


def is_vision_match(*texts):
    blob = " ".join(t for t in texts if t).lower()
    return any(k in blob for k in VISION_KEYWORDS)
