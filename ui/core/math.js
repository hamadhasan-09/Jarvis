export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function lerp(from, to, amount) {
  return from + (to - from) * amount;
}

export function damp(current, target, lambda, dtSeconds) {
  return lerp(current, target, 1 - Math.exp(-lambda * dtSeconds));
}

export function rgba(rgb, alpha) {
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${clamp(alpha, 0, 1)})`;
}
