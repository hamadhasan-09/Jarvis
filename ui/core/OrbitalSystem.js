import { LAYOUT } from "./visualSpec.js";

export class OrbitalSystem {
  constructor() {
    this.rings = [
      { rx: 1.02, ry: 0.34, yaw: 0.18, roll: 0.22, cyan: true, speed: 1 },
      { rx: 0.92, ry: 0.28, yaw: -0.42, roll: 0.55, cyan: true, speed: -0.85 },
      { rx: 0.78, ry: 0.22, yaw: 0.9, roll: -0.2, cyan: false, speed: 1.15 },
      { rx: 1.12, ry: 0.18, yaw: 0.05, roll: 1.15, cyan: true, speed: 0.55 },
      { rx: 0.64, ry: 0.64, yaw: 0, roll: 0, cyan: true, speed: 0.2, circular: true },
    ];
    this.beads = Array.from({ length: LAYOUT.orbitBeadCount }, (_, index) => ({
      ring: index % 3,
      t: index / LAYOUT.orbitBeadCount,
      warm: index % 2 === 0,
    }));
    this.shell = Array.from({ length: LAYOUT.cyanRingCount }, (_, index) => ({
      radius: 0.72 + index * 0.045,
      flatten: 0.86 + (index % 3) * 0.04,
      phase: index * 0.37,
    }));
  }

  update(dtSeconds, pose) {
    for (const ring of this.rings) {
      ring.yaw += dtSeconds * pose.orbitSpeed * ring.speed * 0.35;
      ring.roll += dtSeconds * pose.ringSpeed * 0.12 * (ring.cyan ? 1 : -1);
    }
    for (const bead of this.beads) {
      const ring = this.rings[bead.ring];
      bead.t = (bead.t + dtSeconds * pose.orbitSpeed * 0.08 * (ring.speed > 0 ? 1 : -1) + 1) % 1;
    }
    return { rings: this.rings, beads: this.beads, shell: this.shell };
  }
}
