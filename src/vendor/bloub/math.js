const TAU = Math.PI * 2;
const clamp = (v, lo = 0, hi = 1) => v < lo ? lo : v > hi ? hi : v;
const lerp = (a, b, t) => a + (b - a) * t;
const easings = {
  easeOutCubic: (t) => 1 - (1 - t) ** 3,
  easeInOutCubic: (t) => t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2,
  easeOutQuint: (t) => 1 - (1 - t) ** 5
};
function loopNoise(t, period, seed = 0) {
  const p = t / period * TAU;
  return 0.55 * Math.sin(p + seed) + 0.3 * Math.sin(2 * p + seed * 1.7 + 1.1) + 0.15 * Math.sin(3 * p + seed * 2.3 + 2.4);
}
function createRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = a + 1831565813 >>> 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
const r2 = (v) => Math.round(v * 100) / 100;
export {
  TAU,
  clamp,
  createRng,
  easings,
  lerp,
  loopNoise,
  r2
};
