// The art: the Blender arena and the skinned athletes, moved bone by bone from MuJoCo bodies (drawing only).
// Port of Source/PoSlapUnreal/SlapAthleteVisual.cpp: bone names == body names; at rest (MuJoCo at qpos0, the T-pose
// the meshes were bound in) each bone's rotation is stored relative to its body, after lining the mesh frame up with
// the world (pelvis->head up, hand_r->hand_l left); every frame a bone takes its body's position and rotation x that.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { skin, envLight, crowdMaterial } from "./fx.js";

const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
const load = url => new Promise((ok, fail) => loader.load(url, ok, undefined, fail));

export async function loadArena(scene) {
  const gltf = await load("./assets/arena.glb");
  const root = new THREE.Group();
  root.rotation.x = Math.PI / 2;   // glTF is Y-up; the arena was modelled in MuJoCo's Z-up frame
  // the broadcast side (Scripts/arena_env.py SET_YAW): the dressing turned 180 degrees about the centre, so the camera
  // at -Y sees the set as designed (judges' table across, press row behind it); the foot boxes keep the physics frame
  gltf.scene.rotation.y = Math.PI;
  for (const n of [...gltf.scene.children]) {
    if (!/^FootBox/.test(n.name)) continue;
    n.position.set(-n.position.x, n.position.y, -n.position.z); n.rotation.y += Math.PI;   // undo the turn for these
  }
  root.add(gltf.scene); scene.add(root);
  gltf.scene.traverse(o => { if (o.isMesh) { o.receiveShadow = true; o.castShadow = /podium|pod/i.test(o.name); } });
  envLight(gltf.scene, 0.06);
  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse(o => {   // the crowd: one id and a feet-to-head height per spectator (fx.js crowdMaterial)
    if (!o.isMesh || !/crowd/i.test(o.material?.name || "")) return;
    tagCrowd(o.geometry); o.material = crowdMaterial(o.material, 1 / o.getWorldScale(new THREE.Vector3()).x);
  });
  return root;
}

// The crowd mesh is unwelded quads: weld by position, then merge the parts of one spectator (a head over a torso:
// centroids within 15 cm across); each gets a random id (_person) and each vertex its height in him (_height, 0..1)
function tagCrowd(geo) {
  const P = geo.attributes.position, A = P.array, N = P.count, idx = geo.index?.array;
  const par = new Int32Array(N); for (let i = 0; i < N; i++) par[i] = i;
  const f = i => { while (par[i] !== i) { par[i] = par[par[i]]; i = par[i]; } return i; };
  const u = (a, b) => { a = f(a); b = f(b); if (a !== b) par[b] = a; };
  if (idx) for (let i = 0; i < idx.length; i += 3) { u(idx[i], idx[i + 1]); u(idx[i], idx[i + 2]); }
  const at = new Map(), s = P.normalized ? 1 : 1000;   // the quantised positions are exact integers
  for (let i = 0; i < N; i++) {
    const k = (Math.round(A[3 * i] * s) + 65536) * 17179869184 + (Math.round(A[3 * i + 1] * s) + 65536) * 131072 + (Math.round(A[3 * i + 2] * s) + 65536);
    const j = at.get(k); if (j === undefined) at.set(k, i); else u(i, j);
  }
  const v = new THREE.Vector3(), comp = new Map();
  for (let i = 0; i < N; i++) {
    const r = f(i); let c = comp.get(r);
    v.fromBufferAttribute(P, i);
    if (!c) comp.set(r, c = { n: 0, x: 0, z: 0, lo: Infinity, hi: -Infinity, root: r });
    c.n++; c.x += v.x; c.z += v.z; c.lo = Math.min(c.lo, v.y); c.hi = Math.max(c.hi, v.y);
  }
  // the mesh's own units: 15 cm in them (the node's scale is 1/the quantisation scale)
  const box = new THREE.Box3().setFromBufferAttribute(P), span = Math.max(box.max.x - box.min.x, box.max.z - box.min.z);
  const cell = span / 200;   // the bowl is ~30 m across: ~15 cm
  const grid = new Map(), key = (a, b) => a * 100003 + b;
  for (const c of comp.values()) {
    c.x /= c.n; c.z /= c.n;
    const gx = Math.floor(c.x / cell), gz = Math.floor(c.z / cell);
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      const o = grid.get(key(gx + dx, gz + dz));
      if (o && Math.hypot(o.x - c.x, o.z - c.z) < cell) { u(o.root, c.root); }
    }
    grid.set(key(gx, gz), c);
  }
  const ppl = new Map();
  for (const c of comp.values()) {
    const r = f(c.root); let q = ppl.get(r);
    if (!q) ppl.set(r, q = { id: Math.random(), lo: Infinity, hi: -Infinity });
    q.lo = Math.min(q.lo, c.lo); q.hi = Math.max(q.hi, c.hi);
  }
  const id = new Float32Array(N), ht = new Float32Array(N);
  for (let i = 0; i < N; i++) { const q = ppl.get(f(i)); id[i] = q.id; ht[i] = (P.getY(i) - q.lo) / Math.max(1e-6, q.hi - q.lo); }
  geo.setAttribute("_person", new THREE.BufferAttribute(id, 1)); geo.setAttribute("_height", new THREE.BufferAttribute(ht, 1));
  geo.userData.people = ppl.size;
}

// frame from an "up" and a "left" direction (Unreal's FRotationMatrix::MakeFromXY)
function frame(up, left) {
  const x = up.clone().normalize(), z = new THREE.Vector3().crossVectors(x, left).normalize(), y = new THREE.Vector3().crossVectors(z, x);
  return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
}

export async function loadAthlete(scene, url, mj, m, prefix, tint) {
  const gltf = await load(url);
  scene.add(gltf.scene);
  let skinned = null;
  gltf.scene.traverse(o => { if (o.isSkinnedMesh) skinned = o; if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; } });
  const sk = skinned.skeleton;
  sk.pose(); gltf.scene.updateMatrixWorld(true);   // the bind (rest) pose
  if (tint) skinned.material.emissive = new THREE.Color(tint).multiplyScalar(0.12);   // a hint of the corner colour
  const skins = [];   // the skin shader (fx.js): subsurface wrap, sheen, the cheek ripple and flush
  gltf.scene.traverse(o => { if (o.isSkinnedMesh) { o.material = skin(o.material); skins.push(o.material.userData.skin); } });
  envLight(gltf.scene, 0.3);
  // the deformable cheek: morph targets CheekDent_L / CheekDent_R (the athlete's own left and right), if the GLB has
  // them; without them cheek() does nothing and the shader's ripple alone pushes the cheek in
  const dents = [];   // [influences, index of _L, index of _R]
  gltf.scene.traverse(o => {
    const dict = o.morphTargetDictionary;
    if (!dict || !o.morphTargetInfluences) return;
    const l = dict.CheekDent_L ?? -1, r = dict.CheekDent_R ?? -1;
    if (l >= 0 || r >= 0) dents.push([o.morphTargetInfluences, l, r]);
  });

  // MuJoCo at rest: qpos0, kinematics
  const d0 = new mj.MjData(m); mj.mj_resetData(m, d0); mj.mj_kinematics(m, d0);
  const bodyId = name => { try { const a = m.body(prefix + name), i = a.id; a.delete(); return i; } catch { return -1; } };
  const driven = [];   // { bone, body, offset, scale }
  const restPos = {}, mjPos = {};
  for (const bone of sk.bones) {
    const b = bodyId(bone.name);
    if (b < 0) continue;
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    bone.matrixWorld.decompose(p, q, s);
    const bq = new THREE.Quaternion(d0.xquat[4 * b + 1], d0.xquat[4 * b + 2], d0.xquat[4 * b + 3], d0.xquat[4 * b]);
    restPos[bone.name] = p; mjPos[bone.name] = new THREE.Vector3(d0.xpos[3 * b], d0.xpos[3 * b + 1], d0.xpos[3 * b + 2]);
    driven.push({ bone, body: b, restRot: q, bodyRest: bq, scale: s });
  }
  d0.delete();
  for (const n of ["pelvis", "head", "hand_l", "hand_r"]) if (!restPos[n]) throw new Error(`${url}: no bone/body ${prefix}${n}`);
  const F = P => frame(P.head.clone().sub(P.pelvis), P.hand_l.clone().sub(P.hand_r));
  const meshToWorld = F(mjPos).multiply(F(restPos).invert());
  for (const k of driven) k.offset = k.bodyRest.clone().invert().multiply(meshToWorld).multiply(k.restRot);
  // the skin detail map tiles in metres (a quantised GLB's positions are in its own units: the node's scale undoes them)
  const ws = skinned.getWorldScale(new THREE.Vector3()).x;
  for (const u of skins) u.detailScale.value = 22 * ws;
  const clip = clipData(skinned, driven);

  // parents before children, so each bone's local transform uses its parent's pose from this frame
  const order = [];
  const visit = o => { if (o.isBone) order.push(o); o.children.forEach(visit); };
  visit(gltf.scene);
  const byBone = new Map(driven.map(k => [k.bone, k]));
  const W = new THREE.Matrix4(), inv = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3();

  return {
    root: gltf.scene, skins, hasCheek: dents.length > 0, clip,
    cheek(left, right) { for (const [w, l, r] of dents) { if (l >= 0) w[l] = left; if (r >= 0) w[r] = right; } },
    // push(name, p, d): may move a driven bone's position (app.js keeps the drawn hand out of the face it hits)
    update(d, push) {
      for (const bone of order) {
        const k = byBone.get(bone), parentW = bone.parent.matrixWorld;
        if (k) {
          const b = k.body;
          q.set(d.xquat[4 * b + 1], d.xquat[4 * b + 2], d.xquat[4 * b + 3], d.xquat[4 * b]).multiply(k.offset);
          p.set(d.xpos[3 * b], d.xpos[3 * b + 1], d.xpos[3 * b + 2]);
          if (push) push(bone.name, p, d);
          W.compose(p, q, k.scale);
          bone.matrix.multiplyMatrices(inv.copy(parentW).invert(), W);
          bone.matrix.decompose(bone.position, bone.quaternion, bone.scale);
          bone.matrixWorld.copy(W);
        } else {
          bone.updateMatrix(); bone.matrixWorld.multiplyMatrices(parentW, bone.matrix);
        }
      }
    },
  };
}

// ---- the mesh clamp: the drawn palm stays out of the drawn face ------------------------------------------------------
// MuJoCo's palm sinks into the head sphere (1-7 cm), and the sphere does not cover the drawn jaw, so the old clamp
// (the hand box pushed off the sphere) left the drawn palm 3 cm into the jaw in most mirror-match slaps
// (Scripts/collision_mesh_clip.py, DOCS/20261103/collision_review.md). clipData keeps the hands', the head's and the
// neck's (the lower jaw is skinned to it) vertices (weight >= 0.5) in their MuJoCo body's frame, from the bind pose;
// clipPush (once against the head, once against the neck) moves the drawn hand out along
// the mean normal of the face vertices it is under by the deepest 5 % of its vertices (+2 mm), up to 5 times.
function clipData(skinned, driven) {
  const geo = skinned.geometry, si = geo.attributes.skinIndex, sw = geo.attributes.skinWeight;
  const pa = geo.attributes.position, na = geo.attributes.normal;
  if (!si || !sw || !na) return null;
  const bones = skinned.skeleton.bones, byName = new Map(driven.map(k => [k.bone.name, k]));
  const out = {}, Mw = skinned.matrixWorld, Nw = new THREE.Matrix3().getNormalMatrix(Mw);
  const v = new THREE.Vector3(), n = new THREE.Vector3(), lists = { hand_l: [], hand_r: [], head: [], neck: [] }, norms = { head: [], neck: [] };
  const rot = {};
  for (const name of Object.keys(lists)) {
    const k = byName.get(name); if (!k) return null;
    rot[name] = k.offset.clone().multiply(k.restRot.clone().invert());
    out[name] = { rest: k.bone.matrixWorld.clone() };
  }
  const restPos = name => new THREE.Vector3().setFromMatrixPosition(out[name].rest);
  const rp = Object.fromEntries(Object.keys(lists).map(k => [k, restPos(k)]));
  for (let i = 0; i < pa.count; i++) {
    let j = 0, w = sw.getX(i);
    for (let c = 1; c < 4; c++) { const x = sw.getComponent(i, c); if (x > w) { w = x; j = c; } }
    if (w < 0.5) continue;
    const name = bones[si.getComponent(i, j)]?.name;
    if (!(name in lists)) continue;
    v.fromBufferAttribute(pa, i).applyMatrix4(Mw).sub(rp[name]).applyQuaternion(rot[name]);
    lists[name].push(v.x, v.y, v.z);
    if (norms[name]) { n.fromBufferAttribute(na, i).applyMatrix3(Nw).normalize().applyQuaternion(rot[name]); norms[name].push(n.x, n.y, n.z); }
  }
  for (const name of Object.keys(lists)) out[name] = { local: new Float32Array(lists[name]) };
  for (const k of ["head", "neck"]) {   // the face: the head-skinned and the neck-skinned (the lower jaw) vertices
    const H = out[k]; H.nrm = new Float32Array(norms[k]);
    const c = new THREE.Vector3(); for (let i = 0; i < H.local.length; i += 3) c.add(v.set(H.local[i], H.local[i + 1], H.local[i + 2]));
    c.divideScalar(Math.max(1, H.local.length / 3));
    let r = 0; for (let i = 0; i < H.local.length; i += 3) r = Math.max(r, c.distanceTo(v.set(H.local[i], H.local[i + 1], H.local[i + 2])));
    H.c = c; H.r = r;
  }
  for (const name of ["hand_l", "hand_r"]) {
    let m = 0; const L = out[name].local; for (let i = 0; i < L.length; i += 3) m = Math.max(m, Math.hypot(L[i], L[i + 1], L[i + 2]));
    out[name].reach = m;
  }
  return out;
}

// hand: clip[hand_x] of the striker, head: clip.head of the other; hb / kb their MuJoCo bodies; p the hand bone's
// position this frame (moved in place). Returns the push applied (m) or 0.
const _q = new Float32Array(3), _dep = [], _nrm = [];
export function clipPush(hand, head, d, hb, kb, p) {
  const X = d.xmat, P = d.xpos, R = 9 * hb, K = 9 * kb;
  const hx = P[3 * kb], hy = P[3 * kb + 1], hz = P[3 * kb + 2];
  const cx = hx + X[K] * head.c.x + X[K + 1] * head.c.y + X[K + 2] * head.c.z;
  const cy = hy + X[K + 3] * head.c.x + X[K + 4] * head.c.y + X[K + 5] * head.c.z;
  const cz = hz + X[K + 6] * head.c.x + X[K + 7] * head.c.y + X[K + 8] * head.c.z;
  if (Math.hypot(p.x - cx, p.y - cy, p.z - cz) > head.r + hand.reach + 0.01) return 0;
  const L = hand.local, F = head.local, N = head.nrm, nv = L.length / 3, nf = F.length / 3, r2 = (head.r + 0.02) ** 2;
  let px = 0, py = 0, pz = 0, total = 0;
  for (let it = 0; it < 5; it++) {
    _dep.length = 0; _nrm.length = 0;
    for (let i = 0; i < nv; i++) {
      const lx = L[3 * i], ly = L[3 * i + 1], lz = L[3 * i + 2];
      const wx = p.x + px + X[R] * lx + X[R + 1] * ly + X[R + 2] * lz - hx;
      const wy = p.y + py + X[R + 3] * lx + X[R + 4] * ly + X[R + 5] * lz - hy;
      const wz = p.z + pz + X[R + 6] * lx + X[R + 7] * ly + X[R + 8] * lz - hz;
      const qx = X[K] * wx + X[K + 3] * wy + X[K + 6] * wz, qy = X[K + 1] * wx + X[K + 4] * wy + X[K + 7] * wz, qz = X[K + 2] * wx + X[K + 5] * wy + X[K + 8] * wz;
      if ((qx - head.c.x) ** 2 + (qy - head.c.y) ** 2 + (qz - head.c.z) ** 2 > r2) continue;
      let best = Infinity, bj = 0;
      for (let j = 0; j < nf; j++) {
        const dx = F[3 * j] - qx, dy = F[3 * j + 1] - qy, dz = F[3 * j + 2] - qz, dd = dx * dx + dy * dy + dz * dz;
        if (dd < best) { best = dd; bj = j; }
      }
      if (best > 0.0016) continue;   // 4 cm from the skin: not at the face
      const dep = (F[3 * bj] - qx) * N[3 * bj] + (F[3 * bj + 1] - qy) * N[3 * bj + 1] + (F[3 * bj + 2] - qz) * N[3 * bj + 2];
      if (dep > 0) { _dep.push(dep); _nrm.push(bj); }
    }
    if (_dep.length < Math.max(1, nv / 100)) break;
    let nx = 0, ny = 0, nz = 0;
    for (const j of _nrm) { nx += N[3 * j]; ny += N[3 * j + 1]; nz += N[3 * j + 2]; }
    const nl = Math.hypot(nx, ny, nz) || 1; nx /= nl; ny /= nl; nz /= nl;
    _dep.sort((a, b) => a - b);
    const k = _dep[Math.min(_dep.length - 1, Math.floor(0.95 * _dep.length))] + 0.002;
    px += (X[K] * nx + X[K + 1] * ny + X[K + 2] * nz) * k; py += (X[K + 3] * nx + X[K + 4] * ny + X[K + 5] * nz) * k; pz += (X[K + 6] * nx + X[K + 7] * ny + X[K + 8] * nz) * k;
    total += k;
  }
  if (total > 0) { p.x += px; p.y += py; p.z += pz; }
  return total;
}
