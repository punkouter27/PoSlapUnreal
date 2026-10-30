// The broadcast look in the browser: image-based light, ground-truth ambient occlusion, bloom, SMAA, a lens/shock pass,
// the key light's visible beam with dust in it, the impact spray, and the skin shader (wrapped subsurface light, peach
// fuzz sheen, the cheek ripple and flush, the greyer tired skin: the same parameters as Unreal's M_<athlete>,
// Scripts/broadcast_fx.py). Drawing only: MuJoCo owns every contact.
//
// Quality tiers (?gfx=ultra|high|low, or Settings): ultra = full-res GTAO + bloom + SMAA + 4K shadows + the beam,
// high = half-res GTAO + bloom + SMAA, low = the plain forward render. "auto" starts at ultra on a desktop and high
// on a phone, and steps down a tier when the frame rate stays under 30 fps.
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
  burst(pos, dir, speed) {   // dir: the slap's direction (unit), speed: the palm's m/s
    const k = Math.round(40 + 10 * speed);
    for (let j = 0; j < k; j++) {
      const i = this.i = (this.i + 1) % this.n;
      const s = speed * (0.25 + 0.6 * Math.random());
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
        uniform vec3 flushPoint; uniform float flush, tired; varying vec3 vSkinW;`)
      .replace("#include <color_fragment>", `#include <color_fragment>
        {
          float f = flush * clamp(1.0 - distance(vSkinW, flushPoint) * 100.0 / 9.0, 0.0, 1.0);
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
    Object.assign(this, { renderer, scene, camera, key, tier: "low", auto: true, slow: 0, shake: 0, shakeAmp: 0 });
    const pm = new THREE.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;   // speculars and reflections from a room
    scene.environmentIntensity = 1;   // per material: the arena keeps its dark, stage-lit look (envLight)
    pm.dispose();
    scene.fog = new THREE.FogExp2(0x0b0b0d, 0.055);
    const from = key.position.clone(), to = key.target.position.clone();
    this.beam = beam(from, to, 1.5); this.motes = motes(from, to, 1.2);
    scene.add(this.beam, this.motes);
    this.spray = new Spray(scene);
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
    if (this.auto) t = matchMedia("(pointer: coarse)").matches ? "high" : "ultra";
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
    this.motes.material.uniforms.scale.value = this.spray.points.material.uniforms.scale.value = h * pr * 0.5;
  }
  // a strike: droplets off the cheek, the shock ring from the contact, the camera jolts with the palm's speed
  hit(pos, dir, speed) {
    const p = new THREE.Vector3(...pos);
    this.spray.burst(p, dir, speed);
    const s = p.clone().project(this.camera);
    this.lens.uniforms.center.value.set(s.x * 0.5 + 0.5, s.y * 0.5 + 0.5);
    this.lens.uniforms.age.value = 0; this.lens.uniforms.amp.value = Math.min(1.5, Math.max(0, (speed - 4) / 6));
    this.shake = 0; this.shakeAmp = Math.min(0.03, 0.002 * speed);
  }
  render(dt, fps) {
    const t = performance.now() / 1000;
    this.beam.material.uniforms.time.value = this.motes.material.uniforms.time.value = this.lens.uniforms.time.value = t;
    this.lens.uniforms.age.value += dt;
    this.spray.tick(dt);
    if (this.auto && fps) {   // a slow machine steps down a tier after 4 s under 30 fps (the floor)
      this.slow = fps < 30 ? this.slow + dt : 0;
      if (this.slow > 4 && this.tier !== "low") { this.slow = 0; this.setTier(TIERS[TIERS.indexOf(this.tier) - 1]); this.auto = true; }
    }
    let jolt = null;
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
