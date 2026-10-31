// Shared Three.js helpers for Vidhur scenes. Deterministic: everything is driven by time t.
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.181.2/+esm";
export { THREE };
export const TEAL = 0x14b8a6, AMBER = 0xf59e0b, NAVY = 0x0b1220, RED = 0xef4444, GREEN = 0x22c55e;

export function setup(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setSize(1920, 1080, false); renderer.setPixelRatio(1); renderer.setClearColor(NAVY);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(NAVY, 14, 30);
  const camera = new THREE.PerspectiveCamera(35, 16 / 9, 0.1, 100);
  scene.add(new THREE.HemisphereLight(0x9fdcff, 0x0b1220, 1.4));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(4, 6, 6); scene.add(key);
  const rim = new THREE.PointLight(TEAL, 30, 20); rim.position.set(-4, 2, -3); scene.add(rim);
  // soft floor glow disc
  const floor = new THREE.Mesh(new THREE.CircleGeometry(6, 64),
    new THREE.MeshBasicMaterial({ map: radialTex("rgba(20,184,166,0.28)"), transparent: true, depthWrite: false }));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -1.6; scene.add(floor);
  return { renderer, scene, camera, floor };
}

export function radialTex(color) {
  const c = document.createElement("canvas"); c.width = c.height = 256;
  const g = c.getContext("2d"), gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  gr.addColorStop(0, color); gr.addColorStop(1, "rgba(0,0,0,0)"); g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

export function textTex(text, { color = "#ffffff", size = 120, weight = 700, w = 1024, h = 256, glow = null } = {}) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d"); g.font = `${weight} ${size}px Inter, Arial, sans-serif`;
  g.textAlign = "center"; g.textBaseline = "middle";
  if (glow) { g.shadowColor = glow; g.shadowBlur = 30; }
  g.fillStyle = color; g.fillText(text, w / 2, h / 2);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t;
}

export function textPlane(text, width, opts = {}) {
  const w = opts.w || 1024, h = opts.h || 256;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(width, width * h / w),
    new THREE.MeshBasicMaterial({ map: textTex(text, opts), transparent: true, depthWrite: false, side: THREE.DoubleSide }));
  return m;
}

// Zetaris database cylinder with glowing rings and label band
export function zetarisCylinder() {
  const g = new THREE.Group();
  const body = new THREE.MeshStandardMaterial({ color: 0x12324a, metalness: 0.55, roughness: 0.32, emissive: 0x06202a });
  for (let i = 0; i < 3; i++) {
    const disk = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 0.62, 96), body);
    disk.position.y = -0.72 + i * 0.72; g.add(disk);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(1.002, 1.002, 0.04, 96),
      new THREE.MeshStandardMaterial({ color: 0x1b4a63, metalness: 0.6, roughness: 0.25 }));
    cap.position.y = disk.position.y + 0.31; g.add(cap);
  }
  g.rings = [];
  for (let i = 0; i < 3; i++) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(1.22, 0.025, 16, 128),
      new THREE.MeshBasicMaterial({ color: TEAL, transparent: true, opacity: 0.9 }));
    ring.rotation.x = Math.PI / 2; ring.position.y = -0.95 + i * 0.95; g.add(ring); g.rings.push(ring);
  }
  // label band wrapping the middle disk
  const c = document.createElement("canvas"); c.width = 2048; c.height = 128;
  const x = c.getContext("2d"); x.fillStyle = "rgba(0,0,0,0)"; x.fillRect(0, 0, 2048, 128);
  x.font = "700 78px Inter, Arial"; x.fillStyle = "#5eead4"; x.textBaseline = "middle"; x.shadowColor = "#14B8A6"; x.shadowBlur = 18;
  for (let k = 0; k < 3; k++) x.fillText("ZETARIS", 90 + k * 682, 66);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const band = new THREE.Mesh(new THREE.CylinderGeometry(1.012, 1.012, 0.3, 96, 1, true),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
  g.add(band); g.band = band;
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: radialTex("rgba(20,184,166,0.35)"), depthWrite: false }));
  halo.scale.set(5, 5, 1); halo.position.z = -1.2; g.add(halo);
  return g;
}

export function animateCylinder(g, t) {
  g.rotation.y = t * 0.35;
  g.rings.forEach((r, i) => { const s = 1 + 0.03 * Math.sin(t * 2 + i * 1.7); r.scale.set(s, s, s); r.material.opacity = 0.65 + 0.3 * Math.sin(t * 2.2 + i); });
}

export function shield(color = TEAL) {
  const s = new THREE.Shape();
  s.moveTo(0, 1); s.bezierCurveTo(0.45, 0.85, 0.75, 0.88, 0.8, 0.8); s.lineTo(0.8, 0.1);
  s.bezierCurveTo(0.8, -0.45, 0.35, -0.8, 0, -1); s.bezierCurveTo(-0.35, -0.8, -0.8, -0.45, -0.8, 0.1);
  s.lineTo(-0.8, 0.8); s.bezierCurveTo(-0.75, 0.88, -0.45, 0.85, 0, 1);
  const geo = new THREE.ExtrudeGeometry(s, { depth: 0.18, bevelEnabled: true, bevelThickness: 0.05, bevelSize: 0.05, bevelSegments: 4 });
  geo.center();
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x10403f, metalness: 0.6, roughness: 0.3, emissive: color, emissiveIntensity: 0.25 }));
  const chk = new THREE.Shape(); chk.moveTo(-0.35, 0.02); chk.lineTo(-0.1, -0.25); chk.lineTo(0.38, 0.3); chk.lineTo(0.3, 0.38); chk.lineTo(-0.1, -0.08); chk.lineTo(-0.27, 0.1);
  const ck = new THREE.Mesh(new THREE.ExtrudeGeometry(chk, { depth: 0.08, bevelEnabled: false }), new THREE.MeshBasicMaterial({ color }));
  ck.position.z = 0.15; m.add(ck);
  return m;
}

export function recordStack() {
  const g = new THREE.Group();
  for (let i = 0; i < 7; i++) {
    const sheet = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.06, 0.95),
      new THREE.MeshStandardMaterial({ color: i % 2 ? 0xdbe7f3 : 0xc8d6e5, roughness: 0.6, emissive: 0x0a2030 }));
    sheet.position.set(Math.sin(i * 2.1) * 0.05, -0.4 + i * 0.12, Math.cos(i * 1.3) * 0.04);
    sheet.rotation.y = Math.sin(i * 1.9) * 0.12; g.add(sheet);
    const line = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.065, 0.06), new THREE.MeshBasicMaterial({ color: TEAL }));
    line.position.copy(sheet.position); line.position.z += 0.2; line.rotation.y = sheet.rotation.y; g.add(line);
  }
  return g;
}

export function coinGlobe() {
  const g = new THREE.Group();
  const coin = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.75, 0.12, 96),
    new THREE.MeshStandardMaterial({ color: 0xf5c451, metalness: 0.85, roughness: 0.25, emissive: 0x3a2a00 }));
  coin.rotation.x = Math.PI / 2; g.add(coin);
  const globe = new THREE.Mesh(new THREE.SphereGeometry(0.95, 24, 16),
    new THREE.MeshBasicMaterial({ color: TEAL, wireframe: true, transparent: true, opacity: 0.35 }));
  g.add(globe); g.globe = globe;
  const sym = textPlane("€ £ $", 1.2, { color: "#3a2a00", size: 150 }); sym.position.z = 0.07; g.add(sym);
  const sym2 = sym.clone(); sym2.position.z = -0.07; sym2.rotation.y = Math.PI; g.add(sym2);
  return g;
}

export const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - Math.pow(1 - x, 3));
export const clamp01 = (x) => Math.max(0, Math.min(1, x));
