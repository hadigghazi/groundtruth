// A crest drawn from a fictional club's colours: a shield, a diagonal sash and its initials.

export function Crest({ colors, code, size = 28 }: { colors: string[]; code: string; size?: number }) {
  const [a, b] = [colors[0] ?? "#888", colors[1] ?? "#fff"];
  const id = `cr-${code}-${a.slice(1)}`;
  return (
    <svg className="crest" width={size} height={size * 1.15} viewBox="0 0 40 46" aria-hidden="true">
      <defs>
        <clipPath id={id}><path d="M20 1 L38 6 V22 C38 34 30 41 20 45 C10 41 2 34 2 22 V6 Z" /></clipPath>
      </defs>
      <g clipPath={`url(#${id})`}>
        <rect width="40" height="46" fill={a} />
        <path d="M-4 30 L44 6 L44 18 L-4 42 Z" fill={b} opacity="0.92" />
      </g>
      <path d="M20 1 L38 6 V22 C38 34 30 41 20 45 C10 41 2 34 2 22 V6 Z" fill="none" stroke="rgba(255,255,255,.55)" strokeWidth="1.5" />
      <text x="20" y="27" textAnchor="middle" className="crest-code" fill="#fff" stroke="rgba(0,0,0,.45)" strokeWidth="2.4" paintOrder="stroke">
        {code.slice(0, 3).toUpperCase()}
      </text>
    </svg>
  );
}
