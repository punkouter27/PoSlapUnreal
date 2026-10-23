// Loads the Grandpa-vs-Kim arena, both corners' fingerprints, cards and brains; shared by the page and the Node check.
import { Match, OBS } from "./sim.js";

export const CORNERS = [
  { prefix: "red_", char: "grandpa", brain: "grandpa_L2_lean10_wip" },
  { prefix: "blue_", char: "kim", brain: "kim_final" },
];

export async function loadMatch(mj, ort, get, emit) {
  const m = mj.MjModel.from_xml_string(await get("training/models/grandpa_vs_kim_arena.xml", "text"));
  const fps = await Promise.all(CORNERS.map(c => get(`training/models/${c.char}_fingerprint.json`, "json")));
  const o = fps[0].option;   // Unreal's options (the red corner's fingerprint): the XML drops solver and iterations
  m.opt.timestep = o.timestep; m.opt.integrator = o.integrator; m.opt.solver = o.solver; m.opt.iterations = o.iterations;
  m.opt.gravity.set(o.gravity);
  const d = new mj.MjData(m);
  const corners = await Promise.all(CORNERS.map(async (c, i) => {
    const s = await ort.InferenceSession.create(await get(`training/models/brains/${c.brain}.onnx`, "bytes"));
    const n = s.inputNames[0], out = s.outputNames[0];
    const policy = async obs => (await s.run({ [n]: new ort.Tensor("float32", obs, [1, OBS]) }))[out].data;
    return { ...c, fp: fps[i], card: await get(`training/characters/${c.char}.json`, "json"), policy };
  }));
  return { m, d, match: new Match(mj, m, d, corners, emit) };
}
