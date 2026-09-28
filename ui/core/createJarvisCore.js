import { LAYOUT } from "./visualSpec.js";
import { AnimationController } from "./AnimationController.js";
import { AudioReactiveController } from "./AudioReactiveController.js";
import { CoreRenderer } from "./CoreRenderer.js";
import { OrbitalSystem } from "./OrbitalSystem.js";
import { ParticleSystem } from "./ParticleSystem.js";
import { StateController } from "./StateController.js";

/**
 * Public HUD-facing API. Conversation, Socket.IO, and speech stay outside.
 */
export function createJarvisCore(canvas) {
  const renderer = new CoreRenderer(canvas);
  const state = new StateController();
  const animation = new AnimationController();
  const audio = new AudioReactiveController();
  const orbits = new OrbitalSystem();
  const particles = new ParticleSystem();
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let running = false;
  let raf = 0;
  let last = 0;
  animation.setTargetState(state.getState());
  state.onChange((name) => animation.setTargetState(name));

  const resize = () => {
    renderer.resize(window.innerWidth, window.innerHeight, Math.min(window.devicePixelRatio || 1, LAYOUT.maxDevicePixelRatio));
  };

  const frame = (now) => {
    if (!running) return;
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
    last = now;
    const pose = animation.step(dt);
    const levels = audio.sample(dt);
    const timeSeconds = now / 1000;
    const orbitState = orbits.update(reducedMotion ? 0 : dt, pose);
    const particleState = particles.update(timeSeconds, pose, levels.live || levels.external ? levels.rms : 0);
    renderer.draw(timeSeconds, pose, levels, orbitState, particleState, reducedMotion);
    raf = window.requestAnimationFrame(frame);
  };

  const start = () => {
    if (running) return;
    running = true;
    last = 0;
    raf = window.requestAnimationFrame(frame);
  };

  const stop = () => {
    running = false;
    window.cancelAnimationFrame(raf);
  };

  resize();
  start();
  window.addEventListener("resize", resize, { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });

  return {
    setBackendState(name) { state.setBackendState(name); },
    setUserSpeaking(active) { state.setUserSpeaking(active); },
    setJarvisSpeaking(active) { state.setJarvisSpeaking(active); },
    notifyInterrupted() { state.notifyInterrupted(); },
    attachMicrophone(stream) { return audio.attachStream(stream); },
    setTtsEnergy(rms) { audio.setExternalLevels(rms); },
    getState() { return state.getState(); },
    destroy() {
      stop();
      audio.detach();
      window.removeEventListener("resize", resize);
    },
  };
}
