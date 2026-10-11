"""Formation slots as (slot, u, v) in the team's own frame.

u is metres from the team's own goal line and v runs across the pitch, with the
team's right flank at small v. These are neutral positions with the ball near
the centre spot; the engine shifts and stretches them every tick.
"""

FORMATIONS: dict[str, list[tuple[str, float, float]]] = {
    "4-3-3": [
        ("GK", 4, 34), ("RB", 28, 8), ("RCB", 22, 24), ("LCB", 22, 44), ("LB", 28, 60),
        ("DM", 38, 34), ("RCM", 48, 22), ("LCM", 48, 46),
        ("RW", 64, 10), ("ST", 70, 34), ("LW", 64, 58),
    ],
    "4-2-3-1": [
        ("GK", 4, 34), ("RB", 28, 8), ("RCB", 22, 24), ("LCB", 22, 44), ("LB", 28, 60),
        ("RDM", 38, 26), ("LDM", 38, 42),
        ("RAM", 58, 12), ("CAM", 58, 34), ("LAM", 58, 56), ("ST", 70, 34),
    ],
    "4-4-2": [
        ("GK", 4, 34), ("RB", 28, 8), ("RCB", 22, 24), ("LCB", 22, 44), ("LB", 28, 60),
        ("RM", 48, 9), ("RCM", 42, 26), ("LCM", 42, 42), ("LM", 48, 59),
        ("RST", 66, 28), ("LST", 66, 40),
    ],
}

_ROLE_BY_SLOT = {
    "GK": "GK",
    "RB": "DEF", "RCB": "DEF", "LCB": "DEF", "LB": "DEF",
    "DM": "MID", "RDM": "MID", "LDM": "MID", "RCM": "MID", "LCM": "MID",
    "RM": "MID", "LM": "MID", "RAM": "MID", "CAM": "MID", "LAM": "MID",
    "RW": "FWD", "LW": "FWD", "ST": "FWD", "RST": "FWD", "LST": "FWD",
}


def role_of(slot: str) -> str:
    return _ROLE_BY_SLOT[slot]
