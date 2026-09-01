import * as THREE from "three";

const vertexShader = `
  uniform float u_time;
  uniform float u_frequency;

  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying vec3 vPosition;

  // Lightweight trigonometric displacement
  float trigNoise(vec3 p) {
      float n = sin(p.x) * cos(p.y) * sin(p.z);
      n += 0.5 * sin(p.x * 2.1 + p.y) * cos(p.z * 1.9);
      n += 0.25 * sin(p.y * 4.3) * cos(p.x * 3.7 + p.z);
      return n / 1.75;
  }

  void main() {
      float speed = 1.0 + min(u_frequency, 40.0) * 0.08;
      float activeTime = u_time * (speed * 0.45 + 0.18);
      vec3 coord = position * 0.85 + vec3(0.0, activeTime, 0.0);
      float noise = trigNoise(coord);
      
      float freqDamped = min(u_frequency, 40.0);
      float displacement = (0.16 + (freqDamped / 40.0) * 0.72) * noise;
      vec3 newPosition = position + normal * displacement;
      
      vNormal = normalize(normalMatrix * normal);
      vec4 mvPosition = modelViewMatrix * vec4(newPosition, 1.0);
      vViewPosition = -mvPosition.xyz;
      vPosition = newPosition;
      
      gl_PointSize = 135.0 / -mvPosition.z;
      gl_Position = projectionMatrix * mvPosition;
  }
`;

const fragmentShader = `
  uniform float u_time;
  uniform float u_frequency;
  uniform vec3 u_colorCenter;
  uniform vec3 u_colorCrescent;
  uniform vec3 u_colorRim;

  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying vec3 vPosition;

  void main() {
      vec2 pc = gl_PointCoord - vec2(0.5);
      float dist = length(pc);
      if (dist > 0.5) discard;
      
      float particleGlow = smoothstep(0.5, 0.02, dist);

      vec3 normal = normalize(vNormal);
      vec3 viewDir = normalize(vViewPosition);
      
      float dotNV = dot(normal, viewDir);
      float fresnel = pow(1.0 - max(dotNV, 0.0), 2.5);
      
      float colorMix = smoothstep(-2.2, -0.5, vPosition.y + vPosition.x * 0.4);
      vec3 colGrid = mix(u_colorCrescent, u_colorCenter, colorMix);
      
      vec3 colRim = u_colorRim * fresnel * 4.5;
      vec3 finalColor = (colGrid * 0.75 + colRim) * particleGlow;
      
      float amp = 1.0 + u_frequency * 0.008;
      finalColor *= amp;
      
      float alpha = mix(0.4, 1.0, fresnel) * particleGlow;
      gl_FragColor = vec4(finalColor, alpha);
  }
`;

// --- State Variables ---
let currentState = "idle";
let remoteAudioFrequency = 0;

// --- WebGL Setup ---
const container = document.getElementById("canvas-container");
let W = window.innerWidth;
let H = window.innerHeight;

let mouseX = 0;
let mouseY = 0;

const renderer = new THREE.WebGLRenderer({
  antialias: false,
  alpha: true,
  powerPreference: "low-power",
});
renderer.setPixelRatio(1.0);
renderer.setSize(W, H);
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 1000);
camera.position.set(0, 0, 22);
camera.lookAt(0, 0, 0);

// --- Dynamic Color Configurations by State ---
const stateColors = {
  idle: {
    center: new THREE.Color(0.04, 0.15, 0.72),
    crescent: new THREE.Color(0.85, 0.0, 0.6),
    rim: new THREE.Color(0.0, 0.55, 1.0),
  },
  listening: {
    center: new THREE.Color(0.0, 0.5, 0.95),
    crescent: new THREE.Color(0.0, 0.85, 0.9),
    rim: new THREE.Color(0.0, 0.98, 1.0),
  },
  speaking: {
    center: new THREE.Color(0.75, 0.05, 0.5),
    crescent: new THREE.Color(0.98, 0.1, 0.55),
    rim: new THREE.Color(1.0, 0.4, 0.8),
  },
  thinking: {
    center: new THREE.Color(0.45, 0.05, 0.9),
    crescent: new THREE.Color(0.7, 0.1, 0.98),
    rim: new THREE.Color(0.88, 0.4, 1.0),
  },
};

const targetColors = {
  center: stateColors.idle.center.clone(),
  crescent: stateColors.idle.crescent.clone(),
  rim: stateColors.idle.rim.clone(),
};

const uniforms = {
  u_time: { value: 0.0 },
  u_frequency: { value: 0.0 },
  u_colorCenter: { value: stateColors.idle.center.clone() },
  u_colorCrescent: { value: stateColors.idle.crescent.clone() },
  u_colorRim: { value: stateColors.idle.rim.clone() },
};

const mat = new THREE.ShaderMaterial({
  uniforms,
  vertexShader: vertexShader,
  fragmentShader: fragmentShader,
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
});

// Clean, lightweight geometry (60x30 points) -> 0% CPU consumption
const geo = new THREE.SphereGeometry(3.5, 60, 30);
const mesh = new THREE.Points(geo, mat);
scene.add(mesh);

// --- Mouse Parallax ---
const onMouseMove = (e) => {
  const halfX = window.innerWidth / 2;
  const halfY = window.innerHeight / 2;
  mouseX = (e.clientX - halfX) / 150;
  mouseY = (e.clientY - halfY) / 150;
};
document.addEventListener("mousemove", onMouseMove, { passive: true });

const onResize = () => {
  W = window.innerWidth;
  H = window.innerHeight;
  camera.aspect = W / H;
  camera.updateProjectionMatrix();
  renderer.setSize(W, H);
};
window.addEventListener("resize", onResize, { passive: true });

// --- Instant State Transitions ---
const setVisualizerState = (state) => {
  const validStates = ["idle", "listening", "speaking", "thinking"];
  if (!validStates.includes(state)) return;

  currentState = state;

  if (stateColors[state]) {
    targetColors.center.copy(stateColors[state].center);
    targetColors.crescent.copy(stateColors[state].crescent);
    targetColors.rim.copy(stateColors[state].rim);
  }

  document.querySelectorAll(".bg-layer").forEach((layer) => {
    if (layer.classList.contains(`bg-${state}`)) {
      layer.classList.add("active");
    } else {
      layer.classList.remove("active");
    }
  });
};
window.setVisualizerState = setVisualizerState;

// --- WebSocket Bridge (Completely decoupled from audio loop) ---
const initWebSocketBridge = () => {
  const host = window.location.hostname || "127.0.0.1";
  const port = window.location.port || "8765";
  const wsUrl = `ws://${host}:${port}/ws`;

  let ws = null;
  try {
    ws = new WebSocket(wsUrl);
  } catch (err) {
    return;
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (!data) return;

      if (data.type === "state" && data.state) {
        setVisualizerState(data.state);
      } else if (data.type === "audio_frequency") {
        remoteAudioFrequency = data.frequency || 0;
      }
    } catch (e) {}
  };

  ws.onclose = () => {
    setTimeout(initWebSocketBridge, 2000);
  };
};

initWebSocketBridge();

// --- Interactive Barge-In (Tap / Click to Interrupt & Keyboard Hotkey) ---
const triggerBargeIn = () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    console.log("[Barge-In] Sending interruption request via WebSocket");
    ws.send(JSON.stringify({ type: "interrupt" }));
    setVisualizerState("listening");
  }
};

window.addEventListener("pointerdown", (e) => {
  // Clicking or tapping anywhere immediately interrupts speech
  triggerBargeIn();
});

window.addEventListener("keydown", (e) => {
  // Pressing Spacebar or Escape immediately interrupts speech
  if (e.code === "Space" || e.code === "Escape") {
    triggerBargeIn();
  }
});

let lastTime = performance.now();
let accumulatedTime = 0;

// --- Ultra-Fast Render Loop ---
const animate = () => {
  const now = performance.now();
  const delta = (now - lastTime) * 0.001;
  lastTime = now;

  let speedMultiplier = 1.0;
  let targetFrequency = 0;

  // Ultra-snappy color interpolation (0.22)
  uniforms.u_colorCenter.value.lerp(targetColors.center, 0.22);
  uniforms.u_colorCrescent.value.lerp(targetColors.crescent, 0.22);
  uniforms.u_colorRim.value.lerp(targetColors.rim, 0.22);

  if (currentState === "idle") {
    speedMultiplier = 0.55;
    accumulatedTime += delta * speedMultiplier;
    targetFrequency = 2.0 + Math.sin(accumulatedTime * 2.0) * 0.5;
  } else if (currentState === "thinking") {
    speedMultiplier = 2.4;
    accumulatedTime += delta * speedMultiplier;
    targetFrequency = 14.0 + Math.sin(accumulatedTime * 5.0) * 4.0;
  } else if (currentState === "speaking") {
    speedMultiplier = 1.6;
    accumulatedTime += delta * speedMultiplier;
    targetFrequency = Math.max(remoteAudioFrequency, 4.5 + Math.sin(accumulatedTime * 4.0) * 2.5);
    remoteAudioFrequency = Math.max(0, remoteAudioFrequency - delta * 30.0);
  } else if (currentState === "listening") {
    speedMultiplier = 1.4;
    accumulatedTime += delta * speedMultiplier;
    targetFrequency = 7.0 + Math.sin(accumulatedTime * 4.0) * 3.5;
  }

  // Snappy frequency response
  uniforms.u_frequency.value += (targetFrequency - uniforms.u_frequency.value) * 0.28;
  uniforms.u_time.value = accumulatedTime;

  camera.position.x += (mouseX - camera.position.x) * 0.05;
  camera.position.y += (-mouseY - camera.position.y) * 0.05;
  camera.lookAt(0, 0, 0);

  mesh.rotation.y += 0.003;
  mesh.rotation.x = Math.sin(accumulatedTime * 0.4) * 0.08;

  renderer.render(scene, camera);
  requestAnimationFrame(animate);
};

animate();
