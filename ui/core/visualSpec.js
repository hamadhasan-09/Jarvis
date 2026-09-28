/**
 * JARVIS core visual specification.
 * Derived from analysis of ui/assets/jarvis-reference-core.png (composition only).
 * This file is the design target. Do not restyle the HUD from a chat description.
 */

export const COLORS = Object.freeze({
  space: [1, 7, 13],
  spaceDeep: [0, 4, 10],
  cyan: [62, 199, 255],
  cyanSoft: [123, 223, 255],
  cyanHot: [184, 240, 255],
  amber: [255, 154, 46],
  amberSoft: [255, 193, 108],
  amberHot: [255, 231, 168],
  error: [255, 155, 155],
});

export const LAYOUT = Object.freeze({
  coreYFraction: 0.48,
  coreRadiusFraction: 0.236,
  floorOffsetFraction: 0.34,
  maxDevicePixelRatio: 2,
  particleCount: 140,
  orbitBeadCount: 5,
  filamentCount: 11,
  cyanRingCount: 7,
});

export const EASING = Object.freeze({
  pose: "cubic-bezier(0.2, 0.8, 0.2, 1)",
  interruptMs: 120,
  defaultMs: 340,
  thinkingMs: 420,
  errorMs: 280,
});

/**
 * Deliberate pose per core state. Scale is relative to idle radius.
 * Audio fields are multipliers applied only when live analyser data exists.
 */
export const STATE_POSE = Object.freeze({
  IDLE: {
    scale: 1,
    glow: 0.38,
    saturation: 1,
    orbitSpeed: 0.22,
    ringSpeed: 0.16,
    particleSpeed: 0.12,
    filamentEnergy: 0.35,
    shellOpacity: 0.92,
    coreHeat: 0.72,
    audioToScale: 0,
    audioToGlow: 0.08,
    transitionMs: 340,
  },
  LISTENING: {
    scale: 0.986,
    glow: 0.44,
    saturation: 1.02,
    orbitSpeed: 0.28,
    ringSpeed: 0.2,
    particleSpeed: 0.16,
    filamentEnergy: 0.4,
    shellOpacity: 1,
    coreHeat: 0.68,
    audioToScale: 0.012,
    audioToGlow: 0.18,
    transitionMs: 280,
  },
  USER_SPEAKING: {
    scale: 0.972,
    glow: 0.5,
    saturation: 1.04,
    orbitSpeed: 0.34,
    ringSpeed: 0.26,
    particleSpeed: 0.28,
    filamentEnergy: 0.48,
    shellOpacity: 1,
    coreHeat: 0.7,
    audioToScale: 0.045,
    audioToGlow: 0.42,
    transitionMs: 160,
  },
  THINKING: {
    scale: 1.012,
    glow: 0.48,
    saturation: 1.05,
    orbitSpeed: 0.12,
    ringSpeed: 0.48,
    particleSpeed: 0.22,
    filamentEnergy: 0.7,
    shellOpacity: 0.96,
    coreHeat: 0.82,
    audioToScale: 0,
    audioToGlow: 0.06,
    transitionMs: 420,
  },
  JARVIS_SPEAKING: {
    scale: 1.038,
    glow: 0.62,
    saturation: 1.1,
    orbitSpeed: 0.4,
    ringSpeed: 0.32,
    particleSpeed: 0.3,
    filamentEnergy: 0.78,
    shellOpacity: 1,
    coreHeat: 0.92,
    audioToScale: 0.03,
    audioToGlow: 0.35,
    transitionMs: 240,
  },
  INTERRUPTED: {
    scale: 1,
    glow: 0.28,
    saturation: 0.86,
    orbitSpeed: 0.08,
    ringSpeed: 0.1,
    particleSpeed: 0.08,
    filamentEnergy: 0.2,
    shellOpacity: 0.78,
    coreHeat: 0.5,
    audioToScale: 0,
    audioToGlow: 0,
    transitionMs: 120,
  },
  ERROR: {
    scale: 0.98,
    glow: 0.22,
    saturation: 0.72,
    orbitSpeed: 0.07,
    ringSpeed: 0.05,
    particleSpeed: 0.05,
    filamentEnergy: 0.18,
    shellOpacity: 0.7,
    coreHeat: 0.35,
    audioToScale: 0,
    audioToGlow: 0,
    transitionMs: 280,
  },
});

export const MOTION_RULES = Object.freeze({
  avoid: [
    "constant large pulsing",
    "random bounce",
    "excessive spin",
    "neon flash",
    "heavy blur",
    "dashboard loaders",
  ],
  character: "controlled, mechanical, restrained, cinematic",
});
