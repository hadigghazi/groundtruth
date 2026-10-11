"""Fixed overlay wording in English, Spanish and Arabic.

These templates carry the stat cues (the fast lane, no LLM) and stand in for
any agent text that is late or fails verification. Player and club names stay
as written in the line-up; numbers are formatted by the caller.
"""

from __future__ import annotations

LANGS = ("en", "es", "ar")

# kind -> layer, template id, seconds on screen, priority (1 = most important)
PLACEMENT: dict[str, tuple[str, str, int, int]] = {
    "goal": ("fullscreen", "goal", 8, 1),
    "card": ("lower_third", "card", 7, 2),
    "big_chance": ("lower_third", "big_chance", 7, 2),
    "tactical_shift": ("stat_panel", "tactical_shift", 10, 2),
    "momentum_swing": ("ticker", "momentum", 8, 3),
    "control_chaos": ("ticker", "control_chaos", 8, 3),
    "shot_speed": ("lower_third", "shot_speed", 6, 3),
    "pass_quality": ("lower_third", "pass_quality", 6, 3),
    "substitution": ("lower_third", "substitution", 6, 3),
    "passing_move": ("ticker", "passing_move", 6, 4),
    "milestone": ("ticker", "milestone", 6, 4),
    "speed_burst": ("player_tag", "speed_burst", 4, 4),
    "distance_milestone": ("player_tag", "distance", 4, 5),
}

TEXT: dict[str, dict[str, tuple[str, str | None]]] = {
    # template -> lang -> (headline, body)
    "goal": {
        "en": ("GOAL! {team}", "{player} scores. {score}"),
        "es": ("¡GOL! {team}", "Marca {player}. {score}"),
        "ar": ("هدف! {team}", "يسجّل {player}. {score}"),
    },
    "card": {
        "en": ("{card_en} card", "{player}, {team}"),
        "es": ("Tarjeta {card_es}", "{player}, {team}"),
        "ar": ("بطاقة {card_ar}", "{player}، {team}"),
    },
    "big_chance": {
        "en": ("Big chance: {player}", "{xg} xG"),
        "es": ("Ocasión clara: {player}", "{xg} xG"),
        "ar": ("فرصة محققة: {player}", "{xg} أهداف متوقعة"),
    },
    "shot_speed": {
        "en": ("{player} strikes it at {speed} km/h", None),
        "es": ("{player} dispara a {speed} km/h", None),
        "ar": ("{player} يسدد بسرعة {speed} كم/س", None),
    },
    "pass_quality": {
        "en": ("{player}: a {difficulty}/100 pass", "{distance} m, completed"),
        "es": ("{player}: pase de dificultad {difficulty}/100", "{distance} m, completado"),
        "ar": ("{player}: تمريرة بصعوبة {difficulty}/100", "{distance} م، مكتملة"),
    },
    "substitution": {
        "en": ("Substitution, {team}", "On: {player_in} · Off: {player_out}"),
        "es": ("Cambio en {team}", "Entra: {player_in} · Sale: {player_out}"),
        "ar": ("تبديل في {team}", "يدخل: {player_in} · يخرج: {player_out}"),
    },
    "passing_move": {
        "en": ("{team}: {passes} passes in a row", None),
        "es": ("{team}: {passes} pases seguidos", None),
        "ar": ("{team}: {passes} تمريرة متتالية", None),
    },
    "milestone": {
        "en": ("{player} completes a 50th pass", None),
        "es": ("{player} completa su pase número 50", None),
        "ar": ("{player} يكمل تمريرته الخمسين", None),
    },
    "speed_burst": {
        "en": ("{player}", "{speed} km/h"),
        "es": ("{player}", "{speed} km/h"),
        "ar": ("{player}", "{speed} كم/س"),
    },
    "distance": {
        "en": ("{player}", "{km} km covered"),
        "es": ("{player}", "{km} km recorridos"),
        "ar": ("{player}", "{km} كم مقطوعة"),
    },
    "momentum": {
        "en": ("Momentum swings to {team}", None),
        "es": ("El impulso cambia a favor de {team}", None),
        "ar": ("الأفضلية تنتقل إلى {team}", None),
    },
    "control_chaos": {
        "en": ("The match tips into {state}", None),
        "es": ("El partido entra en {state}", None),
        "ar": ("المباراة تتحول إلى {state}", None),
    },
    "tactical_shift": {
        "en": ("{team}: {signal} {direction}", "{before} → {after} over the last {window} minutes"),
        "es": ("{team}: {signal} {direction}", "{before} → {after} en los últimos {window} minutos"),
        "ar": ("{team}: {signal} {direction}", "من {before} إلى {after} خلال آخر {window} دقائق"),
    },
}

WORDS: dict[str, dict[str, str]] = {
    "card_yellow": {"en": "Yellow", "es": "amarilla", "ar": "صفراء"},
    "card_red": {"en": "Red", "es": "roja", "ar": "حمراء"},
    "state_chaos": {"en": "chaos", "es": "el caos", "ar": "فوضى"},
    "state_control": {"en": "control", "es": "el control", "ar": "سيطرة"},
    "up": {"en": "up", "es": "al alza", "ar": "في ارتفاع"},
    "down": {"en": "down", "es": "a la baja", "ar": "في انخفاض"},
    # detector signals
    "press_rate": {"en": "pressing", "es": "presión", "ar": "الضغط"},
    "ppda": {"en": "passes allowed per defensive action", "es": "pases permitidos por acción defensiva",
             "ar": "التمريرات المسموحة لكل تدخل دفاعي"},
    "def_action_height_m": {"en": "height of defending", "es": "altura de la defensa",
                            "ar": "ارتفاع التدخلات الدفاعية"},
    "high_turnovers": {"en": "high turnovers", "es": "recuperaciones altas", "ar": "استخلاص الكرة في الأمام"},
    "line_height_m": {"en": "defensive line", "es": "línea defensiva", "ar": "خط الدفاع"},
    "block_height_m": {"en": "block height", "es": "altura del bloque", "ar": "ارتفاع الكتلة الدفاعية"},
    "block_height_adj_m": {"en": "block height", "es": "altura del bloque", "ar": "ارتفاع الكتلة الدفاعية"},
    "width_m": {"en": "width in possession", "es": "amplitud con balón", "ar": "العرض أثناء الاستحواذ"},
    "width_adj_m": {"en": "width in possession", "es": "amplitud con balón", "ar": "العرض أثناء الاستحواذ"},
    "long_pass_share": {"en": "long balls", "es": "balones largos", "ar": "الكرات الطويلة"},
    "forward_share": {"en": "forward passing", "es": "pase hacia delante", "ar": "التمرير إلى الأمام"},
    "time_on_ball_s": {"en": "time on the ball", "es": "tiempo con el balón", "ar": "مدة الاحتفاظ بالكرة"},
}


def word(key: str, lang: str) -> str:
    return WORDS.get(key, {}).get(lang) or WORDS.get(key, {}).get("en") or key


def render(template: str, lang: str, **values) -> tuple[str, str | None]:
    head, body = TEXT[template][lang]
    return head.format(**values), (body.format(**values) if body else None)
