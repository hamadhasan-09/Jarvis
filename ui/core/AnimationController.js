import { STATE_POSE } from "./visualSpec.js";
import { CORE_STATES } from "./states.js";
import { damp } from "./math.js";

const KEYS = [
  "scale",
  "glow",
  "saturation",
  "orbitSpeed",
  "ringSpeed",
  "particleSpeed",
  "filamentEnergy",
  "shellOpacity",
  "coreHeat",
  "audioToScale",
  "audioToGlow",
];

export class AnimationController {
  constructor() {
    this.pose = { ...STATE_POSE[CORE_STATES.IDLE] };
    this.target = { ...STATE_POSE[CORE_STATES.IDLE] };
  }

  setTargetState(stateName) {
    const next = STATE_POSE[stateName] || STATE_POSE[CORE_STATES.IDLE];
    this.target = next;
  }

  /**
   * Interpolates toward the target pose. lambda is derived from transitionMs.
   */
  step(dtSeconds) {
    const ms = this.target.transitionMs || 340;
    const lambda = 1000 / ms;
    for (const key of KEYS) {
      this.pose[key] = damp(this.pose[key], this.target[key], lambda, dtSeconds);
    }
    return this.pose;
  }
}
