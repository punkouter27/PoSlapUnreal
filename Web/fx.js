// The broadcast look in the browser: image-based light, ground-truth ambient occlusion, bloom, SMAA, a lens/shock pass,
// the key light's visible beam with dust in it, the impact spray, and the skin shader (wrapped subsurface light, peach
// fuzz sheen, the cheek ripple and flush, the greyer tired skin: the same parameters as Unreal's M_<athlete>,
// Scripts/broadcast_fx.py). Drawing only: MuJoCo owns every contact.
//
// Quality tiers (?gfx=ultra|high|low, or View): ultra = full-res GTAO + bloom + SMAA + 4K shadows + the beam,
// high = half-res GTAO + bloom + SMAA, low = the plain forward render. "auto" starts at ultra on a desktop, high on a
// phone and low on a phone with little memory or few cores, and steps down a tier when the frame rate stays under 30 fps.
// The screen shake follows the palm's speed and is off under load (low tier, under 30 fps) and for reduced motion.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { SMAAPass } from "three/addons/postprocessing/SMAAPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export const TIERS = ["low", "high", "ultra"];

// ---- the lens: the shock ring of a big hit, chromatic fringe, vignette, film grain ----------------------------------
const LensShader = {
  uniforms: { tDiffuse: { value: null }, center: { value: new THREE.Vector2(0.5, 0.5) }, age: { value: 9 }, amp: { value: 0 },
    aspect: { value: 1 }, time: { value: 0 }, grain: { value: 0.02 } },
  vertexShader: "varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform vec2 center; uniform float age, amp, aspect, time, grain; varying vec2 vUv;
    void main() {
      vec2 d = vUv - center; d.x *= aspect;
      float r = length(d), front = age * 0.9;                                  // the ring runs out from the contact
      float w = amp * exp(-pow((r - front) / 0.035, 2.0)) * clamp(1.0 - age / 0.45, 0.0, 1.0);
      vec2 dir = r > 1e-4 ? d / r : vec2(0.0);
      vec2 off = dir * w * 0.025; off.x /= aspect;
      vec2 uv = vUv - off, e = (vUv - 0.5) * (0.0015 + w * 0.01);             // the lens fringes at the edges, more in the ring
      vec3 c = vec3(texture2D(tDiffuse, uv + e).r, texture2D(tDiffuse, uv).g, texture2D(tDiffuse, uv - e).b);
      c += w * 0.08;                                                           // the flash in the ring
      vec2 q = vUv - 0.5; c *= 1.0 - 0.55 * dot(q, q);                          // vignette
      float n = fract(sin(dot(floor(vUv * 1400.0) + fract(time) * 91.0, vec2(12.9898, 78.233))) * 43758.5453);
      gl_FragColor = vec4(c + (n - 0.5) * grain, 1.0);
    }`,
};

// ---- the key light's beam: a soft additive cone with drifting haze ---------------------------------------------------
function beam(from, to, radius) {
  const len = from.distanceTo(to);
  const geo = new THREE.CylinderGeometry(0.06, radius, len, 48, 24, true).translate(0, -len / 2, 0);
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    uniforms: { time: { value: 0 }, color: { value: new THREE.Color(0xfff1dc) }, len: { value: len } },
    vertexShader: `
      varying float vAlong; varying vec3 vN, vV, vW;
      uniform float len;
      void main() {
        vAlong = -position.y / len;
        vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz;
        vN = normalize(mat3(modelMatrix) * normal); vV = normalize(cameraPosition - w.xyz);
        gl_Position = projectionMatrix * viewMatrix * w;
      }`,
    fragmentShader: `
      uniform float time; uniform vec3 color; varying float vAlong; varying vec3 vN, vV, vW;
      float h(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
      float noise(vec3 p) {                                                    // value noise: the haze is uneven
        vec3 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
        return mix(mix(mix(h(i), h(i + vec3(1,0,0)), f.x), mix(h(i + vec3(0,1,0)), h(i + vec3(1,1,0)), f.x), f.y),
                   mix(mix(h(i + vec3(0,0,1)), h(i + vec3(1,0,1)), f.x), mix(h(i + vec3(0,1,1)), h(i + vec3(1,1,1)), f.x), f.y), f.z);
      }
      void main() {
        float edge = pow(abs(dot(normalize(vN), normalize(vV))), 2.0);         // thick through the middle, gone at the rim
        float fall = smoothstep(0.0, 0.12, vAlong) * (1.0 - smoothstep(0.7, 1.0, vAlong));
        float haze = 0.55 + 0.45 * noise(vW * 2.2 + vec3(0.0, time * 0.08, time * 0.05));
        gl_FragColor = vec4(color * edge * fall * haze * 0.05, 1.0);
      }`,
  });
  const m = new THREE.Mesh(geo, mat);
  m.position.copy(from);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), to.clone().sub(from).normalize());
  m.renderOrder = 10;
  return m;
}

function motes(from, to, radius, n = 900) {   // dust in the beam: lit only inside it, drifting up in the heat
  const pos = new Float32Array(n * 3), seed = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.random(), r = Math.sqrt(Math.random()) * radius * (0.2 + 0.8 * a), th = Math.random() * Math.PI * 2;
    pos.set([r * Math.cos(th), -a * from.distanceTo(to), r * Math.sin(th)], 3 * i); seed[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3)); geo.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { time: { value: 0 }, scale: { value: 300 } },
    vertexShader: `
      attribute float seed; uniform float time, scale; varying float vA;
      void main() {
        vec3 p = position + vec3(sin(time * 0.3 + seed * 40.0) * 0.05, mod(time * 0.03 + seed, 1.0) * 0.4, cos(time * 0.23 + seed * 17.0) * 0.05);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vA = (0.4 + 0.6 * fract(seed * 7.13)) * (0.5 + 0.5 * sin(time * (0.5 + seed) + seed * 9.0));
        gl_PointSize = scale * (0.006 + 0.01 * fract(seed * 3.7)) / -mv.z;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying float vA;
      void main() { float d = length(gl_PointCoord - 0.5); gl_FragColor = vec4(vec3(1.0, 0.95, 0.85) * vA * 0.5 * smoothstep(0.5, 0.0, d), 1.0); }`,
  });
  const p = new THREE.Points(geo, mat);
  p.position.copy(from); p.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), to.clone().sub(from).normalize());
  p.frustumCulled = false; p.renderOrder = 11;
  return p;
}

// ---- the impact spray: sweat droplets thrown off the cheek, with gravity and drag, drawn as short streaks -------------
class Spray {
  constructor(scene, n = 600) {
    this.n = n; this.i = 0;
    this.p = new Float32Array(n * 3); this.v = new Float32Array(n * 3); this.life = new Float32Array(n).fill(9);
    const geo = new THREE.BufferGeometry();
    this.attr = new THREE.BufferAttribute(new Float32Array(n * 3), 3); this.attr.setUsage(THREE.DynamicDrawUsage);
    this.age = new THREE.BufferAttribute(new Float32Array(n), 1); this.age.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("position", this.attr); geo.setAttribute("age", this.age);
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, uniforms: { scale: { value: 300 } },
      vertexShader: `
        attribute float age; uniform float scale; varying float vA;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); vA = clamp(1.0 - age / 0.7, 0.0, 1.0);
          gl_PointSize = scale * 0.035 / -mv.z * (0.6 + vA); gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `
        varying float vA;
        void main() { vec2 q = gl_PointCoord - 0.5; float d = length(q);
          float spec = smoothstep(0.18, 0.0, length(q + vec2(0.12, 0.12)));     // a glint on each droplet
          gl_FragColor = vec4(vec3(0.75, 0.8, 0.85) * (0.35 + 1.4 * spec) * vA * smoothstep(0.5, 0.2, d), 1.0); }`,
    });
    this.points = new THREE.Points(geo, mat); this.points.frustumCulled = false; this.points.renderOrder = 12;
    scene.add(this.points);
  }
  // dir: the slap's direction (unit), speed: the palm's m/s, size: the strike's momentum relative to a clean slap
  burst(pos, dir, speed, size = 1) {
    const k = Math.min(this.n, Math.round((40 + 10 * speed) * size));
    for (let j = 0; j < k; j++) {
      const i = this.i = (this.i + 1) % this.n;
      const s = speed * (0.25 + 0.6 * Math.random()) * Math.sqrt(size);
      const r = new THREE.Vector3().randomDirection().multiplyScalar(0.55).add(dir).normalize();
      this.p.set(pos.toArray(), 3 * i); this.v.set([r.x * s, r.y * s, r.z * s + 0.8 * Math.random()], 3 * i); this.life[i] = 0;
    }
  }
  tick(dt) {
    for (let i = 0; i < this.n; i++) {
      if (this.life[i] > 0.7) { this.attr.array[3 * i + 2] = -99; continue; }
      this.life[i] += dt;
      const drag = Math.exp(-dt * 3.5);
      this.v[3 * i] *= drag; this.v[3 * i + 1] *= drag; this.v[3 * i + 2] = this.v[3 * i + 2] * drag - 9.81 * dt;
      for (let c = 0; c < 3; c++) { this.p[3 * i + c] += this.v[3 * i + c] * dt; this.attr.array[3 * i + c] = this.p[3 * i + c]; }
      this.age.array[i] = this.life[i];
    }
    this.attr.needsUpdate = this.age.needsUpdate = true;
  }
}

// ---- the knockdown dust: chalk and mat dust kicked up where a body hits the floor or the podium ------------------------
class Dust {
  constructor(scene, n = 400) {
    this.n = n; this.i = 0;
    this.p = new Float32Array(n * 3); this.v = new Float32Array(n * 3); this.life = new Float32Array(n).fill(9);
    const geo = new THREE.BufferGeometry();
    this.attr = new THREE.BufferAttribute(new Float32Array(n * 3).fill(-99), 3); this.attr.setUsage(THREE.DynamicDrawUsage);
    this.age = new THREE.BufferAttribute(new Float32Array(n).fill(9), 1); this.age.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("position", this.attr); geo.setAttribute("age", this.age);
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, uniforms: { scale: { value: 300 } },
      vertexShader: `
        attribute float age; uniform float scale; varying float vA;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); vA = clamp(1.0 - age / 1.8, 0.0, 1.0);
          gl_PointSize = scale * (0.06 + 0.12 * (1.0 - vA)) / -mv.z; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `
        varying float vA;
        void main() { float d = length(gl_PointCoord - 0.5);
          gl_FragColor = vec4(vec3(0.62, 0.58, 0.52), 0.32 * vA * smoothstep(0.5, 0.0, d)); }`,
    });
    this.points = new THREE.Points(geo, mat); this.points.frustumCulled = false; this.points.renderOrder = 12;
    scene.add(this.points);
  }
  puff(pos, k = 1) {   // pos: where the body met the floor; k: how hard it landed (0..1.5)
    const m = Math.round(60 + 90 * k);
    for (let j = 0; j < m; j++) {
      const i = this.i = (this.i + 1) % this.n, a = Math.random() * Math.PI * 2, s = (0.4 + 1.2 * Math.random()) * (0.5 + k);
      this.p.set([pos.x + 0.05 * Math.cos(a), pos.y + 0.05 * Math.sin(a), Math.max(0.02, pos.z)], 3 * i);
      this.v.set([Math.cos(a) * s, Math.sin(a) * s, 0.2 + 0.5 * Math.random()], 3 * i); this.life[i] = 0;
    }
  }
  tick(dt) {
    for (let i = 0; i < this.n; i++) {
      if (this.life[i] > 1.8) { this.attr.array[3 * i + 2] = -99; continue; }
      this.life[i] += dt;
      const drag = Math.exp(-dt * 4);
      this.v[3 * i] *= drag; this.v[3 * i + 1] *= drag; this.v[3 * i + 2] = this.v[3 * i + 2] * drag + 0.05 * dt;   // it hangs, then drifts up
      for (let c = 0; c < 3; c++) { this.p[3 * i + c] += this.v[3 * i + c] * dt; this.attr.array[3 * i + c] = this.p[3 * i + c]; }
      this.age.array[i] = this.life[i];
    }
    this.attr.needsUpdate = this.age.needsUpdate = true;
  }
}

// ---- the contact burst: a ring of light off the cheek, sized by the strike's momentum (strike mass x palm speed) ----
class Rings {
  constructor(scene) { this.scene = scene; this.live = []; }
  add(pos, camera, size) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.04 + 0.03 * size, 0.01 + 0.006 * size, 8, 40),
      new THREE.MeshBasicMaterial({ color: 0xffe08a, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
    ring.position.copy(pos); ring.lookAt(camera.position); ring.renderOrder = 13;
    this.scene.add(ring); this.live.push([ring, 0, size]);
  }
  tick(dt) {
    for (let i = this.live.length - 1; i >= 0; i--) {
      const f = this.live[i]; f[1] += dt;
      f[0].scale.setScalar(1 + f[1] * (10 + 8 * f[2])); f[0].material.opacity = Math.max(0, 1 - f[1] / 0.45);
      if (f[1] > 0.45) { this.scene.remove(f[0]); f[0].geometry.dispose(); f[0].material.dispose(); this.live.splice(i, 1); }
    }
  }
}

// ---- skin: the glTF's standard material with Unreal's skin parameters -------------------------------------------------
// wrapped light per channel (red light travels further under the skin: a cheap pre-integrated subsurface look) and a
// warm rim where the surface turns away (peach fuzz catching the light: three's sheen for a fraction of its cost - the
// physical material alone cost ~15 fps on the two athletes); the vertex shader pushes the cheek in and rings it
// (RipplePoint/Dir/Time/Amp, cm as in Unreal), the colour flushes red round FlushPoint and greys with Tired.
export function skin(std) {
  const m = std.clone();
  const u = {
    ripplePoint: { value: new THREE.Vector3(0, 0, -9) }, rippleDir: { value: new THREE.Vector3(1, 0, 0) },
    rippleTime: { value: 100 }, rippleAmp: { value: 0 },
    flushPoint: { value: new THREE.Vector3(0, 0, -9) }, flush: { value: 0 }, tired: { value: 0 },
    flushRadius: { value: 9 }, bruise: { value: 0 },   // cm round the hit; the cheek-wide redness the neck damage builds
  };
  m.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", `#include <common>
        uniform vec3 ripplePoint, rippleDir; uniform float rippleTime, rippleAmp; varying vec3 vSkinW;`)
      .replace("#include <skinning_vertex>", `#include <skinning_vertex>
        {
          vec3 w = (modelMatrix * vec4(transformed, 1.0)).xyz, nW = normalize(mat3(modelMatrix) * objectNormal);
          float D = distance(w, ripplePoint) * 100.0, T = rippleTime, A = rippleAmp;   // Unreal's ripple, in cm
          float fall = clamp(1.0 - D / 14.0, 0.0, 1.0);
          float ring = A * exp(-T * 7.0) * sin(D * 0.7 - T * 45.0) * fall;
          vec3 dir = normalize(rippleDir + vec3(0.0, 0.0, 0.001));
          float push = A * exp(-T * 9.0) * fall * fall * clamp(dot(nW, dir), 0.0, 1.0);
          vec3 off = (nW * ring + dir * push * 4.0) * 0.01;
          transformed += inverse(mat3(modelMatrix)) * off;
        }`)
      .replace("#include <worldpos_vertex>", `#include <worldpos_vertex>
        vSkinW = (modelMatrix * vec4(transformed, 1.0)).xyz;`);
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>
        uniform vec3 flushPoint; uniform float flush, tired, flushRadius, bruise; varying vec3 vSkinW;`)
      .replace("#include <color_fragment>", `#include <color_fragment>
        {
          float D = distance(vSkinW, flushPoint) * 100.0;
          float f = flush * clamp(1.0 - D / flushRadius, 0.0, 1.0) + bruise * 0.45 * clamp(1.0 - D / (2.2 * flushRadius), 0.0, 1.0);
          vec3 c = mix(diffuseColor.rgb, dot(diffuseColor.rgb, vec3(0.3, 0.59, 0.11)) * vec3(0.7, 0.72, 0.75), tired * 0.35);
          diffuseColor.rgb = mix(c, c * vec3(1.5, 0.5, 0.5), clamp(f, 0.0, 1.0));
        }`)
      // the wrap is the diffuse term's only: fed to the specular it lights GGX at grazing angles, where it divides by ~0
      .replace("#include <lights_physical_pars_fragment>", THREE.ShaderChunk.lights_physical_pars_fragment.replace(
        "reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );",
        `vec3 wrapK = vec3(0.45, 0.2, 0.12);                                  // how far each channel wraps past the terminator
        vec3 wrapped = clamp((dot(geometryNormal, directLight.direction) + wrapK) / (1.0 + wrapK), 0.0, 1.0) * directLight.color;
        reflectedLight.directDiffuse += wrapped * BRDF_Lambert( material.diffuseColor );`))
      .replace("#include <opaque_fragment>", `{                                                   // the fuzz rim, lit by what lights the skin
          float rim = pow(1.0 - saturate(dot(normal, geometryViewDir)), 4.0);
          outgoingLight += vec3(1.0, 0.82, 0.75) * rim * 0.35 * (reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + 0.02);
        }
        #include <opaque_fragment>`);
  };
  m.customProgramCacheKey = () => "poslap-skin";
  m.userData.skin = u;
  return m;
}

// how much of the room's image light a material takes: a little on the arena (the stage light stays the light),
// more on the athletes (their skin and clothes catch reflections)
export function envLight(root, k) { root.traverse(o => { if (o.isMesh) for (const m of [].concat(o.material)) m.envMapIntensity = k; }); }

// ---- the pipeline -----------------------------------------------------------------------------------------------------
export class Look {
  constructor(renderer, scene, camera, key) {
    Object.assign(this, { renderer, scene, camera, key, tier: "low", auto: true, slow: 0, shake: 0, shakeAmp: 0, fps: 60, shakeOn: true });
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const pm = new THREE.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;   // speculars and reflections from a room
    scene.environmentIntensity = 1;   // per material: the arena keeps its dark, stage-lit look (envLight)
    pm.dispose();
    scene.fog = new THREE.FogExp2(0x0b0b0d, 0.055);
    const from = key.position.clone(), to = key.target.position.clone();
    this.beam = beam(from, to, 1.5); this.motes = motes(from, to, 1.2);
    scene.add(this.beam, this.motes);
    this.spray = new Spray(scene); this.dust = new Dust(scene); this.rings = new Rings(scene);
    this.composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 0 }));
    this.composer.addPass(new RenderPass(scene, camera));
    this.gtao = new GTAOPass(scene, camera, 1, 1);
    this.gtao.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.5, thickness: 1.2, scale: 1.0, samples: 16 });
    this.gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16 });
    this.composer.addPass(this.gtao);
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.25, 0.5, 3.0);   // linear HDR: only real highlights bloom
    this.composer.addPass(this.bloom);
    this.lens = new ShaderPass(LensShader);   // in linear HDR, before the tone map: after it the frame would be encoded twice
    this.composer.addPass(this.lens);
    this.composer.addPass(new OutputPass());
    this.smaa = new SMAAPass(1, 1);
    this.composer.addPass(this.smaa);
  }
  setTier(t) {
    this.auto = !TIERS.includes(t);   // a named tier stays put; "auto" may step down
    if (this.auto) {
      const phone = matchMedia("(pointer: coarse)").matches, small = (navigator.deviceMemory || 8) <= 3 || (navigator.hardwareConcurrency || 8) <= 4;
      t = phone ? (small ? "low" : "high") : "ultra";
    }
    this.tier = t;
    const ultra = t === "ultra";
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, t === "low" ? 1.25 : 2));
    this.key.shadow.mapSize.set(ultra ? 4096 : 2048, ultra ? 4096 : 2048);
    this.key.shadow.map?.dispose(); this.key.shadow.map = null;
    this.beam.visible = this.motes.visible = t !== "low";
    this.gtao.enabled = t !== "low";
    this.resize(innerWidth, innerHeight);
  }
  resize(w, h) {
    this.renderer.setSize(w, h, false);
    const pr = this.renderer.getPixelRatio();
    this.composer.setPixelRatio(pr); this.composer.setSize(w, h);
    if (this.tier === "high") this.gtao.setSize(Math.round(w * pr / 2), Math.round(h * pr / 2));   // half-res AO on a phone
    this.lens.uniforms.aspect.value = w / h;
    this.motes.material.uniforms.scale.value = this.spray.points.material.uniforms.scale.value =
      this.dust.points.material.uniforms.scale.value = h * pr * 0.5;
  }
  // the shake runs only when it is affordable and wanted: not on the low tier, not under 30 fps, not for reduced
  // motion, not when the viewer turned it off
  get canShake() { return this.shakeOn && !this.reduced.matches && this.tier !== "low" && this.fps >= 30; }
  // a strike: droplets off the cheek and a ring of light, both sized by the momentum (size 1 = a 3 kg x 9 m/s clean
  // slap), the lens's shock ring from the contact, the camera jolts with the palm's speed
  hit(pos, dir, speed, size = 1) {
    const p = new THREE.Vector3(...pos);
    this.spray.burst(p, dir, speed, size);
    this.rings.add(p, this.camera, size);
    const s = p.clone().project(this.camera);
    this.lens.uniforms.center.value.set(s.x * 0.5 + 0.5, s.y * 0.5 + 0.5);
    this.lens.uniforms.age.value = 0; this.lens.uniforms.amp.value = Math.min(1.5, Math.max(0, (speed - 4) / 6));
    this.shake = 0; this.shakeAmp = this.canShake ? Math.min(0.03, 0.002 * speed) : 0;
  }
  knockdown(pos, k) { this.dust.puff(new THREE.Vector3(...pos), k); }
  render(dt, fps) {
    const t = performance.now() / 1000;
    this.beam.material.uniforms.time.value = this.motes.material.uniforms.time.value = this.lens.uniforms.time.value = t;
    this.lens.uniforms.age.value += dt;
    this.spray.tick(dt); this.dust.tick(dt); this.rings.tick(dt);
    if (fps) this.fps = fps;
    if (this.auto && fps) {   // a slow machine steps down a tier after 4 s under 30 fps (the floor)
      this.slow = fps < 30 ? this.slow + dt : 0;
      if (this.slow > 4 && this.tier !== "low") { this.slow = 0; this.setTier(TIERS[TIERS.indexOf(this.tier) - 1]); this.auto = true; }
    }
    let jolt = null;
    if (this.shakeAmp > 0 && !this.canShake) this.shakeAmp = 0;   // the load or the setting changed mid-jolt
    if (this.shakeAmp > 0) {   // a damped jolt, then the camera is put back where the orbit controls left it
      this.shake += dt;
      const a = this.shakeAmp * Math.exp(-this.shake * 14) * Math.sin(this.shake * 70);
      if (this.shake > 0.4) this.shakeAmp = 0;
      jolt = new THREE.Vector3(a, 0, a * 0.6).applyQuaternion(this.camera.quaternion);
      this.camera.position.add(jolt);
    }
    if (this.tier === "low") {
      this.renderer.toneMappingExposure = 1.4;
      this.renderer.render(this.scene, this.camera);
    } else this.composer.render(dt);
    if (jolt) this.camera.position.sub(jolt);
  }
}
