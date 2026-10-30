// The art: the Blender arena and the skinned athletes, moved bone by bone from MuJoCo bodies (drawing only).
// Port of Source/PoSlapUnreal/SlapAthleteVisual.cpp: bone names == body names; at rest (MuJoCo at qpos0, the T-pose
// the meshes were bound in) each bone's rotation is stored relative to its body, after lining the mesh frame up with
// the world (pelvis->head up, hand_r->hand_l left); every frame a bone takes its body's position and rotation x that.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { skin, envLight } from "./fx.js";

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
  return root;
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

  // parents before children, so each bone's local transform uses its parent's pose from this frame
  const order = [];
  const visit = o => { if (o.isBone) order.push(o); o.children.forEach(visit); };
  visit(gltf.scene);
  const byBone = new Map(driven.map(k => [k.bone, k]));
  const W = new THREE.Matrix4(), inv = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3();

  return {
    root: gltf.scene, skins,
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
