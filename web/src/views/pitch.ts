// Pitch geometry shared by both views (metres, the same fixed frame as the engine).

export const L = 105, W = 68;

export type Seg = [number, number, number, number];
export type Arc = { cx: number; cy: number; r: number; a0: number; a1: number };

/** Every pitch marking, as straight segments and arcs. */
export function markings(): { segs: Seg[]; arcs: Arc[]; spots: [number, number][] } {
  const segs: Seg[] = [
    [0, 0, L, 0], [L, 0, L, W], [L, W, 0, W], [0, W, 0, 0], [L / 2, 0, L / 2, W],
  ];
  for (const side of [0, 1]) {
    const x0 = side === 0 ? 0 : L, s = side === 0 ? 1 : -1;
    const box = (d: number, hw: number) => {
      segs.push([x0, W / 2 - hw, x0 + s * d, W / 2 - hw], [x0 + s * d, W / 2 - hw, x0 + s * d, W / 2 + hw], [x0 + s * d, W / 2 + hw, x0, W / 2 + hw]);
    };
    box(16.5, 20.16);
    box(5.5, 9.16);
  }
  const arcs: Arc[] = [{ cx: L / 2, cy: W / 2, r: 9.15, a0: 0, a1: Math.PI * 2 }];
  const a = Math.acos(5.5 / 9.15); // the D: the part of the penalty arc outside the box
  arcs.push({ cx: 11, cy: W / 2, r: 9.15, a0: -a, a1: a }, { cx: L - 11, cy: W / 2, r: 9.15, a0: Math.PI - a, a1: Math.PI + a });
  for (const [cx, cy, a0] of [[0, 0, 0], [L, 0, Math.PI / 2], [L, W, Math.PI], [0, W, -Math.PI / 2]] as const) {
    arcs.push({ cx, cy, r: 1, a0, a1: a0 + Math.PI / 2 });
  }
  return { segs, arcs, spots: [[11, W / 2], [L - 11, W / 2], [L / 2, W / 2]] };
}
