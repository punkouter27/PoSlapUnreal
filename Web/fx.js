// The broadcast look in the browser. Drawing only: MuJoCo owns every contact.
//
// The frame: the scene (with a depth texture) -> a linear-depth copy every later pass reads -> screen-space subsurface
// scattering on the skin -> ground-truth AO -> a ray-marched volumetric key light that the athletes' shadow-map
// silhouettes cut into the haze -> depth of field (autofocus on the screen centre: the director's subject) -> motion
// blur on a fast palm -> bloom -> the lens/shock pass (chromatic fringe, vignette, film grain) -> tone map -> SMAA.
// In the scene: image-based light, the key light's dust motes, the impact spray, the knockdown dust, the contact ring,
// a planar reflection on the stage floor, and the skin shader (wrapped subsurface diffuse, peach-fuzz rim, a tileable
// pore/wrinkle micro-relief baked in Blender, a wet sweat lobe that beads as stamina drops, the cheek ripple, flush
// and the greyer tired skin: the same parameters as Unreal's M_<athlete>, Scripts/broadcast_fx.py).
//
// Quality tiers (?gfx=low|high|ultra|cinema, or View), each a set of effects (TIER_FX); ?fx=sss:0,vol:48,... overrides
// single effects (Web/fps_ladder.mjs steps through them; wet:1 forces a late-match sweat for the report's shots).
// "auto" picks from the measured ladder (DEFAULT_TIER) and
// steps down a tier when the frame rate stays under 30 fps for 4 s; a phone starts at high (low with little memory).
// The screen shake follows the palm's speed and is off under load (low tier, under 30 fps) and for reduced motion.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { SMAAPass } from "three/addons/postprocessing/SMAAPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { Pass, FullScreenQuad } from "three/addons/postprocessing/Pass.js";
import { Reflector } from "three/addons/objects/Reflector.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export const TIERS = ["low", "high", "ultra", "cinema"];
// what each tier draws: gtao 0/half/full, sss / vol / dof = taps or ray-march steps (0 = off), refl = planar floor
// reflection (its resolution share), ss = supersampling (pixel ratio x), pr = the pixel-ratio cap
export const TIER_FX = {
  low:    { gtao: 0, bloom: 0, smaa: 0, beam: 0, motes: 0, mblur: 0, sss: 0, vol: 0, dof: 0, refl: 0, detail: 1, sweat: 1, crowd: 1, pr: 1.25, ss: 1, shadow: 2048 },
  high:   { gtao: 1, bloom: 1, smaa: 1, beam: 1, motes: 1, mblur: 1, sss: 0, vol: 0, dof: 0, refl: 0, detail: 1, sweat: 1, crowd: 1, pr: 2, ss: 1, shadow: 2048 },
  ultra:  { gtao: 2, bloom: 1, smaa: 1, beam: 0, motes: 1, mblur: 1, sss: 16, vol: 24, dof: 24, refl: 0, detail: 1, sweat: 1, crowd: 1, pr: 2, ss: 1, shadow: 4096 },
  cinema: { gtao: 2, bloom: 1, smaa: 1, beam: 0, motes: 1, mblur: 1, sss: 32, vol: 64, dof: 48, refl: 0.75, detail: 1, sweat: 1, crowd: 1, pr: 2, ss: 1, shadow: 4096 },
};
// the "auto" start on a desktop, from the fps ladder (Web/fps_ladder.mjs, SCREENSHOTS/261103_gfx_sound2, RTX 5070 Ti
// laptop at 84-86 C, physics paused): at 412 x 915 (0.4 Mpx) ultra drew 107 fps and cinema 50; at 1080 x 2400
// (2.6 Mpx) high drew 54 and ultra 7-19. So: ultra up to 1 Mpx of drawing buffer, high above it (>= 45 fps both ways);
// cinema is opt-in. Re-run the ladder on a cool GPU and move these if the numbers move.
export const DEFAULT_TIER = { smallMpx: 1.0, small: "ultra", large: "high" };

function parseFx(s) {   // "sss:0,vol:48" -> { sss: 0, vol: 48 }
  const o = {};
  for (const kv of (s || "").split(",")) { const [k, v] = kv.split(":"); if (k && v !== undefined && !isNaN(+v)) o[k.trim()] = +v; }
  return o;
}

// transparent effects leave the frame's alpha alone: the skin writes alpha 0 so the SSS pass knows its pixels
function keepAlpha(m, additive = true) {
  m.blending = THREE.CustomBlending; m.blendEquation = THREE.AddEquation;
  m.blendSrc = additive ? THREE.OneFactor : THREE.SrcAlphaFactor; m.blendDst = additive ? THREE.OneFactor : THREE.OneMinusSrcAlphaFactor;
  m.blendSrcAlpha = THREE.ZeroFactor; m.blendDstAlpha = THREE.OneFactor; m.blendEquationAlpha = THREE.AddEquation;
  return m;
}

const QUAD_VS = "varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }";
const NOISE = `
  float h13(vec3 p) { p = fract(p * 0.1031); p += dot(p, p.zyx + 31.32); return fract((p.x + p.y) * p.z); }
  float vnoise(vec3 p) {
    vec3 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(h13(i), h13(i + vec3(1,0,0)), f.x), mix(h13(i + vec3(0,1,0)), h13(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(h13(i + vec3(0,0,1)), h13(i + vec3(1,0,1)), f.x), mix(h13(i + vec3(0,1,1)), h13(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float ign(vec2 p) { return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }   // interleaved gradient noise
`;

// ---- the lens: the shock ring of a big hit, chromatic fringe, vignette, film grain ----------------------------------
// grain: luminance-weighted (strongest in the mid-tones, as film), animated per frame; fringe grows to the edges
const LensShader = {
  uniforms: { tDiffuse: { value: null }, center: { value: new THREE.Vector2(0.5, 0.5) }, age: { value: 9 }, amp: { value: 0 },
    aspect: { value: 1 }, time: { value: 0 }, grain: { value: 0.02 }, fringe: { value: 0.0015 } },
  vertexShader: QUAD_VS,
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform vec2 center; uniform float age, amp, aspect, time, grain, fringe; varying vec2 vUv;
    ${NOISE}
    void main() {
      vec2 d = vUv - center; d.x *= aspect;
      float r = length(d), front = age * 0.9;                                  // the ring runs out from the contact
      float w = amp * exp(-pow((r - front) / 0.035, 2.0)) * clamp(1.0 - age / 0.45, 0.0, 1.0);
      vec2 dir = r > 1e-4 ? d / r : vec2(0.0);
      vec2 off = dir * w * 0.025; off.x /= aspect;
      vec2 q = vUv - 0.5;
      vec2 uv = vUv - off, e = q * (fringe * (0.4 + 2.4 * dot(q, q) * 4.0) + w * 0.01);   // the lens fringes at the edges
      vec3 c = vec3(texture2D(tDiffuse, uv + e).r, texture2D(tDiffuse, uv).g, texture2D(tDiffuse, uv - e).b);
      c += w * 0.08;                                                           // the flash in the ring
      c *= 1.0 - 0.55 * dot(q, q);                                             // vignette
      float l = dot(c, vec3(0.2126, 0.7152, 0.0722)), lw = 4.0 * l / (1.0 + l) * exp(-l * 0.6);   // mid-tone weight
      float n = ign(gl_FragCoord.xy + fract(time * 7.13) * 397.0) + ign(gl_FragCoord.yx * 1.37 + fract(time * 3.1) * 211.0) - 1.0;
      gl_FragColor = vec4(c * (1.0 + n * grain * 2.0 * lw), 1.0);
    }`,
};

// ---- the depth every screen-space pass reads: linear view distance (m) in .r -------------------------------------------
class DepthCopyPass extends Pass {
  constructor(camera) {
    super();
    this.camera = camera; this.needsSwap = false;
    this.target = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: false });
    this.quad = new FullScreenQuad(new THREE.ShaderMaterial({
      uniforms: { tDepth: { value: null }, cameraNear: { value: 0.05 }, cameraFar: { value: 50 } },
      vertexShader: QUAD_VS,
      fragmentShader: `
        #include <packing>
        uniform sampler2D tDepth; uniform float cameraNear, cameraFar; varying vec2 vUv;
        void main() { float d = texture2D(tDepth, vUv).x; gl_FragColor = vec4(-perspectiveDepthToViewZ(d, cameraNear, cameraFar), d, 0.0, 1.0); }`,
      depthTest: false, depthWrite: false,
    }));
  }
  setSize(w, h) { this.target.setSize(w, h); }
  render(renderer, writeBuffer, readBuffer) {
    const u = this.quad.material.uniforms;
    u.tDepth.value = readBuffer.depthTexture; u.cameraNear.value = this.camera.near; u.cameraFar.value = this.camera.far;
    renderer.setRenderTarget(this.target); this.quad.render(renderer);
  }
}

// a screen-space pass that reads the linear depth; `defines` sets its tap / step count (recompiled on change)
function depthPass(shader, depthTarget) {
  const p = new ShaderPass(new THREE.ShaderMaterial({
    uniforms: Object.assign({ tDiffuse: { value: null }, tLin: { value: depthTarget.texture }, res: { value: new THREE.Vector2(1, 1) },
      pxPerM: { value: 1000 } }, shader.uniforms),
    defines: Object.assign({}, shader.defines), vertexShader: QUAD_VS, fragmentShader: shader.fragmentShader, depthTest: false, depthWrite: false,
  }));
  return p;
}

// ---- screen-space subsurface scattering (separable-profile skin, Jimenez-style, done as one disk gather) ----------------
// only on skin pixels (alpha 0), only from skin taps at the same depth (+-1 cm); red light travels furthest under skin
// (sigma 3.2 / 1.3 / 0.8 mm), so the shadow edges and the pores soften red, the way skin is lit
const SSS = {
  defines: { TAPS: 16 },
  uniforms: { strength: { value: 0.8 }, rmax: { value: 7.0 } },
  fragmentShader: `
    uniform sampler2D tDiffuse, tLin; uniform vec2 res; uniform float pxPerM, strength, rmax; varying vec2 vUv;
    ${NOISE}
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      if (c.a > 0.5) { gl_FragColor = vec4(c.rgb, 1.0); return; }
      float z = texture2D(tLin, vUv).r, mm = pxPerM / max(z, 0.05) * 0.001;   // pixels per mm at this depth
      vec3 sig2 = 2.0 * vec3(3.2, 1.3, 0.8) * vec3(3.2, 1.3, 0.8), sum = c.rgb, ws = vec3(1.0);
      float rot = ign(gl_FragCoord.xy) * 6.2832;
      for (int i = 0; i < TAPS; i++) {
        float r = sqrt((float(i) + 0.5) / float(TAPS)) * rmax, a = float(i) * 2.39996 + rot;
        vec2 uv = vUv + vec2(cos(a), sin(a)) * r * mm / res;
        vec4 s = textureLod(tDiffuse, uv, 0.0); float zs = textureLod(tLin, uv, 0.0).r;
        float ok = step(s.a, 0.5) * (1.0 - smoothstep(0.004, 0.012, abs(zs - z)));
        vec3 w = exp(-r * r / sig2) * ok;
        sum += s.rgb * w; ws += w;
      }
      gl_FragColor = vec4(mix(c.rgb, sum / ws, strength), 1.0);
    }`,
};

// ---- the key light as a volume: ray-marched in-scattering, shadowed by the key's own shadow map ------------------------
// Henyey-Greenstein phase (g 0.35: haze scatters forward), drifting 3D value-noise density, jittered start per pixel
const VOL = {
  defines: { STEPS: 24 },
  uniforms: { tShadow: { value: null }, shadowMat: { value: new THREE.Matrix4() }, projInv: { value: new THREE.Matrix4() },
    camWorld: { value: new THREE.Matrix4() }, camPos: { value: new THREE.Vector3() }, lPos: { value: new THREE.Vector3() },
    lDir: { value: new THREE.Vector3(0, 0, -1) }, lColor: { value: new THREE.Color(0xfff1dc) }, cosOuter: { value: 0.85 },
    cosInner: { value: 0.93 }, lRange: { value: 12 }, density: { value: 0.12 }, intensity: { value: 0.55 }, time: { value: 0 },
    hasShadow: { value: 0 } },
  fragmentShader: `
    #include <packing>
    uniform sampler2D tDiffuse, tLin, tShadow; uniform vec2 res; uniform mat4 shadowMat, projInv, camWorld;
    uniform vec3 camPos, lPos, lDir, lColor; uniform float cosOuter, cosInner, lRange, density, intensity, time, hasShadow, pxPerM;
    varying vec2 vUv;
    ${NOISE}
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      float z = texture2D(tLin, vUv).r;
      vec4 rv = projInv * vec4(vUv * 2.0 - 1.0, -1.0, 1.0); rv.xyz /= rv.w;
      vec3 sv = rv.xyz / -rv.z * min(z, 30.0);                                  // the surface (view space)
      vec3 sw = (camWorld * vec4(sv, 1.0)).xyz, rd = normalize(sw - camPos);
      float tMax = min(distance(sw, camPos), 16.0);
      // the lit volume's bounding sphere (the cone from the lamp to the floor): march only inside it
      vec3 cc = lPos + lDir * lRange * 0.45; float R = lRange * 0.55;
      vec3 oc = camPos - cc; float b = dot(oc, rd), q = dot(oc, oc) - R * R, disc = b * b - q;
      if (disc <= 0.0) { gl_FragColor = c; return; }
      float s = sqrt(disc), t0 = max(0.0, -b - s), t1 = min(tMax, -b + s);
      if (t1 <= t0) { gl_FragColor = c; return; }
      float dt = (t1 - t0) / float(STEPS), t = t0 + dt * ign(gl_FragCoord.xy + fract(time * 1.618) * 113.0);
      float acc = 0.0, T = 1.0;
      for (int i = 0; i < STEPS; i++) {
        vec3 p = camPos + rd * t, L = p - lPos; float dl = length(L);
        float cone = smoothstep(cosOuter, cosInner, dot(L / dl, lDir));
        if (cone > 0.0) {
          float vis = 1.0;
          if (hasShadow > 0.5) {
            vec4 sc = shadowMat * vec4(p, 1.0); sc.xyz /= sc.w;
            if (all(greaterThan(sc.xyz, vec3(0.0))) && all(lessThan(sc.xyz, vec3(1.0))))
              vis = step(sc.z - 0.0015, unpackRGBAToDepth(textureLod(tShadow, sc.xy, 0.0)));
          }
          float wisp = vnoise(p * 1.3 + vec3(0.0, time * 0.05, -time * 0.08)) * vnoise(p * 0.4 - vec3(time * 0.02, 0.0, time * 0.015));
          float dens = density * (0.25 + 3.0 * smoothstep(0.12, 0.55, wisp));   // a haze machine's wisps, not a fog
          float att = 1.0 / (1.0 + 0.04 * dl * dl);
          acc += T * dens * cone * vis * att * dt;
          T *= exp(-dens * dt * 0.25);
        }
        t += dt;
      }
      float g = 0.35, ct = dot(lDir, -rd), phase = (1.0 - g * g) / pow(1.0 + g * g - 2.0 * g * ct, 1.5) * 0.0796;
      gl_FragColor = vec4(c.rgb + lColor * intensity * phase * 12.566 * acc, c.a);
    }`,
};

// ---- depth of field: thin-lens circle of confusion, focused on the director's subject (the orbit target: the faces on
// the close and side shots, the podium on the wide one), a Vogel-disk gather that keeps sharp foreground from smearing
// over the background --------------------------------------------------------------------------------------------------
const DOF = {
  defines: { TAPS: 24 },
  uniforms: { aperture: { value: 0.012 }, maxCoc: { value: 18 }, focus: { value: 4 } },
  fragmentShader: `
    uniform sampler2D tDiffuse, tLin; uniform vec2 res; uniform float aperture, maxCoc, focus; varying vec2 vUv;
    ${NOISE}
    float coc(float z, float F) { return clamp(aperture * res.y * abs(1.0 / F - 1.0 / max(z, 0.05)), 0.0, maxCoc); }
    void main() {
      float F = focus;
      vec4 c = texture2D(tDiffuse, vUv); float z = texture2D(tLin, vUv).r, cc = coc(z, F);
      if (cc < 0.6) { gl_FragColor = c; return; }
      vec3 sum = c.rgb; float ws = 1.0, rot = ign(gl_FragCoord.xy) * 6.2832;
      for (int i = 0; i < TAPS; i++) {
        float r = sqrt((float(i) + 0.5) / float(TAPS)) * cc, a = float(i) * 2.39996 + rot;
        vec2 uv = vUv + vec2(cos(a), sin(a)) * r / res;
        float zs = textureLod(tLin, uv, 0.0).r, cs = coc(zs, F);
        float w = zs < z ? smoothstep(r - 1.0, r, cs) : smoothstep(r - 1.0, r, min(cs, cc) + 1.0);
        sum += textureLod(tDiffuse, uv, 0.0).rgb * w; ws += w;
      }
      gl_FragColor = vec4(sum / ws, c.a);
    }`,
};

// ---- motion blur on the palms: each hand's screen path this frame (a 180 degree shutter), blurred along it ------------
const MBLUR = {
  defines: { TAPS: 12 },
  uniforms: { palms: { value: [0, 1, 2, 3].map(() => new THREE.Vector4(-9, -9, -9, -9)) }, radii: { value: [0, 0, 0, 0] }, aspect: { value: 1 } },
  fragmentShader: `
    uniform sampler2D tDiffuse, tLin; uniform vec2 res; uniform vec4 palms[4]; uniform float radii[4], aspect; varying vec2 vUv;
    void main() {
      vec4 c = texture2D(tDiffuse, vUv); vec3 o = c.rgb; float best = 0.0;
      for (int k = 0; k < 4; k++) {
        vec2 a = palms[k].zw, b = palms[k].xy, v = b - a; float R = radii[k];
        if (R <= 0.0 || dot(v, v) < 1e-7) continue;
        vec2 pa = (vUv - a) * vec2(aspect, 1.0), ba = v * vec2(aspect, 1.0);
        float hh = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0), dd = length(pa - ba * hh);
        float m = 1.0 - smoothstep(0.55 * R, R, dd);
        if (m <= best) continue;
        vec3 s = vec3(0.0);
        for (int i = 0; i < TAPS; i++) s += textureLod(tDiffuse, vUv + v * (float(i) / float(TAPS - 1) - 0.5), 0.0).rgb;
        o = mix(c.rgb, s / float(TAPS), m); best = m;
      }
      gl_FragColor = vec4(o, c.a);
    }`,
};

// ---- the key light's beam (the high tier's cheap stand-in for the volume): a soft additive cone with drifting haze ----
function beam(from, to, radius) {
  const len = from.distanceTo(to);
  const geo = new THREE.CylinderGeometry(0.06, radius, len, 48, 24, true).translate(0, -len / 2, 0);
  const mat = keepAlpha(new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
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
      ${NOISE}
      void main() {
        float edge = pow(abs(dot(normalize(vN), normalize(vV))), 2.0);         // thick through the middle, gone at the rim
        float fall = smoothstep(0.0, 0.12, vAlong) * (1.0 - smoothstep(0.7, 1.0, vAlong));
        float haze = 0.55 + 0.45 * vnoise(vW * 2.2 + vec3(0.0, time * 0.08, time * 0.05));
        gl_FragColor = vec4(color * edge * fall * haze * 0.05, 1.0);
      }`,
  }));
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
  const mat = keepAlpha(new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
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
  }));
  const p = new THREE.Points(geo, mat);
  p.position.copy(from); p.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), to.clone().sub(from).normalize());
  p.frustumCulled = false; p.renderOrder = 11;
  return p;
}

// ---- the impact spray: sweat droplets thrown off the cheek, with gravity and drag, each a lit glint ---------------------
class Spray {
  constructor(scene, n = 600) {
    this.n = n; this.i = 0;
    this.p = new Float32Array(n * 3); this.v = new Float32Array(n * 3); this.life = new Float32Array(n).fill(9);
    const geo = new THREE.BufferGeometry();
    this.attr = new THREE.BufferAttribute(new Float32Array(n * 3), 3); this.attr.setUsage(THREE.DynamicDrawUsage);
    this.age = new THREE.BufferAttribute(new Float32Array(n), 1); this.age.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("position", this.attr); geo.setAttribute("age", this.age);
    const mat = keepAlpha(new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, uniforms: { scale: { value: 300 } },
      vertexShader: `
        attribute float age; uniform float scale; varying float vA;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); vA = clamp(1.0 - age / 0.7, 0.0, 1.0);
          gl_PointSize = scale * 0.035 / -mv.z * (0.6 + vA); gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `
        varying float vA;
        void main() { vec2 q = gl_PointCoord - 0.5; float d = length(q);
          float spec = smoothstep(0.18, 0.0, length(q + vec2(0.12, 0.12)));     // a glint on each droplet
          gl_FragColor = vec4(vec3(0.75, 0.8, 0.85) * (0.35 + 1.4 * spec) * vA * smoothstep(0.5, 0.2, d), 1.0); }`,
    }));
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
      if (this.p[3 * i + 2] < 0.004) { this.p[3 * i + 2] = 0.004; this.v[3 * i + 2] = 0; this.v[3 * i] *= 0.3; this.v[3 * i + 1] *= 0.3; this.attr.array[3 * i + 2] = 0.004; }   // drops land on the mat
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
    const mat = keepAlpha(new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, uniforms: { scale: { value: 300 } },
      vertexShader: `
        attribute float age; uniform float scale; varying float vA;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0); vA = clamp(1.0 - age / 1.8, 0.0, 1.0);
          gl_PointSize = scale * (0.06 + 0.12 * (1.0 - vA)) / -mv.z; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `
        varying float vA;
        void main() { float d = length(gl_PointCoord - 0.5);
          gl_FragColor = vec4(vec3(0.62, 0.58, 0.52), 0.32 * vA * smoothstep(0.5, 0.0, d)); }`,
    }), false);
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
      keepAlpha(new THREE.MeshBasicMaterial({ color: 0xffe08a, transparent: true, depthWrite: false })));
    ring.position.copy(pos); ring.lookAt(camera.position); ring.renderOrder = 13;
    this.scene.add(ring); this.live.push([ring, 0, size]);
  }
  tick(dt) {
    for (let i = this.live.length - 1; i >= 0; i--) {
      const f = this.live[i]; f[1] += dt;
      f[0].scale.setScalar(1 + f[1] * (10 + 8 * f[2]));
      f[0].material.color.setHex(0xffe08a).multiplyScalar(Math.max(0, 1 - f[1] / 0.45));   // additive: fade the colour
      if (f[1] > 0.45) { this.scene.remove(f[0]); f[0].geometry.dispose(); f[0].material.dispose(); this.live.splice(i, 1); }
    }
  }
}

// ---- the stage floor's planar reflection (cinema): the scene mirrored in the lacquered deck, rougher on the mat -------
function floorReflection(w, h, share) {
  const geo = new THREE.CircleGeometry(7, 96);
  const shader = {
    name: "PoSlapFloorReflection",
    uniforms: { color: { value: null }, tDiffuse: { value: null }, textureMatrix: { value: null }, res: { value: new THREE.Vector2(w, h) } },
    vertexShader: `
      uniform mat4 textureMatrix; varying vec4 vUv; varying vec3 vW;
      void main() { vUv = textureMatrix * vec4(position, 1.0); vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz;
        gl_Position = projectionMatrix * viewMatrix * w; }`,
    fragmentShader: `
      uniform sampler2D tDiffuse; uniform vec2 res; varying vec4 vUv; varying vec3 vW;
      ${NOISE}
      void main() {
        float r = length(vW.xy), mat = 1.0 - smoothstep(2.6, 2.8, r);            // the padded mat inside 2.7 m, the deck outside
        float rough = mix(2.0, 9.0, mat), k = mix(1.0, 0.45, mat);           // lacquered deck, satin vinyl mat
        vec2 uv = vUv.xy / vUv.w; vec3 s = vec3(0.0); float rot = ign(gl_FragCoord.xy) * 6.28;
        for (int i = 0; i < 8; i++) { float a = float(i) * 2.39996 + rot, rr = sqrt((float(i) + 0.5) / 8.0) * rough;
          s += textureLod(tDiffuse, uv + vec2(cos(a), sin(a)) * rr / res, 0.0).rgb; }
        vec3 V = normalize(cameraPosition - vW); float F = 0.04 + 0.96 * pow(1.0 - clamp(V.z, 0.0, 1.0), 5.0);
        float fade = 1.0 - smoothstep(5.5, 7.0, r);
        gl_FragColor = vec4(s / 8.0 * F * k * fade, 1.0);
      }`,
  };
  const refl = new Reflector(geo, { shader, textureWidth: Math.round(w * share), textureHeight: Math.round(h * share), clipBias: 0.003, multisample: 0 });
  keepAlpha(refl.material); refl.material.transparent = true; refl.material.depthWrite = false;
  refl.position.z = 0.003; refl.renderOrder = 1;
  refl.material.uniforms.res.value.set(Math.round(w * share), Math.round(h * share));
  return refl;
}

// ---- skin -----------------------------------------------------------------------------------------------------------
// The glTF's standard material with Unreal's skin parameters: wrapped light per channel (red travels furthest under the
// skin: pre-integrated subsurface), a warm rim where the surface turns away (peach fuzz), a micro-relief from the baked
// skin detail map (R: pores and fine wrinkles, G: sweat beads; tileable, Scripts/blender_skin_detail.py) sampled
// triplanar in the bind pose (it rides with the skin, it does not swim), a water lobe (GGX, roughness 0.12, F0 0.02) on
// the sweat that beads as the stamina drops, the cheek ripple and push (RipplePoint/Dir/Time/Amp, cm as in Unreal), the
// flush round FlushPoint and the grey of Tired. The skin writes alpha 0 when the SSS pass runs (skinFlag).
const SKINS = new Set();
let detailTex = null;
function skinDetail() {
  if (detailTex) return detailTex;
  detailTex = new THREE.TextureLoader().load("./assets/tex/skin_detail.png", t => { for (const u of SKINS) u.detailOn.value = u.detailWant; });
  detailTex.wrapS = detailTex.wrapT = THREE.RepeatWrapping; detailTex.colorSpace = THREE.NoColorSpace; detailTex.anisotropy = 4;
  return detailTex;
}
export function skin(std) {
  const m = std.clone();
  const u = {
    ripplePoint: { value: new THREE.Vector3(0, 0, -9) }, rippleDir: { value: new THREE.Vector3(1, 0, 0) },
    rippleTime: { value: 100 }, rippleAmp: { value: 0 },
    flushPoint: { value: new THREE.Vector3(0, 0, -9) }, flush: { value: 0 }, tired: { value: 0 },
    flushRadius: { value: 9 }, bruise: { value: 0 },   // cm round the hit; the cheek-wide redness the neck damage builds
    skinDetail: { value: skinDetail() }, detailOn: { value: 0 }, sweatOn: { value: 0 }, skinFlag: { value: 0 }, wetMin: { value: 0 },
    detailScale: { value: 22 }, // tiles per mesh unit (the GLBs are in metres: one tile ~4.5 cm)
  };
  u.detailWant = 1;
  m.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", `#include <common>
        uniform vec3 ripplePoint, rippleDir; uniform float rippleTime, rippleAmp; varying vec3 vSkinW, vBindP, vBindN;`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>
        vBindP = position; vBindN = normal;`)
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
        uniform vec3 flushPoint; uniform float flush, tired, flushRadius, bruise, detailOn, sweatOn, skinFlag, detailScale, wetMin;
        uniform sampler2D skinDetail; varying vec3 vSkinW, vBindP, vBindN;
        float gSweat = 0.0;
        vec2 skinTri() {                                                       // the detail map, triplanar in the bind pose
          vec3 bw = pow(abs(normalize(vBindN)), vec3(4.0)); bw /= dot(bw, vec3(1.0));
          vec3 p = vBindP * detailScale;
          return texture2D(skinDetail, p.yz).rg * bw.x + texture2D(skinDetail, p.zx).rg * bw.y + texture2D(skinDetail, p.xy).rg * bw.z;
        }
        vec3 skinBump(vec3 surf, vec3 n, float h, float fd) {                  // derivative bump (Mikkelsen), as three's bump map
          vec3 sx = normalize(dFdx(surf)), sy = normalize(dFdy(surf)), r1 = cross(sy, n), r2 = cross(n, sx);
          float det = dot(sx, r1) * fd; vec3 g = sign(det) * (dFdx(h) * r1 + dFdy(h) * r2);
          return normalize(abs(det) * n - g);
        }`)
      .replace("#include <color_fragment>", `#include <color_fragment>
        {
          float D = distance(vSkinW, flushPoint) * 100.0;
          float f = flush * clamp(1.0 - D / flushRadius, 0.0, 1.0) + bruise * 0.45 * clamp(1.0 - D / (2.2 * flushRadius), 0.0, 1.0);
          vec3 c = mix(diffuseColor.rgb, dot(diffuseColor.rgb, vec3(0.3, 0.59, 0.11)) * vec3(0.7, 0.72, 0.75), tired * 0.35);
          diffuseColor.rgb = mix(c, c * vec3(1.5, 0.5, 0.5), clamp(f, 0.0, 1.0));
        }`)
      .replace("#include <normal_fragment_maps>", `#include <normal_fragment_maps>
        if (detailOn > 0.5) {
          vec2 dt = skinTri();
          float wet = sweatOn * max(wetMin, 0.15 + 0.85 * tired);              // more sweat as the stamina goes
          float near = 1.0 - smoothstep(1.2, 4.0, length(vViewPosition));       // micro-relief only where it is resolved
          float h = (dt.r * 0.02 + wet * smoothstep(0.55, 0.9, dt.g) * 0.03) * near;
          normal = skinBump(-vViewPosition, normal, h, faceDirection);
          gSweat = wet * (0.25 + 0.75 * smoothstep(0.5, 0.85, dt.g));
          roughnessFactor = mix(roughnessFactor, 0.35, wet * 0.5);
        } else gSweat = sweatOn * max(wetMin, 0.15 + 0.85 * tired) * 0.4;`)
      // the wrap is the diffuse term's only: fed to the specular it lights GGX at grazing angles, where it divides by ~0
      .replace("#include <lights_physical_pars_fragment>", THREE.ShaderChunk.lights_physical_pars_fragment.replace(
        "reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );",
        `vec3 wrapK = vec3(0.45, 0.2, 0.12);                                  // how far each channel wraps past the terminator
        vec3 wrapped = clamp((dot(geometryNormal, directLight.direction) + wrapK) / (1.0 + wrapK), 0.0, 1.0) * directLight.color;
        reflectedLight.directDiffuse += wrapped * BRDF_Lambert( material.diffuseColor );
        if (gSweat > 0.0) {                                                    // the water on the skin: a sharp second lobe
          vec3 H = normalize(directLight.direction + geometryViewDir);
          float nh = saturate(dot(geometryNormal, H)), nv = saturate(dot(geometryNormal, geometryViewDir)), a = 0.12 * 0.12;
          reflectedLight.directSpecular += irradiance * gSweat * F_Schlick(vec3(0.02), 1.0, saturate(dot(geometryViewDir, H))) *
            V_GGX_SmithCorrelated(a, dotNL, nv) * D_GGX(a, nh);
        }`))
      .replace("#include <opaque_fragment>", `{                                                   // the fuzz rim, lit by what lights the skin
          float rim = pow(1.0 - saturate(dot(normal, geometryViewDir)), 4.0);
          outgoingLight += vec3(1.0, 0.82, 0.75) * rim * 0.35 * (reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + 0.02);
        }
        #include <opaque_fragment>
        gl_FragColor.a = 1.0 - skinFlag;`);
  };
  m.customProgramCacheKey = () => "poslap-skin-2";
  m.userData.skin = u;
  SKINS.add(u);
  return m;
}

// ---- the crowd (art.js tags each spectator: _person = a random id, _height = 0 at the feet .. 1 at the head) ----------
// each one bobs with the room's excitement (a roar after a stagger, a lift on every slap), sways a little when calm,
// and wears his own shirt and skin tone (the scan's palette is one colour per seat block)
const CROWDS = new Set();
export function crowdMaterial(std, unit = 1) {   // unit: mesh units per metre (a quantised GLB's node scale undoes them)
  const m = std.clone();
  const u = { time: { value: 0 }, excite: { value: 0.1 }, crowdOn: { value: 1 }, unit: { value: unit } };
  m.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", `#include <common>
        attribute float _person, _height; uniform float time, excite, crowdOn, unit; varying float vPerson, vHeight;`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>
        vPerson = _person; vHeight = _height;
        if (crowdOn > 0.5) {
          float ph = _person * 61.0, rate = 5.0 + 3.0 * fract(_person * 13.7);
          float hop = pow(max(0.0, sin(time * rate + ph)), 2.0) * excite * (0.05 + 0.08 * fract(_person * 3.1));
          float sway = sin(time * (0.6 + 0.5 * fract(_person * 7.7)) + ph) * 0.012 * _height;
          if (_height > 0.62) hop += excite * 0.04 * fract(_person * 5.3) * smoothstep(0.62, 0.9, _height);   // heads up
          transformed.y += hop * unit; transformed.x += sway * unit; transformed.z += sway * 0.6 * unit;
        }`);
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>
        uniform float crowdOn; varying float vPerson, vHeight;
        vec3 shirt(float id) {
          vec3 p[6]; p[0] = vec3(0.55, 0.08, 0.07); p[1] = vec3(0.08, 0.12, 0.35); p[2] = vec3(0.09, 0.09, 0.1);
          p[3] = vec3(0.65, 0.62, 0.58); p[4] = vec3(0.12, 0.3, 0.14); p[5] = vec3(0.6, 0.45, 0.1);
          int k = int(floor(fract(id * 9.17) * 6.0)); return p[k];
        }`)
      .replace("#include <color_fragment>", `#include <color_fragment>
        if (crowdOn > 0.5) {
          float torso = smoothstep(0.28, 0.36, vHeight) * (1.0 - smoothstep(0.66, 0.72, vHeight));
          float head = smoothstep(0.76, 0.82, vHeight);
          vec3 tone = mix(vec3(0.95, 0.72, 0.58), vec3(0.42, 0.27, 0.18), fract(vPerson * 4.31));
          diffuseColor.rgb = mix(diffuseColor.rgb, shirt(vPerson), torso * 0.7);
          diffuseColor.rgb = mix(diffuseColor.rgb, tone * 0.8, head * 0.5);
          diffuseColor.rgb *= 0.75 + 0.5 * fract(vPerson * 17.3);
        }`);
  };
  m.customProgramCacheKey = () => "poslap-crowd";
  CROWDS.add(u);
  return m;
}

// how much of the room's image light a material takes: a little on the arena (the stage light stays the light),
// more on the athletes (their skin and clothes catch reflections)
export function envLight(root, k) { root.traverse(o => { if (o.isMesh) for (const m of [].concat(o.material)) m.envMapIntensity = k; }); }

// ---- the pipeline -----------------------------------------------------------------------------------------------------
export class Look {
  constructor(renderer, scene, camera, key) {
    Object.assign(this, { renderer, scene, camera, key, tier: "low", auto: true, slow: 0, shake: 0, shakeAmp: 0, fps: 60, shakeOn: true });
    this.override = parseFx(new URLSearchParams(location.search).get("fx"));
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)");
    this.excite = 0.1; this.hands = null; this.handsPrev = null; this.size = new THREE.Vector2(1, 1);
    this.focusAt = null;   // the point the depth of field holds sharp (app.js: the orbit target); null: 4 m
    const pm = new THREE.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;   // speculars and reflections from a room
    scene.environmentIntensity = 1;   // per material: the arena keeps its dark, stage-lit look (envLight)
    pm.dispose();
    scene.fog = new THREE.FogExp2(0x0b0b0d, 0.055);
    const from = key.position.clone(), to = key.target.position.clone();
    this.beam = beam(from, to, 1.5); this.motes = motes(from, to, 1.2);
    scene.add(this.beam, this.motes);
    this.spray = new Spray(scene); this.dust = new Dust(scene); this.rings = new Rings(scene);
    const rt = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 0, depthTexture: new THREE.DepthTexture(1, 1) });
    this.composer = new EffectComposer(renderer, rt);
    this.composer.addPass(new RenderPass(scene, camera));
    this.depth = new DepthCopyPass(camera); this.composer.addPass(this.depth);
    this.sss = depthPass(SSS, this.depth.target); this.composer.addPass(this.sss);
    this.gtao = new GTAOPass(scene, camera, 1, 1);
    this.gtao.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.5, thickness: 1.2, scale: 1.0, samples: 16 });
    this.gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16 });
    this.composer.addPass(this.gtao);
    this.vol = depthPass(VOL, this.depth.target); this.composer.addPass(this.vol);
    this.dof = depthPass(DOF, this.depth.target); this.composer.addPass(this.dof);
    this.mblur = depthPass(MBLUR, this.depth.target); this.composer.addPass(this.mblur);
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.25, 0.5, 3.0);   // linear HDR: only real highlights bloom
    this.composer.addPass(this.bloom);
    this.lens = new ShaderPass(LensShader);   // in linear HDR, before the tone map: after it the frame would be encoded twice
    this.composer.addPass(this.lens);
    this.composer.addPass(new OutputPass());
    this.smaa = new SMAAPass(1, 1);
    this.composer.addPass(this.smaa);
    this.refl = null;
  }
  // the effects of tier t, with ?fx= overrides
  fxFor(t) { return Object.assign({}, TIER_FX[t], this.override); }
  setTier(t) {
    this.auto = !TIERS.includes(t);   // a named tier stays put; "auto" may step down
    if (this.auto) {
      const phone = matchMedia("(pointer: coarse)").matches, small = (navigator.deviceMemory || 8) <= 3 || (navigator.hardwareConcurrency || 8) <= 4;
      const mpx = innerWidth * innerHeight * Math.min(devicePixelRatio, 2) ** 2 / 1e6;
      t = phone ? (small ? "low" : "high") : mpx <= DEFAULT_TIER.smallMpx ? DEFAULT_TIER.small : DEFAULT_TIER.large;
    }
    this.tier = t;
    const f = this.fx = this.fxFor(t);
    this.renderer.setPixelRatio(Math.min(devicePixelRatio * f.ss, f.pr * f.ss));
    this.key.shadow.mapSize.set(f.shadow, f.shadow);
    this.key.shadow.map?.dispose(); this.key.shadow.map = null;
    this.beam.visible = !!f.beam && !f.vol; this.motes.visible = !!f.motes;
    this.gtao.enabled = f.gtao > 0; this.bloom.enabled = !!f.bloom; this.smaa.enabled = !!f.smaa;
    const setN = (pass, key, n) => { pass.enabled = n > 0; if (n > 0 && pass.material.defines[key] !== n) { pass.material.defines[key] = n; pass.material.needsUpdate = true; } };
    setN(this.sss, "TAPS", f.sss); setN(this.vol, "STEPS", f.vol); setN(this.dof, "TAPS", f.dof); this.mblur.enabled = !!f.mblur;
    this.depth.enabled = !!(f.sss || f.vol || f.dof || f.mblur);
    for (const u of SKINS) { u.detailWant = f.detail; u.detailOn.value = f.detail && detailTex?.image ? 1 : 0; u.sweatOn.value = f.sweat; u.skinFlag.value = f.sss > 0 ? 1 : 0; u.wetMin.value = f.wet || 0; }
    for (const u of CROWDS) u.crowdOn.value = f.crowd;
    if (this.refl) { this.scene.remove(this.refl); this.refl.dispose(); this.refl = null; }
    this.resize(innerWidth, innerHeight);
    if (f.refl > 0) {
      const pr = this.renderer.getPixelRatio();
      this.refl = floorReflection(Math.round(innerWidth * pr), Math.round(innerHeight * pr), f.refl);
      this.scene.add(this.refl);
    }
  }
  resize(w, h) {
    this.renderer.setSize(w, h, false);
    const pr = this.renderer.getPixelRatio(), W = Math.round(w * pr), H = Math.round(h * pr);
    this.size.set(W, H);
    this.composer.setPixelRatio(pr); this.composer.setSize(w, h);
    this.depth.setSize(W, H);
    if (this.fx?.gtao === 1) this.gtao.setSize(Math.round(W / 2), Math.round(H / 2));   // half-res AO on the high tier
    for (const p of [this.sss, this.vol, this.dof, this.mblur]) p.material.uniforms.res.value.set(W, H);
    this.mblur.material.uniforms.aspect.value = w / h;
    this.lens.uniforms.aspect.value = w / h;
    this.motes.material.uniforms.scale.value = this.spray.points.material.uniforms.scale.value =
      this.dust.points.material.uniforms.scale.value = h * pr * 0.5;
    if (this.refl) this.refl.getRenderTarget().setSize(Math.round(W * this.fx.refl), Math.round(H * this.fx.refl));
  }
  // the shake runs only when it is affordable and wanted: not on the low tier, not under 30 fps, not for reduced
  // motion, not when the viewer turned it off
  get canShake() { return this.shakeOn && !this.reduced.matches && this.tier !== "low" && this.fps >= 30; }
  // a strike: droplets off the cheek and a ring of light, both sized by the momentum (size 1 = a 3 kg x 9 m/s clean
  // slap), the lens's shock ring from the contact, the camera jolts with the palm's speed, the crowd comes up
  hit(pos, dir, speed, size = 1) {
    const p = new THREE.Vector3(...pos);
    this.spray.burst(p, dir, speed, size);
    this.rings.add(p, this.camera, size);
    const s = p.clone().project(this.camera);
    this.lens.uniforms.center.value.set(s.x * 0.5 + 0.5, s.y * 0.5 + 0.5);
    this.lens.uniforms.age.value = 0; this.lens.uniforms.amp.value = Math.min(1.5, Math.max(0, (speed - 4) / 6));
    this.shake = 0; this.shakeAmp = this.canShake ? Math.min(0.03, 0.002 * speed) : 0;
    this.excite = Math.min(1, this.excite + 0.35 + 0.25 * size);
  }
  knockdown(pos, k) { this.dust.puff(new THREE.Vector3(...pos), k); this.excite = 1; }
  // the hands' world positions this frame (flat [x, y, z] x n): the palm motion blur's paths
  track(hands) { this.handsPrev = this.hands; this.hands = hands.slice(); }
  frameUniforms(t, dt) {
    const cam = this.camera, f = this.fx, H = this.size.y;
    const pxPerM = H / (2 * Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2));
    for (const p of [this.sss, this.vol, this.dof, this.mblur]) p.material.uniforms.pxPerM.value = pxPerM;
    if (f.dof && this.focusAt) {   // the focus distance along the view axis, eased (a focus pull, not a jump)
      const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion), want = Math.max(0.3, this.focusAt.clone().sub(cam.position).dot(fwd));
      const u = this.dof.material.uniforms.focus; u.value += (want - u.value) * Math.min(1, dt * 6 || 1);
    }
    if (f.vol) {
      const u = this.vol.material.uniforms, k = this.key;
      u.projInv.value.copy(cam.projectionMatrixInverse); u.camWorld.value.copy(cam.matrixWorld); u.camPos.value.copy(cam.position);
      u.lPos.value.copy(k.position); u.lDir.value.copy(k.target.position).sub(k.position).normalize();
      u.cosOuter.value = Math.cos(k.angle); u.cosInner.value = Math.cos(k.angle * (1 - k.penumbra)); u.lRange.value = k.distance || 12;
      u.time.value = t;
      u.hasShadow.value = k.shadow.map ? 1 : 0;
      if (k.shadow.map) { u.tShadow.value = k.shadow.map.texture; u.shadowMat.value.copy(k.shadow.matrix); }
    }
    if (f.mblur) {
      const u = this.mblur.material.uniforms, a = this.hands, b = this.handsPrev, v = new THREE.Vector3(), w = new THREE.Vector3();
      for (let i = 0; i < 4; i++) {
        u.radii.value[i] = 0;
        if (!a || !b || dt <= 0 || 3 * i + 2 >= a.length) continue;
        v.set(a[3 * i], a[3 * i + 1], a[3 * i + 2]); w.set(b[3 * i], b[3 * i + 1], b[3 * i + 2]);
        const speed = v.distanceTo(w) / dt;
        if (speed < 2.5) continue;   // only a swing blurs
        const z = Math.max(0.2, v.clone().applyMatrix4(cam.matrixWorldInverse).z * -1);
        const half = w.clone().lerp(v, 0.5);   // a 180 degree shutter: the second half of the frame's path
        const sv = v.clone().project(cam), sh = half.project(cam);
        u.palms.value[i].set(sv.x * 0.5 + 0.5, sv.y * 0.5 + 0.5, sh.x * 0.5 + 0.5, sh.y * 0.5 + 0.5);
        u.radii.value[i] = Math.min(0.3, 0.11 * pxPerM / z / H) * Math.min(1, (speed - 2.5) / 3);
      }
    }
  }
  render(dt, fps) {
    const t = performance.now() / 1000;
    this.beam.material.uniforms.time.value = this.motes.material.uniforms.time.value = this.lens.uniforms.time.value = t;
    this.lens.uniforms.age.value += dt;
    this.spray.tick(dt); this.dust.tick(dt); this.rings.tick(dt);
    this.excite = 0.1 + (this.excite - 0.1) * Math.exp(-dt / 1.6);
    for (const u of CROWDS) { u.time.value = t; u.excite.value = this.excite; }
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
    if (this.tier === "low" && !this.override.post) {
      this.renderer.toneMappingExposure = 1.4;
      this.renderer.render(this.scene, this.camera);
    } else {
      this.camera.updateMatrixWorld();
      this.frameUniforms(t, dt);
      this.composer.render(dt);
    }
    if (jolt) this.camera.position.sub(jolt);
  }
}
