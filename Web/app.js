// PoSlap in the browser: the match engine (sim.js) steps MuJoCo at 200 Hz in real time; three.js draws every
// physics shape where MuJoCo puts it (drawing only, as Unreal does). Served from the repo root: Web/serve.py.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import loadMujoco from "@mujoco/mujoco";
import * as ort from "onnxruntime-web";
import { loadMatch, MATCHUPS } from "./loader.js";
import { loadArena, loadAthlete } from "./art.js";

ort.env.wasm.wasmPaths = new URL("./node_modules/onnxruntime-web/dist/", location.href).href;   // relative paths resolve against ort's own file
ort.env.wasm.numThreads = 1;   // no cross-origin isolation needed

const $ = id => document.getElementById(id);
const COLORS = { red: 0xd9473f, blue: 0x4f7fe0 };

// ---- HUD ------------------------------------------------------------------------------------------------
let bannerTimer = 0, last = { striker: "red" };
function banner(big, small = "", seconds = 1.6, color = "") {
  $("bannerBig").textContent = big; $("bannerBig").style.color = color; $("bannerSmall").textContent = small;
  $("banner").classList.add("on"); clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => $("banner").classList.remove("on"), seconds * 1000);
}
const corner = n => `<span class="${n}">${n.toUpperCase()}</span>`;
function onEvent(e) {
  switch (e.type) {
    case "match_start": banner(`Match ${e.match}`, `${e.first} wins the toss`, 2); renderCards([[0, 0], [0, 0], [0, 0]]); break;
    case "turn_start":
      last.striker = e.striker;
      $("round").textContent = `Round ${e.round} / 3`;
      $("turn").innerHTML = `${corner(e.striker)} strikes`;
      $("strike").textContent = ""; $("call").textContent = ""; break;
    case "strike":
      $("strike").textContent = `${e.speed.toFixed(1)} m/s · ${e.part.replace("_r", "")}`;
      banner(e.palm ? "Clean palm" : "Hit", `${e.speed.toFixed(1)} m/s${e.palm ? "" : " · not the palm"}`, 1.4, e.palm ? "var(--gold)" : "");
      flash(e.pos); break;
    case "knockdown": $("call").textContent = `${e.athlete} down (${e.part})`; break;
    case "foul": $("call").textContent = `Foul: ${e.athlete} ${e.why}`; break;
    case "turn_end": {
      const call = e.ko ? "KO" : e.end === "recovered" ? `Recovered · head snap ${e.head_peak.toFixed(1)} m/s` : e.fouls.length ? `Foul: ${e.fouls.map(f => f.join(" ")).join(", ")}` : e.end;
      $("call").textContent = call;
      if (e.ko) banner("KO!", `${e.striker} wins`, 3, "var(--gold)");
      break;
    }
    case "round": renderCards(e.cards); break;
    case "result":
      renderCards(e.cards);
      banner(e.winner ? `${e.winner} wins` : "Draw", e.how, 5, e.winner === "red" ? "var(--red)" : e.winner === "blue" ? "var(--blue)" : "");
      $("series").textContent = `Series ${game.match.series[0]} – ${game.match.series[1]}`; break;
  }
}
function renderCards(cards) {
  $("cards").innerHTML = cards.map((c, j) => `<tr><th>${j + 1}</th><td class="r">${c[0]}</td><td class="b">${c[1]}</td></tr>`).join("");
}

// ---- 3D -------------------------------------------------------------------------------------------------
const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.4;   // the arena art is dark at 1.05 (Unreal meters on the lit stage)
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0b0d); scene.fog = new THREE.Fog(0x0b0b0d, 8, 18);
const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 50);
camera.up.set(0, 0, 1);   // MuJoCo is Z-up
const controls = new OrbitControls(camera, canvas);
const VIEWS = [{ pos: [0, 4.6, 1.5], at: [0, 0, 1.05] }, { pos: [2.6, 2.6, 2.4], at: [0, 0, 1.1] }, { pos: [-2.4, 0.9, 1.75], at: [0.2, 0, 1.4] }];
let view = 0;
function setView(i) { const v = VIEWS[i]; camera.position.set(...v.pos); controls.target.set(...v.at); controls.update(); }
setView(0);

scene.add(new THREE.HemisphereLight(0x8890a8, 0x201814, 1.1));
const key = new THREE.SpotLight(0xfff1dc, 60, 12, 0.55, 0.5, 1.2);
key.position.set(0.3, 1.2, 5.5); key.target.position.set(0, 0, 1); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
scene.add(key, key.target);
for (const [x, c] of [[-2.4, 0xff8a5c], [2.4, 0x7aa0ff]]) { const r = new THREE.PointLight(c, 6, 6); r.position.set(x, -1.6, 2.6); scene.add(r); }

// the mat: dark floor, a red half and a blue half under the corners, a white ring, the foot boxes
const mat = new THREE.Group(); scene.add(mat);   // the stand-in mat and floor, until the arena art loads
const disc = (r0, r1, color, a0 = 0, len = Math.PI * 2, z = 0.001) => {
  const m = new THREE.Mesh(new THREE.RingGeometry(r0, r1, 96, 1, a0, len), new THREE.MeshStandardMaterial({ color, roughness: 0.85 }));
  m.position.z = z; m.receiveShadow = true; mat.add(m); return m;
};
const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshStandardMaterial({ color: 0x141416, roughness: 1 }));
floor.receiveShadow = true; mat.add(floor);
disc(0, 2.6, 0x7e2a26, Math.PI / 2, Math.PI);      // red corner at -X
disc(0, 2.6, 0x26407e, -Math.PI / 2, Math.PI);     // blue corner at +X
disc(2.6, 2.75, 0xe9e6dd, 0, Math.PI * 2, 0.002);
disc(0, 1.35, 0x3a3a40, 0, Math.PI * 2, 0.0015).material.transparent = true;

const shapes = [];   // [mesh, geom id]
const art = { athletes: [], arena: null, physics: false };   // the art view (meshes) or the physics view (shapes)
function buildShapes(m, sides) {
  const owner = g => sides.find(s => s.geoms.has(g));
  for (let g = 0; g < m.ngeom; g++) {
    const type = m.geom_type[g], sz = [m.geom_size[3 * g], m.geom_size[3 * g + 1], m.geom_size[3 * g + 2]];
    let geo;
    if (type === 2) geo = new THREE.SphereGeometry(sz[0], 24, 16);
    else if (type === 3) geo = new THREE.CapsuleGeometry(sz[0], 2 * sz[1], 8, 16).rotateX(Math.PI / 2);   // MuJoCo capsules run along local z
    else if (type === 4) geo = new THREE.SphereGeometry(1, 24, 16).scale(...sz);
    else if (type === 5) geo = new THREE.CylinderGeometry(sz[0], sz[0], 2 * sz[1], 24).rotateX(Math.PI / 2);
    else if (type === 6) geo = new THREE.BoxGeometry(2 * sz[0], 2 * sz[1], 2 * sz[2]);
    else continue;   // the plane is drawn as the mat above
    const s = owner(g), color = s ? COLORS[s.name] : 0x1c1c22;
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, roughness: s ? 0.55 : 0.4, metalness: s ? 0 : 0.2 }));
    mesh.castShadow = mesh.receiveShadow = true; mesh.matrixAutoUpdate = false;
    scene.add(mesh); shapes.push([mesh, g]);
  }
}
function footBoxes(d, sides) {
  for (const s of sides) {
    const xs = s.feet.map(f => d.geom_xpos[3 * f]), ys = s.feet.map(f => d.geom_xpos[3 * f + 1]);
    const cx = (xs[0] + xs[1]) / 2, cy = (ys[0] + ys[1]) / 2, hx = 0.2 + 0.13, hy = 0.25 + Math.abs(ys[0] - ys[1]) / 2 + 0.05;
    const pts = [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy], [-hx, -hy]].map(([x, y]) => new THREE.Vector3(cx + x, cy + y, 0.004));
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xf1c24b })));
  }
}
function draw(d) {
  for (const a of art.athletes) a.update(d);
  const P = d.geom_xpos, X = d.geom_xmat;
  for (const [mesh, g] of shapes) {
    const b = 9 * g;
    mesh.matrix.set(X[b], X[b + 1], X[b + 2], P[3 * g], X[b + 3], X[b + 4], X[b + 5], P[3 * g + 1], X[b + 6], X[b + 7], X[b + 8], P[3 * g + 2], 0, 0, 0, 1);
  }
}
const fx = [];
function flash(pos) {
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.05, 0.012, 8, 32), new THREE.MeshBasicMaterial({ color: 0xffe08a, transparent: true }));
  ring.position.set(...pos); ring.lookAt(camera.position); scene.add(ring); fx.push([ring, 0]);
}
function tickFx(dt) {
  for (let i = fx.length - 1; i >= 0; i--) {
    const f = fx[i]; f[1] += dt; f[0].scale.setScalar(1 + f[1] * 14); f[0].material.opacity = Math.max(0, 1 - f[1] / 0.45);
    if (f[1] > 0.45) { scene.remove(f[0]); f[0].geometry.dispose(); fx.splice(i, 1); }
  }
}
function resize() { const w = innerWidth, h = innerHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
addEventListener("resize", resize); resize();

// ---- the loop: physics in real time, drawing every frame ---------------------------------------------------
const get = async (p, kind) => { const r = await fetch("../" + p); if (!r.ok) throw new Error(`${p}: ${r.status}`); return kind === "text" ? r.text() : kind === "json" ? r.json() : new Uint8Array(await r.arrayBuffer()); };
const MATCHUP = MATCHUPS[new URLSearchParams(location.search).get("m")] ? new URLSearchParams(location.search).get("m") : "gg";
let game, speed = 1, paused = false, busy = false, debt = 0, prevT = performance.now();
try {
  $("loadmsg").textContent = "Loading MuJoCo…";
  const mj = await loadMujoco();
  $("loadmsg").textContent = "Loading the arena and both brains…";
  game = await loadMatch(mj, ort, get, onEvent, MATCHUP);
  $("matchup").innerHTML = Object.entries(MATCHUPS).map(([k, v]) => `<a href="?m=${k}" aria-current="${k === MATCHUP}">${v.name}${v.untrained ? " *" : ""}</a>`).join("")
    + (MATCHUPS[MATCHUP].untrained ? `<span class="note">* never trained together: expect falls</span>` : "");
  buildShapes(game.m, game.match.sides); footBoxes(game.d, game.match.sides);
  loadArt(mj).catch(err => console.error("art:", err));   // the match starts on the shapes; the art swaps in when loaded
  $("tape").innerHTML = game.match.sides.map(s => { const c = s.card; return `<div><b class="${s.name === "red" ? "r" : "b"}">${c.display}</b> ${c.height_m} m · ${c.mass_kg} kg · STR ${c.strength} · SPD ${c.speed}</div>`; }).join("");
  $("loading").remove();
  requestAnimationFrame(loop);
} catch (err) {
  $("loadmsg").textContent = `Could not start: ${err.message}`; console.error(err);
}

async function advance(n) {
  busy = true;
  try { for (let i = 0; i < n; i++) await game.match.step(); } finally { busy = false; }
}
function loop(now) {
  const dt = Math.min(0.1, (now - prevT) / 1000); prevT = now;
  if (!paused) debt += dt * speed;
  const h = game.match.dt;
  if (!busy && debt >= h) { const n = Math.min(Math.floor(debt / h), 60); debt -= n * h; if (debt > 0.25) debt = 0; advance(n); }
  const M = game.match;
  if (M.phase === "turn") $("clock").textContent = `${M.touchK === null ? "clock" : "recovery"} ${(M.touchK === null ? Math.max(0, 30 - M.k * h) : Math.max(0, 5 - (M.k - M.touchK) * h)).toFixed(1)} s`;
  draw(game.d); tickFx(dt * (paused ? 0 : 1)); controls.update(); renderer.render(scene, camera);
  requestAnimationFrame(loop);
}

$("pause").onclick = e => { paused = !paused; e.target.setAttribute("aria-pressed", paused); e.target.textContent = paused ? "Play" : "Pause"; };
document.querySelectorAll("[data-speed]").forEach(b => b.onclick = () => {
  speed = +b.dataset.speed; document.querySelectorAll("[data-speed]").forEach(x => x.setAttribute("aria-pressed", x === b));
});
$("cam").onclick = () => { view = (view + 1) % VIEWS.length; setView(view); };
$("phys").onclick = e => { art.physics = !art.physics; e.target.setAttribute("aria-pressed", art.physics); showArt(); };

async function loadArt(mj) {
  const TINTS = { red_: 0xd9473f, blue_: 0x4f7fe0 };
  const [arena, ...athletes] = await Promise.all([loadArena(scene),
    ...game.matchup.corners.map(c => loadAthlete(scene, `./assets/${c.char}.glb`, mj, game.m, c.prefix, TINTS[c.prefix]))]);
  art.arena = arena; art.athletes = athletes; showArt();
}
function showArt() {   // art view: meshes and arena; physics view: every MuJoCo shape on the stand-in mat
  const on = !art.physics && art.arena;
  for (const [mesh] of shapes) mesh.visible = !on;
  mat.visible = !on;
  if (art.arena) art.arena.visible = !art.physics;
  for (const a of art.athletes) a.root.visible = !art.physics;
}
