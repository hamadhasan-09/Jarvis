import { COLORS } from "./visualSpec.js";
import { rgba } from "./math.js";

const VERTEX_SHADER = `#version 300 es
in vec2 a_position;
out vec2 v_uv;

void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_audio;
uniform float u_scale;
uniform float u_glow;
uniform float u_saturation;
uniform float u_heat;
uniform float u_shell;
uniform float u_filament;
uniform float u_statePulse;

in vec2 v_uv;
out vec4 outColor;

float hash(float n) {
  return fract(sin(n) * 43758.5453123);
}

float hash2(vec2 p) {
  return fract(
    sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123
  );
}

float softRing(float radius, float target, float width) {
  return exp(-abs(radius - target) / width);
}

vec3 paletteCyan() {
  return vec3(0.16, 0.78, 1.0);
}

vec3 paletteCyanHot() {
  return vec3(0.72, 0.95, 1.0);
}

vec3 paletteAmber() {
  return vec3(1.0, 0.56, 0.10);
}

vec3 paletteAmberHot() {
  return vec3(1.0, 0.90, 0.58);
}

void main() {
  vec2 uv = v_uv * 2.0 - 1.0;

  float aspect = u_resolution.x / max(u_resolution.y, 1.0);
  uv.x *= aspect;

  float t = u_time;

  float audio = clamp(u_audio, 0.0, 1.0);
  float statePulse = clamp(u_statePulse, 0.0, 1.0);

  float radius = length(uv);
  float angle = atan(uv.y, uv.x);

  float dynamicScale =
    1.0
    + (u_scale - 1.0)
    + audio * 0.075
    + statePulse * 0.018;

  float r = radius / max(dynamicScale, 0.2);

  vec3 color = vec3(0.003, 0.012, 0.022);

  /* ----------------------------------------------------------
     ATMOSPHERIC AURA
     ---------------------------------------------------------- */

  float aura =
    exp(-pow(r * 2.8, 2.0))
    * (0.24 + u_glow * 0.5);

  float outerAura =
    exp(-pow((r - 0.45) * 5.5, 2.0))
    * (0.12 + u_glow * 0.2);

  color += paletteCyan() * aura;
  color += paletteCyanHot() * outerAura * 0.55;

  /* ----------------------------------------------------------
     OUTER ENERGY SHELL
     ---------------------------------------------------------- */

  float shell =
    softRing(r, 0.34 + sin(t * 0.34) * 0.006, 0.024)
    * u_shell;

  float shellPulse =
    0.72
    + 0.28 * sin(
      angle * 7.0
      + t * 1.7
      + sin(angle * 3.0 - t * 0.7)
    );

  color += paletteCyanHot()
    * shell
    * shellPulse
    * (0.48 + u_glow * 0.6);

  /* ----------------------------------------------------------
     ORBITAL ENERGY RINGS
     ---------------------------------------------------------- */

  float orbitA =
    softRing(
      r,
      0.42 + 0.012 * sin(t * 0.55),
      0.0065
    );

  float orbitB =
    softRing(
      r,
      0.50 + 0.010 * sin(t * 0.41 + 2.0),
      0.005
    );

  float orbitC =
    softRing(
      r,
      0.58 + 0.008 * sin(t * 0.33 + 4.0),
      0.0045
    );

  float orbitPattern =
    0.52
    + 0.48 * sin(
      angle * 8.0
      + t * 1.2
    );

  color += paletteCyan()
    * (orbitA * 0.48 + orbitB * 0.36 + orbitC * 0.26)
    * (0.6 + orbitPattern * 0.4)
    * u_shell;

  /* ----------------------------------------------------------
     ANGULAR ENERGY FILAMENTS
     ---------------------------------------------------------- */

  float filamentPhase =
    angle * 11.0
    + t * (0.7 + u_filament * 0.7)
    + sin(angle * 4.0 + t * 0.35) * 2.1;

  float filament =
    pow(
      max(0.0, 0.5 + 0.5 * sin(filamentPhase)),
      12.0
    );

  filament *= smoothstep(0.64, 0.08, r);

  float filament2 =
    pow(
      max(
        0.0,
        0.5 + 0.5 * sin(
          angle * 17.0
          - t * 1.45
          + r * 22.0
        )
      ),
      18.0
    );

  filament2 *= smoothstep(0.58, 0.12, r);

  color += paletteAmber()
    * filament
    * u_filament
    * (0.35 + u_heat * 0.7);

  color += paletteCyanHot()
    * filament2
    * u_filament
    * 0.45;

  /* ----------------------------------------------------------
     INNER REACTOR CORE
     ---------------------------------------------------------- */

  float core =
    exp(-pow(r * 5.6, 2.0));

  float coreHot =
    exp(-pow(r * 12.5, 2.0));

  float coreBreath =
    0.90
    + 0.10 * sin(t * 2.6)
    + audio * 0.14;

  color += paletteAmber()
    * core
    * (0.38 + u_heat * 0.75)
    * coreBreath;

  color += paletteAmberHot()
    * coreHot
    * (0.65 + u_heat * 0.45);

  /* ----------------------------------------------------------
     INTERNAL ENERGY SWIRL
     ---------------------------------------------------------- */

  float swirl =
    0.5
    + 0.5 * sin(
      angle * 5.0
      + r * 34.0
      - t * 2.5
    );

  float swirlMask =
    exp(-pow((r - 0.16) * 9.0, 2.0));

  color += mix(
    paletteAmber(),
    paletteCyanHot(),
    swirl * 0.35
  )
  * swirlMask
  * 0.22
  * (0.5 + u_heat);

  /* ----------------------------------------------------------
     TRAVELING SONIC WAVES
     ---------------------------------------------------------- */

  for (int i = 0; i < 3; i++) {
    float fi = float(i);

    float phase =
      fract(
        t * (0.13 + audio * 0.13)
        + fi * 0.31
      );

    float waveRadius =
      0.36 + phase * 0.34;

    float wave =
      softRing(r, waveRadius, 0.008 + audio * 0.006)
      * (1.0 - phase)
      * (0.30 + audio * 0.85);

    float angular =
      0.72
      + 0.28 * sin(
        angle * (5.0 + fi * 2.0)
        + t * (1.0 + fi * 0.4)
      );

    color += mix(
      paletteCyan(),
      paletteAmberHot(),
      fi / 4.0
    ) * wave * angular;
  }

  /* ----------------------------------------------------------
     ORBITING LIGHT PARTICLES
     ---------------------------------------------------------- */

  for (int i = 0; i < 12; i++) {
    float fi = float(i);

    float seedA = hash(fi * 13.17 + 2.31);
    float seedB = hash(fi * 31.91 + 7.22);

    float particleAngle =
      seedA * 6.2831853
      + t * (0.12 + seedB * 0.16)
      * (mod(fi, 2.0) > 0.5 ? -1.0 : 1.0);

    float particleRadius =
      0.43
      + seedB * 0.24
      + sin(t * (0.4 + seedA) + fi) * 0.015;

    vec2 particlePos =
      vec2(
        cos(particleAngle),
        sin(particleAngle)
          * (0.70 + 0.15 * seedA)
      ) * particleRadius;

    float distanceToParticle =
      length(uv - particlePos);

    float particle =
      exp(
        -distanceToParticle
        * distanceToParticle
        * 1600.0
      );

    particle *=
      0.16
      + 0.44 * seedA
      + audio * 0.24;

    color +=
      mix(
        paletteCyanHot(),
        paletteAmberHot(),
        step(0.72, seedB)
      ) * particle;
  }

  /* ----------------------------------------------------------
     FLOOR / REFLECTION
     ---------------------------------------------------------- */

  float reflectionY = uv.y + 0.60;

  float reflection =
    exp(
      -abs(reflectionY) * 12.0
      -abs(uv.x) * 2.2
    );

  reflection *=
    smoothstep(0.80, 0.15, r);

  color +=
    paletteCyan()
    * reflection
    * 0.06
    * u_glow;

  color +=
    paletteAmber()
    * reflection
    * 0.04
    * u_heat;

  /* ----------------------------------------------------------
     EDGE CONTROL
     ---------------------------------------------------------- */

  float vignette =
    smoothstep(
      1.35,
      0.35,
      length(
        vec2(
          uv.x / max(aspect, 1.0),
          uv.y
        )
      )
    );

  color *=
    0.72
    + vignette * 0.28;

  /* Slight cinematic tone mapping. */
  color = color / (1.0 + color);

  color = pow(
    max(color, vec3(0.0)),
    vec3(0.92)
  );

  outColor = vec4(color, 1.0);
}
`;

function createShader(gl, type, source) {
  const shader = gl.createShader(type);

  if (!shader) {
    throw new Error("Unable to create WebGL shader.");
  }

  gl.shaderSource(shader, source);
  gl.compileShader(shader);

  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const info = gl.getShaderInfoLog(shader) || "Unknown shader error";
    gl.deleteShader(shader);
    throw new Error(`JARVIS shader compilation failed: ${info}`);
  }

  return shader;
}

function createProgram(gl) {
  const vertex = createShader(
    gl,
    gl.VERTEX_SHADER,
    VERTEX_SHADER,
  );

  const fragment = createShader(
    gl,
    gl.FRAGMENT_SHADER,
    FRAGMENT_SHADER,
  );

  const program = gl.createProgram();

  if (!program) {
    throw new Error("Unable to create JARVIS WebGL program.");
  }

  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);

  gl.deleteShader(vertex);
  gl.deleteShader(fragment);

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const info = gl.getProgramInfoLog(program) || "Unknown link error";
    gl.deleteProgram(program);
    throw new Error(`JARVIS WebGL program link failed: ${info}`);
  }

  return program;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export class CoreRenderer {
  constructor(canvas) {
    this.canvas = canvas;

    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: true,
      powerPreference: "high-performance",
      desynchronized: true,
    });

    if (!gl) {
      throw new Error(
        "JARVIS requires WebGL2. Please use a current Chrome or Edge browser.",
      );
    }

    this.gl = gl;
    this.program = createProgram(gl);

    this.positionLocation = gl.getAttribLocation(
      this.program,
      "a_position",
    );

    this.uniforms = Object.freeze({
      resolution: gl.getUniformLocation(
        this.program,
        "u_resolution",
      ),
      time: gl.getUniformLocation(
        this.program,
        "u_time",
      ),
      audio: gl.getUniformLocation(
        this.program,
        "u_audio",
      ),
      scale: gl.getUniformLocation(
        this.program,
        "u_scale",
      ),
      glow: gl.getUniformLocation(
        this.program,
        "u_glow",
      ),
      saturation: gl.getUniformLocation(
        this.program,
        "u_saturation",
      ),
      heat: gl.getUniformLocation(
        this.program,
        "u_heat",
      ),
      shell: gl.getUniformLocation(
        this.program,
        "u_shell",
      ),
      filament: gl.getUniformLocation(
        this.program,
        "u_filament",
      ),
      statePulse: gl.getUniformLocation(
        this.program,
        "u_statePulse",
      ),
    });

    this.buffer = gl.createBuffer();

    if (!this.buffer) {
      throw new Error("Unable to create JARVIS WebGL buffer.");
    }

    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);

    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([
        -1, -1,
         1, -1,
        -1,  1,

        -1,  1,
         1, -1,
         1,  1,
      ]),
      gl.STATIC_DRAW,
    );

    gl.useProgram(this.program);

    gl.enableVertexAttribArray(this.positionLocation);

    gl.vertexAttribPointer(
      this.positionLocation,
      2,
      gl.FLOAT,
      false,
      0,
      0,
    );

    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);

    this.width = 1;
    this.height = 1;
    this.dpr = 1;
  }

  resize(cssWidth, cssHeight, dpr) {
    this.width = Math.max(1, cssWidth);
    this.height = Math.max(1, cssHeight);

    this.dpr = Math.min(
      Math.max(1, dpr || 1),
      1.75,
    );

    this.canvas.width = Math.floor(
      this.width * this.dpr,
    );

    this.canvas.height = Math.floor(
      this.height * this.dpr,
    );

    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;

    this.gl.viewport(
      0,
      0,
      this.canvas.width,
      this.canvas.height,
    );
  }

  draw(timeSeconds, pose, audio) {
    const gl = this.gl;

    const actualAudio =
      audio && (audio.live || audio.external)
        ? clamp(Number(audio.rms) || 0, 0, 1)
        : 0;

    /*
     * Browser SpeechSynthesis does not expose PCM audio.
     * Therefore the visual core uses a small procedural voice envelope
     * during speaking/listening states until a streaming TTS audio source
     * is connected.
     *
     * Real microphone analyser data always takes priority.
     */
    const proceduralPulse =
      pose && pose.audioToScale > 0
        ? (
            0.18
            + 0.12 * (
              0.5
              + 0.5 * Math.sin(timeSeconds * 11.0)
            )
            + 0.08 * (
              0.5
              + 0.5 * Math.sin(timeSeconds * 23.0 + 1.4)
            )
          )
        : 0;

    const audioLevel = Math.max(
      actualAudio,
      proceduralPulse,
    );

    const statePulse = clamp(
      pose && pose.audioToScale
        ? pose.audioToScale * 18
        : 0,
      0,
      1,
    );

    gl.useProgram(this.program);

    gl.uniform2f(
      this.uniforms.resolution,
      this.canvas.width,
      this.canvas.height,
    );

    gl.uniform1f(
      this.uniforms.time,
      timeSeconds,
    );

    gl.uniform1f(
      this.uniforms.audio,
      audioLevel,
    );

    gl.uniform1f(
      this.uniforms.scale,
      Number(pose?.scale) || 1,
    );

    gl.uniform1f(
      this.uniforms.glow,
      Number(pose?.glow) || 0.4,
    );

    gl.uniform1f(
      this.uniforms.saturation,
      Number(pose?.saturation) || 1,
    );

    gl.uniform1f(
      this.uniforms.heat,
      Number(pose?.coreHeat) || 0.7,
    );

    gl.uniform1f(
      this.uniforms.shell,
      Number(pose?.shellOpacity) || 1,
    );

    gl.uniform1f(
      this.uniforms.filament,
      Number(pose?.filamentEnergy) || 0.4,
    );

    gl.uniform1f(
      this.uniforms.statePulse,
      statePulse,
    );

    gl.bindBuffer(
      gl.ARRAY_BUFFER,
      this.buffer,
    );

    gl.vertexAttribPointer(
      this.positionLocation,
      2,
      gl.FLOAT,
      false,
      0,
      0,
    );

    gl.drawArrays(
      gl.TRIANGLES,
      0,
      6,
    );
  }

  destroy() {
    const gl = this.gl;

    if (this.buffer) {
      gl.deleteBuffer(this.buffer);
      this.buffer = null;
    }

    if (this.program) {
      gl.deleteProgram(this.program);
      this.program = null;
    }
  }
}