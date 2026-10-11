import type { Lang } from "./api";

const S = {
  en: {
    title: "Pitchside", tagline: "Live match intelligence you can check",
    newMatch: "Start a match", home: "Home", away: "Away", seed: "Seed", speed: "Speed", hidden: "Hidden tactical changes",
    start: "Kick off", join: "Watch", live: "Live", fullTime: "Full time", delay: "on a {s}s broadcast delay",
    broadcast: "Broadcast", tactical: "Tactical", twoUp: "Side by side", viewer: "Viewer",
    analyst: "Analyst", casual: "Casual fan", language: "Language", club: "Club", anyClub: "Neutral", player: "Follow a player", noPlayer: "No one",
    trace: "Agent trace", consoleTab: "Director", stats: "Stats",
    console: "Change a team's tactics, live", consoleNote: "The system is never told. Watch whether it notices, and what it says.",
    revealed: "Answer key", reveal: "Show the hidden changes", revealLater: "Opens at full time",
    evidence: "Evidence", verified: "Verified", fallback: "Template", events: "Events behind this overlay", close: "Close",
    possession: "Possession", passAccuracy: "Pass accuracy", shots: "Shots", xg: "xG",
    noTrace: "Nothing flagged yet. Tactical shifts appear here as the agents work on them.",
    labels: { high_press: "Press high", low_block: "Sit deep", go_direct: "Go direct", slow_down: "Slow it down", push_wide: "Go wide" },
  },
  es: {
    title: "Pitchside", tagline: "Inteligencia de partido en directo que puedes comprobar",
    newMatch: "Empezar un partido", home: "Local", away: "Visitante", seed: "Semilla", speed: "Velocidad", hidden: "Cambios tácticos ocultos",
    start: "Comenzar", join: "Ver", live: "En directo", fullTime: "Final", delay: "con {s} s de retardo",
    broadcast: "Retransmisión", tactical: "Táctica", twoUp: "Lado a lado", viewer: "Espectador",
    analyst: "Analista", casual: "Aficionado", language: "Idioma", club: "Club", anyClub: "Neutral", player: "Seguir a un jugador", noPlayer: "Nadie",
    trace: "Agentes", consoleTab: "Director", stats: "Datos",
    console: "Cambia la táctica de un equipo, en directo", consoleNote: "Al sistema no se le avisa. Mira si lo detecta y qué dice.",
    revealed: "Solución", reveal: "Mostrar los cambios ocultos", revealLater: "Disponible al final",
    evidence: "Pruebas", verified: "Verificado", fallback: "Plantilla", events: "Eventos detrás de este gráfico", close: "Cerrar",
    possession: "Posesión", passAccuracy: "Precisión de pase", shots: "Tiros", xg: "xG",
    noTrace: "Nada todavía. Los cambios tácticos aparecen aquí mientras los agentes trabajan.",
    labels: { high_press: "Presionar arriba", low_block: "Replegarse", go_direct: "Juego directo", slow_down: "Bajar el ritmo", push_wide: "Abrir el campo" },
  },
  ar: {
    title: "Pitchside", tagline: "تحليل مباشر للمباراة يمكنك التحقق منه",
    newMatch: "ابدأ مباراة", home: "صاحب الأرض", away: "الضيف", seed: "البذرة", speed: "السرعة", hidden: "تغييرات تكتيكية خفية",
    start: "انطلاق", join: "شاهد", live: "مباشر", fullTime: "نهاية المباراة", delay: "بتأخير بث {s} ثانية",
    broadcast: "البث", tactical: "تكتيكي", twoUp: "جنبًا إلى جنب", viewer: "المشاهد",
    analyst: "محلل", casual: "مشجع", language: "اللغة", club: "النادي", anyClub: "محايد", player: "تتبّع لاعبًا", noPlayer: "لا أحد",
    trace: "عمل الوكلاء", consoleTab: "المخرج", stats: "الأرقام",
    console: "غيّر تكتيك فريق مباشرةً", consoleNote: "لا يُبلَّغ النظام بذلك. راقب هل يلاحظ التغيير وماذا يقول.",
    revealed: "الإجابة الصحيحة", reveal: "اعرض التغييرات الخفية", revealLater: "تُكشف في نهاية المباراة",
    evidence: "الأدلة", verified: "تم التحقق", fallback: "قالب", events: "الأحداث وراء هذه الرسوم", close: "إغلاق",
    possession: "الاستحواذ", passAccuracy: "دقة التمرير", shots: "التسديدات", xg: "الأهداف المتوقعة",
    noTrace: "لا شيء بعد. تظهر التحولات التكتيكية هنا أثناء عمل الوكلاء.",
    labels: { high_press: "ضغط عالٍ", low_block: "تكتل دفاعي", go_direct: "لعب مباشر", slow_down: "تهدئة اللعب", push_wide: "توسيع اللعب" },
  },
} as const;

export type Strings = typeof S.en;
export const strings = (lang: Lang): Strings => S[lang] as unknown as Strings;
export const dir = (lang: Lang) => (lang === "ar" ? "rtl" : "ltr");
export const LANG_LABEL: Record<Lang, string> = { en: "English", es: "Español", ar: "العربية" };

export function clock(ms: number, period: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60), s = total % 60;
  const base = period === 1 ? 45 : 90;
  if (m >= base) return `${base}+${m - base + 1}'`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
