// The contact probe: MuJoCo's own contact list, read once per drawn frame for the display and the sound (never written
// back). Each frame it groups the contacts by geom pair and gives
//   impacts: a pair that was apart and now touches (a footfall, a body on the mat, a palm on the podium, a body bump),
//            with the approach speed along the contact normal and the energy behind it (1/2 m_eff vn^2)
//   slides:  per athlete, the planted sole that slides fastest (shear speed across the contact plane, normal force)
//   marks:   where the wear decals go (a skidding sole's rubber, a palm on the podium, a body on the floor)
//   heads:   both heads' positions (the voices' place in the binaural mix)
// The normal force is MuJoCo's: efc_force at the contact's efc_address, decoded from the pyramid (the default cone:
// f_n = the sum of its 2(dim-1) edges) or read straight for an elliptic cone. The point's velocity is the geom's,
// differenced across the frame's physics time (d.time), so it is the same at any frame rate and zero while paused.
const MAT_RADIUS = 2.7;   // m: the padded mat inside, the lacquered stage deck outside (audio.js MAT_RADIUS)
// effective mass behind each kind of part on an impact (kg per kg of the part's own body): a footfall carries the
// leg and some of the trunk, a palm on the podium the forearm, a falling trunk most of itself
const M_EFF = { sole: 8, hand: 2.5, head: 1.5, body: 2 };

export class ContactProbe {
  constructor(m, match) {
    this.m = m; this.match = match;
    this.kind = new Map();   // athlete geom -> { side, kind }
    match.sides.forEach((s, si) => {
      for (const g of s.geoms) {
        const kind = s.feet.includes(g) ? "sole" : s.hands.includes(g) ? "hand" : g === s.head ? "head" : "body";
        this.kind.set(g, { side: si, kind, mass: m.body_mass[m.geom_bodyid[g]] * M_EFF[kind] });
      }
    });
    this.elliptic = m.opt.cone === 1;
    this.prevPos = null; this.prevTime = -1;
    this.live = new Map();   // pair key -> seconds since it last touched (re-arms after 60 ms apart)
    this.out = { impacts: [], slides: [null, null], marks: [], heads: [[0, 0, 1.6], [0, 0, 1.6]], dt: 0 };
  }
  // dtWall: the frame's wall time, used only when the physics time does not move (the live second screen)
  read(d, dtWall = 0) {
    const M = this.match, out = this.out, P = d.geom_xpos;
    out.impacts.length = 0; out.marks.length = 0; out.slides[0] = out.slides[1] = null;
    M.sides.forEach((s, i) => { const h = out.heads[i]; h[0] = P[3 * s.head]; h[1] = P[3 * s.head + 1]; h[2] = P[3 * s.head + 2]; });
    let dt = d.time - this.prevTime;
    if (!(dt > 0) || dt > 0.5) dt = d.time === this.prevTime ? dtWall : 0;   // a reset (time went back) or a paused frame
    this.prevTime = d.time;
    if (!this.prevPos || this.prevPos.length !== P.length) { this.prevPos = Float64Array.from(P); return out; }
    out.dt = dt;
    if (dt <= 0) { this.prevPos.set(P); return out; }
    const Q = this.prevPos, F = d.efc_force, cs = d.contact, n = d.ncon, pairs = new Map();
    for (let i = 0; i < n; i++) {
      const c = cs.get(i), g1 = c.geom1, g2 = c.geom2, k1 = this.kind.get(g1), k2 = this.kind.get(g2);
      if (!k1 && !k2) { c.delete(); continue; }
      const key = Math.min(g1, g2) * 65536 + Math.max(g1, g2);
      let fn = 0;
      if (c.efc_address >= 0) {
        if (this.elliptic || c.dim === 1) fn = F[c.efc_address];
        else for (let e = 0; e < 2 * (c.dim - 1); e++) fn += F[c.efc_address + e];
      }
      const fr = c.frame, pos = c.pos;
      let p = pairs.get(key);
      if (!p) pairs.set(key, p = { g1, g2, k1, k2, n: 0, fn: 0, x: 0, y: 0, z: 0, nx: fr[0], ny: fr[1], nz: fr[2] });
      p.n++; p.fn += Math.max(0, fn); p.x += pos[0]; p.y += pos[1]; p.z += pos[2];
      c.delete();
    }
    cs.delete();
    const vel = (g, o) => { o[0] = (P[3 * g] - Q[3 * g]) / dt; o[1] = (P[3 * g + 1] - Q[3 * g + 1]) / dt; o[2] = (P[3 * g + 2] - Q[3 * g + 2]) / dt; return o; };
    const va = [0, 0, 0], vb = [0, 0, 0];
    for (const [key, p] of pairs) {
      p.x /= p.n; p.y /= p.n; p.z /= p.n;
      const A = p.k1 ? p.g1 : p.g2, B = A === p.g1 ? p.g2 : p.g1, kA = this.kind.get(A), kB = this.kind.get(B);
      vel(A, va); if (kB) vel(B, vb); else vb[0] = vb[1] = vb[2] = 0;   // a surface does not move
      const rx = va[0] - vb[0], ry = va[1] - vb[1], rz = va[2] - vb[2];
      const vn = Math.abs(rx * p.nx + ry * p.ny + rz * p.nz);
      const tx = rx - (rx * p.nx + ry * p.ny + rz * p.nz) * p.nx, ty = ry - (rx * p.nx + ry * p.ny + rz * p.nz) * p.ny;
      const tz = rz - (rx * p.nx + ry * p.ny + rz * p.nz) * p.nz, vt = Math.hypot(tx, ty, tz);
      const surf = B === M.floor ? (Math.hypot(p.x, p.y) < MAT_RADIUS ? "mat" : "deck") : B === M.podium ? "podium" : kB ? "body" : "other";
      const since = this.live.get(key);
      this.live.set(key, 0);
      if (since === undefined || since > 0.06) {   // a new touch: an impact
        const mass = kB ? Math.min(kA.mass, kB.mass) : kA.mass;
        out.impacts.push({ side: kA.side, part: kA.kind, surf, other: kB ? kB.kind : null, pos: [p.x, p.y, p.z], vn, vt, fn: p.fn,
          energy: 0.5 * mass * vn * vn, mass });
        if (kA.kind !== "sole" && (surf === "mat" || surf === "deck")) out.marks.push({ x: p.x, y: p.y, kind: 2, s: Math.min(1, 0.2 + vn * 0.4), r: 0.12 });
      }
      if (kA.kind === "sole" && (surf === "mat" || surf === "deck")) {
        const sl = out.slides[kA.side];
        if (!sl || vt > sl.vt) out.slides[kA.side] = { side: kA.side, surf, vt, fn: p.fn, pos: [p.x, p.y, p.z] };
        if (vt > 0.12) {   // a skid lays rubber along its path this frame (one splat per cm)
          const steps = Math.min(12, Math.ceil(vt * dt / 0.01)), s = Math.min(1, vt * 0.6) * Math.min(1, p.fn / 300 + 0.2);
          for (let j = 0; j < steps; j++) { const f = j / steps; out.marks.push({ x: p.x - tx * dt * f, y: p.y - ty * dt * f, kind: 0, s, r: 0.05 }); }
        }
      }
      if (kA.kind === "hand" && surf === "podium" && p.z > 0.9 && p.fn > 5) out.marks.push({ x: p.x, y: p.y, kind: 1, s: Math.min(1, p.fn / 200) * dt * 4, r: 0.06 });
    }
    for (const [key, t] of this.live) {   // pairs not touching this frame age; long-gone ones are forgotten
      if (pairs.has(key)) continue;
      if (t > 1) this.live.delete(key); else this.live.set(key, t + dt);
    }
    this.prevPos.set(P);
    return out;
  }
}
