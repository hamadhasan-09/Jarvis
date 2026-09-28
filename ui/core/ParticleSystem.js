import { LAYOUT } from "./visualSpec.js";
import { clamp } from "./math.js";

export class ParticleSystem {
  constructor() {
    this.dust = [];
    this.sparks = [];
    this.#reset();
  }

  #reset() {
    this.dust = Array.from({ length: LAYOUT.particleCount }, (_, index) => ({
      seed: index * 17.13,
      radius: 0.55 + ((index * 0.137) % 0.85),
      angle: (index * 2.399) % (Math.PI * 2),
      tilt: 0.42 + ((index * 0.051) % 0.38),
      size: 0.4 + (index % 5) * 0.18,
      depth: index % 3,
    }));
    this.sparks = Array.from({ length: 36 }, (_, index) => ({
      angle: (index / 36) * Math.PI * 2,
      radius: 0.12 + (index % 9) * 0.018,
      speed: 0.35 + (index % 4) * 0.08,
    }));
  }

  update(timeSeconds, pose, audioRms) {
    const spin = pose.particleSpeed * (0.7 + audioRms * 0.9);
    for (const particle of this.dust) {
      particle.angle += spin * (0.004 + (particle.seed % 1) * 0.003);
    }
    for (const spark of this.sparks) {
      spark.angle += pose.filamentEnergy * spark.speed * 0.01;
      spark.radius = clamp(spark.radius + Math.sin(timeSeconds * 0.7 + spark.angle) * 0.0004, 0.08, 0.34);
    }
    return { dust: this.dust, sparks: this.sparks };
  }
}
