// Loads one matchup's arena, both corners' fingerprints, cards and brains; shared by the page and the Node check.
import { Match, OBS } from "./sim.js";

// The L4 brains sparred only against their own double; Grandpa vs Kim was never trained together (2026-10-24: on
// Kim's 4 cm lower podium Grandpa's L4 brain fell at 1-2 s every turn), so it keeps the older brains that guard.
export const MATCHUPS = {
  gg: { name: "Grandpa vs Grandpa", xml: "grandpa_vs_grandpa_arena.xml", defenderRole: -1,
        corners: [{ prefix: "red_", char: "grandpa", brain: "grandpa_L4" }, { prefix: "blue_", char: "grandpa", brain: "grandpa_L4" }] },
  kk: { name: "Kim vs Kim", xml: "kim_vs_kim_arena.xml", defenderRole: -1,
        corners: [{ prefix: "red_", char: "kim", brain: "kim_L4" }, { prefix: "blue_", char: "kim", brain: "kim_L4" }] },
  gk: { name: "Grandpa vs Kim", xml: "grandpa_vs_kim_arena.xml", defenderRole: -1,
        corners: [{ prefix: "red_", char: "grandpa", brain: "grandpa_final_vs_kim" }, { prefix: "blue_", char: "kim", brain: "kim_final_vs_grandpa" }] },
};

export async function loadMatch(mj, ort, get, emit, key = "gg") {
  const mu = MATCHUPS[key];
  const m = mj.MjModel.from_xml_string(await get(`training/models/${mu.xml}`, "text"));
  const fps = await Promise.all(mu.corners.map(c => get(`training/models/${c.char}_fingerprint.json`, "json")));
  const o = fps[0].option;   // Unreal's options (the red corner's fingerprint): the XML drops solver and iterations
  m.opt.timestep = o.timestep; m.opt.integrator = o.integrator; m.opt.solver = o.solver; m.opt.iterations = o.iterations;
  m.opt.gravity.set(o.gravity);
  const d = new mj.MjData(m);
  const corners = await Promise.all(mu.corners.map(async (c, i) => {
    const s = await ort.InferenceSession.create(await get(`training/models/brains/${c.brain}.onnx`, "bytes"));
    const n = s.inputNames[0], out = s.outputNames[0];
    const policy = async obs => (await s.run({ [n]: new ort.Tensor("float32", obs, [1, OBS]) }))[out].data;
    return { ...c, fp: fps[i], card: await get(`training/characters/${c.char}.json`, "json"), policy };
  }));
  return { m, d, matchup: mu, match: new Match(mj, m, d, corners, emit, mu.defenderRole) };
}
