"""Full Power Slap match in CPU MuJoCo (L5): the rules Unreal's USlapController plays, step for step.

    .venv/Scripts/python.exe match.py play models/brains/grandpa_L4.onnx [--first 0] [--mjb ../Saved/URLab/scene_compiled.mjb]
    .venv/Scripts/python.exe match.py exam models/brains/grandpa_L4.onnx --matches 100        (L5: no sim blow-ups)
    .venv/Scripts/python.exe match.py compare models/brains/grandpa_L4.onnx <unreal match .jsonl> --mjb ...  (same events)
    --blue <onnx>: blue's own brain (red plays the first one); tournament.py plays brains against each other this way.

Rules (v1): a coin toss picks who strikes first; 3 rounds, each athlete strikes once per round, same order every round.
A turn starts with both athletes reset to the stand pose in their foot boxes. The striker (role +1) has 30 s; the first
touch of the striker's body on the defender's head is the strike; the turn ends 5 s after it (recovery window).
Knockdown (Blueprint 9) = any non-sole part on floor or podium (the defender's palms may brace on the podium): the
defender down after the strike = KO, match over.
Fouls (-1 point on every card, that round): striker's first touch not with the palm (clubbing), no strike in 30 s,
striker knocked down, defender knocked down before the strike, either athlete's feet out of the foot box.
Judges: 3 cards, 10-point must. A strike's effect = palm speed (m/s) + 5 x peak defender head speed (m/s) + 10 if
the defender staggered (COM more than 5 cm off its pre-strike spot at the end of the window). The defender scores for
absorbing a clean strike he survived: 10 if he did not stagger + (10 - 5 x head speed, at least 0). Each judge weighs
(speed, head, stagger, defence) differently: (2,1,1,1), (1,2,1,1), (1,1,2,2). Per round: the bigger total gets 10, the
other 9; 10-8 when the winner staggered him and has at least twice the total; 10-10 within 5 %.
Fatigue (character card, BRAIN_IO 128-129): every turn struck costs the striker stamina_per_strike (joint speed x stamina,
floor 0.7); a defender who absorbs a strike loses neck_damage_per_snap per m/s of head snap from his neck motors' strength
(floor 0.5). Memory (BRAIN_IO 126-127): the defender remembers that strike's touch time and palm speed for his next turns.
Both reset each match; a "condition" event after every turn logs them.
Logs: Logs/Gameplay/<YYMMDD>/match_<HHMMSS>_<first>.jsonl (AGENTS.md): events, turn/round summaries, and every 5th
brain step (10 Hz) both athletes' pelvis, head and actions.
"""
import argparse, json, math, re, sys, time
from pathlib import Path

import mujoco
import numpy as np

from brain_cpu import MIN_NECK, MIN_STAMINA, Athlete, card, onnx_policy, stand

HERE = Path(__file__).parent
ROUNDS, STRIKE_CLOCK, RECOVERY = 3, 30.0, 5.0
BOX = (0.20, 0.25)
WEIGHTS = ((2, 1, 1, 1), (1, 2, 1, 1), (1, 1, 2, 2))   # speed, head, stagger, defence
KICK_STEPS = 2      # the impact impulse lasts 10 ms (as envs/slap.py's scripted slap)
DEFENDER_ROLE = -1.0   # brains from L3 on defend with the striker's head and hand; earlier ones guard (0)


def dist(a, b):   # spelled out so Unreal's C++ rounds identically (np.linalg.norm may sum in another order)
    return math.sqrt(sum((float(x) - float(y)) * (float(x) - float(y)) for x, y in zip(a, b)))


def load(mjb=None, character="grandpa"):
    fp = json.loads((HERE / "models" / f"{character}_fingerprint.json").read_text())
    if mjb:   # the model Unreal runs (URLab's compiled .mjb), with our options (URLab drops a child model's <option>)
        m = mujoco.MjModel.from_binary_path(str(mjb))
        o = fp["option"]
        m.opt.timestep, m.opt.integrator, m.opt.solver, m.opt.iterations = o["timestep"], o["integrator"], o["solver"], o["iterations"]
        m.opt.gravity[:] = o["gravity"]
    else:
        m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{character}_vs_{character}_arena.xml"))
    pre = next(re.sub(r"red_pelvis$", "", m.body(i).name) for i in range(m.nbody) if m.body(i).name.endswith("red_pelvis"))
    return m, fp, (pre + "red_", pre + "blue_")


class Side:
    """One athlete in the arena: brain I/O (brain_cpu.Athlete) plus the geoms the referee watches."""

    def __init__(self, m, fp, prefix, policy):
        self.a, self.policy, self.card = Athlete(m, fp, prefix), policy, card(fp)
        self.fr0 = m.actuator_forcerange[self.a.neck].copy()
        self.name = prefix.split("_")[-2] if prefix.count("_") >= 1 else prefix
        g = lambda n: m.geom(prefix + n).id
        self.head, self.hand, self.feet = g("head"), g("hand_r"), (g("foot_l"), g("foot_r"))
        self.hands = (g("hand_l"), g("hand_r"))
        self.head_body = m.geom_bodyid[self.head]
        self.geoms = {i for i in range(m.ngeom) if m.body_rootid[m.geom_bodyid[i]] == self.a.pelvis}


class Match:
    def __init__(self, m, fp, prefixes, policies, first, log=None):
        self.m, self.fp, self.first, self.log = m, fp, first, log
        self.d = mujoco.MjData(m)
        self.sides = [Side(m, fp, p, pol) for p, pol in zip(prefixes, policies)]
        self.surface = {m.geom(i).id for i in range(m.ngeom) if m.geom(i).name.endswith(("floor", "podium"))}
        self.podium = next(i for i in range(m.ngeom) if m.geom(i).name.endswith("podium"))
        self.blowup = False

    def emit(self, **k):
        if self.log:
            self.log.write(json.dumps(k) + "\n")

    def down(self, side, defender=False):
        """Blueprint 9: a non-foot geom of this athlete on floor or podium (from the last step's contacts); the defender's
        palms may rest on the podium (his brace, 2026-10-22)."""
        for c in self.d.contact[:self.d.ncon]:
            g = {c.geom[0], c.geom[1]}
            if g & self.surface:
                o = (g - self.surface)
                if o and (x := o.pop()) in side.geoms and x not in side.feet:
                    if defender and self.podium in g and x in side.hands:
                        continue
                    return self.m.geom(x).name
        return None

    def turn(self, rnd, s):
        """One turn, striker index s. Returns the turn record (touch, fouls, KO, effect parts)."""
        m, d = self.m, self.d
        S, D = self.sides[s], self.sides[1 - s]
        stand(m, d, self.fp)
        for x in self.sides:
            x.a.goal, x.a.prev, x.a.hist = x.a.stand.copy(), np.zeros(len(x.a.order), dtype=np.float32), []
            m.actuator_forcerange[x.a.neck] = x.fr0 * x.a.neck_strength   # damage: a weaker neck
        mujoco.mj_forward(m, d)
        feet0 = {id(x): np.array([d.geom_xpos[f][:2] for f in x.feet]) for x in self.sides}
        hand_prev = d.geom_xpos[S.hand].copy()
        head_prev = d.geom_xpos[D.head].copy()
        rec = dict(round=rnd, striker=S.name, touch=None, part=None, palm=False, speed=0.0, head_peak=0.0, stagger=False,
                   fouls=[], ko=False, end=None)
        touch_k, com0 = None, None
        kick = np.zeros(6)   # the slap's impact on the defender's head: force, torque for KICK_STEPS physics steps
        self.emit(type="turn_start", round=rnd, striker=S.name, defender=D.name)
        k = 0
        while True:
            t = k * m.opt.timestep
            if k % 4 == 0:   # the referee looks every brain step, before the brains decide (as Unreal)
                for x in (D, S):
                    part = self.down(x, x is D)
                    if part:
                        self.emit(type="knockdown", t=round(t, 3), athlete=x.name, part=re.sub(r"^.*?(red|blue)_", "", part))
                        if x is D and touch_k is not None:
                            rec["ko"], rec["end"] = True, "KO"
                        else:
                            rec["fouls"].append((x.name, "knocked down" if x is S else "down before the strike"))
                            rec["end"] = "knockdown"
                        return self.finish(rec, S, D, com0)
                for x in self.sides:
                    off = np.abs(np.array([d.geom_xpos[f][:2] for f in x.feet]) - feet0[id(x)])
                    if (off[:, 0] > BOX[0]).any() or (off[:, 1] > BOX[1]).any():
                        rec["fouls"].append((x.name, "out of the box"))
                        rec["end"] = "out of the box"
                        self.emit(type="foul", t=round(t, 3), athlete=x.name, why="out of the box")
                        return self.finish(rec, S, D, com0)
            if touch_k is None and t >= STRIKE_CLOCK:
                rec["fouls"].append((S.name, "no strike in 30 s"))
                rec["end"] = "clock"
                return self.finish(rec, S, D, com0)
            if touch_k is not None and (k - touch_k) * m.opt.timestep >= RECOVERY:
                rec["end"] = "recovered"
                return self.finish(rec, S, D, com0)
            if k % 4 == 0:
                mujoco.mj_kinematics(m, d)
                clock = 1.0 - t / STRIKE_CLOCK
                hs, hd = d.geom_xpos[S.head].copy(), d.geom_xpos[D.head].copy()
                S.a.decide(S.policy(S.a.obs(d, 1.0, clock, hd)))
                if DEFENDER_ROLE < 0:
                    D.a.decide(D.policy(D.a.obs(d, DEFENDER_ROLE, clock, hs, d.geom_xpos[S.hand].copy())))
                else:
                    D.a.decide(D.policy(D.a.obs(d)))
                if k % 20 == 0:
                    self.emit(type="frame", t=round(t, 3), **{x.name: dict(
                        pelvis=np.round(d.xpos[x.a.pelvis], 3).tolist(), head=np.round(d.geom_xpos[x.head], 3).tolist(),
                        action=np.round(x.a.prev, 2).tolist()) for x in self.sides})
            for x in self.sides:
                d.ctrl[x.a.act] = x.a.goal
            d.xfrc_applied[D.head_body] = kick if touch_k is not None and k - touch_k < KICK_STEPS else 0.0
            mujoco.mj_step(m, d)
            k += 1
            if not np.isfinite(d.qpos).all() or np.abs(d.qvel).max() > 1e3:
                self.blowup = True
                rec["end"] = "blow-up"
                self.emit(type="blowup", t=round(k * m.opt.timestep, 3))
                return rec
            hand = d.geom_xpos[S.hand].copy()
            speed = dist(hand, hand_prev) / m.opt.timestep
            vel = (hand - hand_prev) / m.opt.timestep
            hand_prev = hand
            head = d.geom_xpos[D.head].copy()
            if touch_k is not None:
                rec["head_peak"] = max(rec["head_peak"], dist(head, head_prev) / m.opt.timestep)
            head_prev = head
            if touch_k is None:
                for c in d.contact[:d.ncon]:
                    if D.head in (c.geom[0], c.geom[1]):
                        other = c.geom[1] if c.geom[0] == D.head else c.geom[0]
                        if other in S.geoms:
                            sign = 1.0 if c.geom[0] == other else -1.0   # normal from the striker's part to the head
                            z = d.geom_xmat[S.hand]                      # palm = the hand box's -z axis (column 2)
                            palm = bool(other == S.hand and -sign * (c.frame[0] * z[2] + c.frame[1] * z[5] + c.frame[2] * z[8]) > 0.5)   # numpy bool is not JSON
                            touch_k, com0 = k, d.subtree_com[D.a.pelvis][:2].copy()
                            # the body behind the palm: the arm's motors are too soft to pass on more than the hand's
                            # own mass, so the rest of the striker's mass driving through it comes as an impulse
                            f = vel * (S.card["strike_mass"] / (KICK_STEPS * m.opt.timestep))
                            kick = np.concatenate([f, np.cross(c.pos - d.xipos[D.head_body], f)])
                            rec.update(touch=round(k * m.opt.timestep, 3), part=re.sub(r"^.*?(red|blue)_", "", m.geom(other).name),
                                       palm=palm, speed=round(speed, 2))
                            self.emit(type="strike", t=rec["touch"], striker=S.name, part=rec["part"], palm=palm, speed=rec["speed"])
                            if not palm:
                                rec["fouls"].append((S.name, "clubbing (not the palm)"))
                            break

    def finish(self, rec, S, D, com0):
        if com0 is not None:
            rec["stagger"] = dist(self.d.subtree_com[D.a.pelvis][:2], com0) > 0.05
        rec["head_peak"] = round(rec["head_peak"], 2)
        self.emit(type="turn_end", **{k: v for k, v in rec.items() if k != "fouls"}, fouls=[f"{a}: {w}" for a, w in rec["fouls"]])
        S.a.stamina = max(MIN_STAMINA, S.a.stamina - S.card["stamina_per_strike"])
        if rec["touch"] is not None:
            D.a.mem = (rec["touch"] / 30.0, rec["speed"] / 10.0)
            if not rec["ko"]:
                D.a.neck_strength = max(MIN_NECK, D.a.neck_strength - D.card["neck_damage_per_snap"] * rec["head_peak"])
        self.emit(type="condition", **{x.name: [round(x.a.stamina, 3), round(x.a.neck_strength, 3)] for x in self.sides})
        return rec

    def play(self):
        names = [x.name for x in self.sides]
        self.emit(type="match_start", first=names[self.first], rules=dict(rounds=ROUNDS, strike_clock=STRIKE_CLOCK, recovery=RECOVERY))
        cards = [[0, 0] for _ in WEIGHTS]
        turns = []
        for rnd in range(1, ROUNDS + 1):
            recs = []
            for s in (self.first, 1 - self.first):
                r = self.turn(rnd, s)
                recs.append((s, r))
                turns.append(r)
                if r["ko"] or self.blowup:
                    res = dict(type="result", how="blow-up" if self.blowup else "KO", winner=None if self.blowup else names[s],
                               round=rnd, cards=cards)
                    self.emit(**res)
                    return res, turns
            for j, w in enumerate(WEIGHTS):
                eff = [0.0, 0.0]
                stag = [False, False]
                for s, r in recs:   # in turn order (Unreal adds in the same order)
                    if r["touch"] is not None and r["palm"]:
                        eff[s] += w[0] * r["speed"] + w[1] * 5 * r["head_peak"] + w[2] * 10 * r["stagger"]
                        stag[s] = r["stagger"]
                        if not r["ko"]:   # the defender absorbed it: stayed put, small head snap
                            eff[1 - s] += w[3] * (10 * (not r["stagger"]) + max(0.0, 10.0 - 5 * r["head_peak"]))
                hi = int(eff[1] > eff[0])
                pts = [10, 10]
                if abs(eff[0] - eff[1]) > 0.05 * max(eff):
                    pts[1 - hi] = 8 if stag[hi] and eff[hi] >= 2 * eff[1 - hi] else 9
                for s, r in recs:
                    for who, _ in r["fouls"]:
                        pts[names.index(who)] -= 1
                cards[j][0] += pts[0]
                cards[j][1] += pts[1]
            self.emit(type="round", round=rnd, cards=[list(c) for c in cards])
        votes = [0 if c[0] > c[1] else 1 if c[1] > c[0] else None for c in cards]
        wins = [votes.count(0), votes.count(1)]
        if max(wins) == 3:
            how, winner = "unanimous decision", names[wins.index(3)]
        elif max(wins) == 2:
            how, winner = ("split decision" if min(wins) == 1 else "majority decision"), names[wins.index(2)]
        else:
            how, winner = "draw", None
        res = dict(type="result", how=how, winner=winner, round=ROUNDS, cards=cards)
        self.emit(**res)
        return res, turns


def log_path(first):
    day = time.strftime("%y%m%d")
    p = HERE.parent / "Logs" / "Gameplay" / day
    p.mkdir(parents=True, exist_ok=True)
    return p / f"match_{time.strftime('%H%M%S')}_{first}_{time.perf_counter_ns() % 100000}.jsonl"


def play(onnx, first, mjb=None, log=True, blue=None, character="grandpa"):
    m, fp, pre = load(mjb, character)
    f = open(log_path(first), "w") if log else None
    red = onnx_policy(onnx)
    try:
        res, turns = Match(m, fp, pre, (red, onnx_policy(blue) if blue else red), first, f).play()
    finally:
        if f:
            f.close()
    return res, turns, (f.name if f else None)


def _exam_one(args):
    onnx, i, mjb, character = args
    res, turns, path = play(onnx, i % 2, mjb, character=character)
    return res, turns, path


def exam(onnx, matches, mjb=None, character="grandpa"):
    """L5: `matches` full matches (coin toss alternating), all must end without a sim blow-up."""
    from multiprocessing import Pool
    with Pool(min(20, matches)) as p:
        out = p.map(_exam_one, [(onnx, i, mjb, character) for i in range(matches)])
    blowups = sum(r["how"] == "blow-up" for r, _, _ in out)
    kos = sum(r["how"] == "KO" for r, _, _ in out)
    strikes = [t for _, ts, _ in out for t in ts]
    landed = [t for t in strikes if t["touch"] is not None]
    print(f"{matches} matches: {blowups} blow-ups, {kos} KOs, {len(strikes)} turns, {len(landed)} strikes landed "
          f"({sum(t['palm'] for t in landed)} palm), mean palm speed "
          f"{np.mean([t['speed'] for t in landed if t['palm']] or [0]):.1f} m/s, decisions: "
          f"{ {h: sum(r['how'] == h for r, _, _ in out) for h in sorted({r['how'] for r, _, _ in out})} }")
    ok = blowups == 0
    print("L5 exam:", "PASS" if ok else "FAIL")
    return ok


def compare(onnx, ue_log, mjb, blue=None, character="grandpa"):
    """Unreal's match log vs this simulator, same first striker, same model: every event must match."""
    ue = [json.loads(l) for l in Path(ue_log).read_text().splitlines() if l.strip()]
    first = next(e for e in ue if e["type"] == "match_start")["first"]
    res, turns, path = play(onnx, ["red", "blue"].index(first), mjb, blue=blue, character=character)
    py = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    keys = ("strike", "knockdown", "foul", "turn_end", "condition", "round", "result")
    a = [e for e in py if e["type"] in keys]
    b = [e for e in ue if e["type"] in keys]
    bad = [(x, y) for x, y in zip(a, b) if x != y]
    ok = len(a) == len(b) and not bad
    print(f"python {len(a)} events, unreal {len(b)}; first mismatch: {bad[0] if bad else '-'}  ->  {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["play", "exam", "compare"])
    ap.add_argument("onnx")
    ap.add_argument("ue_log", nargs="?")
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--matches", type=int, default=100)
    ap.add_argument("--mjb", default=None)
    ap.add_argument("--blue", default=None, help="blue's own brain (default: the same as red's)")
    ap.add_argument("--char", default="grandpa", help="both corners' character (<char>_vs_<char>_arena.xml)")
    ap.add_argument("--defender-role", type=float, default=DEFENDER_ROLE, help="-1 absorb (brains from L3), 0 guard (Unreal's DefenderRole)")
    x = ap.parse_args()
    DEFENDER_ROLE = x.defender_role
    if x.cmd == "play":
        res, turns, path = play(x.onnx, x.first, x.mjb, blue=x.blue, character=x.char)
        for t in turns:
            print(t)
        print(res, "\nlog:", path)
    elif x.cmd == "exam":
        sys.exit(0 if exam(x.onnx, x.matches, x.mjb, x.char) else 1)
    else:
        sys.exit(0 if compare(x.onnx, x.ue_log, x.mjb, x.blue, x.char) else 1)
