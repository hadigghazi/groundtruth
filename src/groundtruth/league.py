"""A fictional four-club league.

Every club, ground and player name is invented. Names are drawn from several
language origins so the multi-language output has varied names to handle.
The league is seeded, so the same squads come back on every run.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sim.formations import FORMATIONS, role_of
from .sim.tactics import Tactics

NAMES: dict[str, tuple[list[str], list[str]]] = {
    "en": (["Callum", "Rhys", "Owen", "Toby", "Jamie", "Lewis", "Kieran", "Ethan", "Joel", "Archie"],
           ["Hartley", "Brennan", "Ashworth", "Calloway", "Whitlock", "Penrose", "Holloway", "Fairbairn", "Sutcliffe", "Thorne"]),
    "es": (["Mateo", "Sergio", "Pablo", "Adrián", "Hugo", "Marcos", "Rubén", "Diego", "Álvaro", "Iván"],
           ["Cuesta", "Ibarra", "Salcedo", "Montoya", "Arenas", "Robles", "Peralta", "Navarro", "Esteban", "Villalba"]),
    "ar": (["Karim", "Yousef", "Omar", "Rami", "Tarek", "Bilal", "Samir", "Nabil", "Ziad", "Fadi"],
           ["Haddad", "Khoury", "Mansour", "Nassar", "Saade", "Fares", "Aoun", "Daher", "Jaber", "Rahal"]),
    "fr": (["Théo", "Bastien", "Maxime", "Yanis", "Clément", "Mathis", "Romain", "Enzo", "Lucien", "Gaël"],
           ["Lefèvre", "Moreau", "Garnier", "Rousseau", "Fontaine", "Chevalier", "Perrin", "Lemaire", "Bertin", "Marchand"]),
    "pt": (["João", "Tiago", "Rafael", "Diogo", "Rui", "Gonçalo", "Vitor", "Caio", "Thiago", "Duarte"],
           ["Moreira", "Cardoso", "Teixeira", "Barros", "Pinheiro", "Correia", "Antunes", "Figueira", "Lobo", "Viana"]),
    "wa": (["Kwame", "Kofi", "Yaw", "Ayo", "Chidi", "Emeka", "Femi", "Sekou", "Ibrahima", "Moussa"],
           ["Mensah", "Owusu", "Asante", "Okafor", "Nwosu", "Diallo", "Bamba", "Ofori", "Adjei", "Danso"]),
    "nordic": (["Erik", "Mikkel", "Jonas", "Oskar", "Henrik", "Lasse", "Viktor", "Anders", "Emil", "Tobias"],
               ["Lindqvist", "Halvorsen", "Dahl", "Berg", "Nygaard", "Holm", "Strand", "Eklund", "Sandberg", "Vik"]),
    "jp": (["Haruto", "Ren", "Sota", "Kaito", "Yuto", "Daiki", "Riku", "Kenta", "Shota", "Yuma"],
           ["Tanaka", "Sato", "Nakamura", "Kobayashi", "Mori", "Ishikawa", "Fujita", "Ogawa", "Sakai", "Hayashi"]),
    "de": (["Lukas", "Felix", "Niklas", "Moritz", "Jannik", "Leon", "Paul", "Fabian", "Malte", "Jonte"],
           ["Hoffmann", "Krüger", "Becker", "Vogel", "Schäfer", "Lorenz", "Wolff", "Seidel", "Kraus", "Hahn"]),
}

REFEREES = ["Ines Albrecht", "Marcus Oyelaran", "Sofia Lindgren", "Daniel Achterberg", "Leila Haddou"]
WEATHER = ["Clear, 14°C", "Light rain, 11°C", "Overcast, 9°C", "Windy, 12°C", "Clear, 7°C"]


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    short_name: str
    number: int
    role: str  # GK, DEF, MID or FWD
    pace: float
    passing: float
    dribbling: float
    tackling: float
    finishing: float
    endurance: float


@dataclass(frozen=True)
class Club:
    club_id: str
    name: str
    short_name: str
    ground: str
    colors: tuple[str, str]
    formation: str
    style: Tactics
    players: tuple[Player, ...]  # the 11 starters in formation-slot order, then 7 substitutes


CLUBS = [
    # id, name, short, ground, colours, formation, style, quality
    ("RVM", "Rivermouth Athletic", "Rivermouth", "Quayside Park", ("#0F7C8C", "#F2F2F2"), "4-3-3",
     Tactics(press_intensity=0.70, line_height=0.65, directness=0.35, tempo=0.65, width=0.60), 0.60),
    ("HLB", "Hollowbridge Rovers", "Hollowbridge", "Mill Lane", ("#E0A100", "#1B2A4A"), "4-4-2",
     Tactics(press_intensity=0.30, line_height=0.35, directness=0.70, tempo=0.50, width=0.50), 0.56),
    ("STM", "Stonemere United", "Stonemere", "Stonemere Arena", ("#4B5563", "#B5E61D"), "4-2-3-1",
     Tactics(press_intensity=0.50, line_height=0.55, directness=0.20, tempo=0.40, width=0.70), 0.60),
    ("KBY", "Kestrel Bay", "Kestrel Bay", "Cliffside Ground", ("#7A1F5C", "#9AD0F5"), "4-3-3",
     Tactics(press_intensity=0.50, line_height=0.50, directness=0.50, tempo=0.55, width=0.50), 0.55),
]

# Attribute means by role, before club quality and per-player noise.
_ROLE_MEANS = {
    "GK": dict(pace=0.35, passing=0.45, dribbling=0.25, tackling=0.20, finishing=0.10, endurance=0.55),
    "DEF": dict(pace=0.55, passing=0.50, dribbling=0.40, tackling=0.70, finishing=0.25, endurance=0.65),
    "MID": dict(pace=0.55, passing=0.68, dribbling=0.58, tackling=0.52, finishing=0.42, endurance=0.72),
    "FWD": dict(pace=0.70, passing=0.52, dribbling=0.66, tackling=0.30, finishing=0.68, endurance=0.60),
}
_BENCH_ROLES = ["GK", "DEF", "DEF", "MID", "MID", "FWD", "FWD"]


def _make_player(rng, club_id, number, role, quality, used):
    while True:
        origin = list(NAMES)[int(rng.integers(len(NAMES)))]
        first = str(rng.choice(NAMES[origin][0]))
        last = str(rng.choice(NAMES[origin][1]))
        if (first, last) not in used:
            used.add((first, last))
            break
    means = _ROLE_MEANS[role]
    attrs = {k: float(np.clip(m + (quality - 0.55) + rng.normal(0, 0.09), 0.05, 0.95)) for k, m in means.items()}
    return Player(
        player_id=f"{club_id}-{number:02d}",
        name=f"{first} {last}",
        short_name=f"{first[0]}. {last}",
        number=number,
        role=role,
        **attrs,
    )


def build_league(seed: int = 2026) -> dict[str, Club]:
    rng = np.random.default_rng(seed)
    used: set[tuple[str, str]] = set()
    league = {}
    for club_id, name, short, ground, colors, formation, style, quality in CLUBS:
        numbers = [1] + [int(n) for n in rng.choice(np.arange(2, 36), size=17, replace=False)]
        roles = [role_of(slot) for slot, _, _ in FORMATIONS[formation]] + _BENCH_ROLES
        players = tuple(
            _make_player(rng, club_id, num, role, quality, used) for num, role in zip(numbers, roles)
        )
        league[club_id] = Club(club_id, name, short, ground, colors, formation, style, players)
    return league
