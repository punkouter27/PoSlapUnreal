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
Fouls (-1 point on every card, that round): no strike in 30 s,
striker knocked down, defender knocked down before the strike, either athlete's feet out of the foot box.
Judges: 3 cards, 10-point must. A strike's effect = hand speed (m/s) + 5 x peak defender head speed (m/s) + 10 if
the defender staggered (COM more than 5 cm off its pre-strike spot at the end of the window). The defender scores for
absorbing a clean strike he survived: 10 if he did not stagger + (10 - 5 x head speed, at least 0). Each judge weighs
(speed, head, stagger, defence) differently: (2,1,1,1), (1,2,1,1), (1,1,2,2). Per round: the bigger total gets 10, the
other 9; 10-8 when the winner staggered him and has at least twice the total; 10-10 within 5 %.
Fatigue (character card, BRAIN_IO 128-129): every turn struck costs the striker stamina_per_strike (joint speed x stamina,
floor 0.7); a defender who absorbs a strike loses neck_damage_per_snap per m/s of head snap from his neck motors' strength
(floor 0.5). Memory (BRAIN_IO 126-127): the defender remembers that strike's touch time and palm speed for his next turns.
Both reset each match; a "condition" event after every turn logs them.
Rules 2 (models of physics 4 on; the numbers are rules.json): no impulse is added at the touch (the contact is the blow);
a head speed peak over the stun threshold in the first 0.25 s slackens the defender's motors (the "stun" event); a
defender down after the strike is counted ("count", "count_end"): back on his soles at 85 % of his stand height for
1 s inside 10 s he fights on and the round is 10-8 against him, else KO; a get-up brain (G1) takes him over while
he is down. New fouls: "flinch" (the defender's head moves flinch_m while the palm is swinging) and "clubbing" (the first
touch is not a palm). Every judge's weights vary +-15 % a round from the match's seed; a hurt neck adds up to 4
decisions to the defender's reaction.
Logs: Logs/Gameplay/<YYMMDD>/match_<HHMMSS>_<first>.jsonl (AGENTS.md): events, turn/round summaries, and every 5th
brain step (10 Hz) both athletes' pelvis, head and actions.
"""
import argparse, json, math, os, random, re, sys, time
from collections import deque
from pathlib import Path

import mujoco
import numpy as np

from brain_cpu import MIN_NECK, MIN_STAMINA, REACT, Athlete, card, muscle_step, muscle_vmax, onnx_policy, settle_muscles
from constants import OBS_V3, lpf_on
from constants import (FEINT_AFTER_S, FEINT_MIN_PEAK, FEINT_PULLBACK, FEINT_REARM, FEINT_REARM_S, GAZE_GAIN, GAZE_MAX_STEP,
                       GAZE_ON, RULES, WATCH_MAX_STEP, daze_lag, models_dir, rules2)

HERE = Path(__file__).parent
# the rules are training/rules.json (constants.RULES): the same file Unreal's and the browser's generated copies come from
ROUNDS, STRIKE_CLOCK, RECOVERY = RULES["rounds"], RULES["strike_clock"], RULES["recovery"]
BOX = tuple(RULES["box"])
WEIGHTS = tuple(tuple(w) for w in RULES["judge_weights"])   # speed, head, stagger, defence
KICK_STEPS = RULES["kick_steps"]      # rules 1: the impact impulse lasts 10 ms (as envs/slap.py's scripted slap)
# Rules 2 (physics 4 on, rules.json): no scripted kick - the blow is the contact's; the stun (head speed against the
# card's chin slackens every motor); a count for a defender down after the strike (up again = 10-8, else KO); the
# flinch and clubbing fouls; judge noise from the match's dice; a damaged neck sees the hand later (constants.daze_lag).
# physics 3: the kick's reaction (momentum conservation, DOCS/20261104/physics3.md). The kick hands the defender's head
# strike_mass x v of momentum nobody gave up: physics 1/2 created it. On physics 3 the striker's touching part and the arm
# above it (up to the shoulder: hand, forearm, upper arm) take -f, split by their masses - the arm that drove the
# palm is what loses the momentum. SLAP_KICK_REACTION=0 turns it off (A/B).
KICK_REACTION = os.environ.get("SLAP_KICK_REACTION", "1") != "0"


def reaction_chain(m, geom):
    """The bodies that take the kick's reaction: the touching geom's body and its parents below the chest (an arm);
    (body ids, mass shares). A part that is not on an arm (a head, a chest) takes it alone."""
    b, out = int(m.geom_bodyid[geom]), []
    while b > 0 and re.sub(r"^.*?(red|blue)_", "", m.body(b).name).startswith(("hand", "forearm", "upperarm")):
        out.append(b)
        b = int(m.body_parentid[b])
    out = out or [int(m.geom_bodyid[geom])]
    w = np.array([m.body_mass[x] for x in out])
    return out, w / w.sum()
DEFENDER_ROLE = -1.0   # brains from L3 on defend with the striker's head and hand; earlier ones guard (0)
EXAM_JITTER = 0.01     # rad per joint on each exam turn's start pose (0.6 deg: below what a judge would see)

# Eye contact (user 2026-10-24): the defender squares up and holds the striker's eyes through the whole turn.
# The neck's yaw motor is walked to a gaze bias (a smooth PD, no teleporting); the brain owns every other joint and
# regains the neck the instant the slap lands (he braces for it). Pure maths - Unreal's Decide() runs the identical
# formulas, so the match log still compares event for event.
# The numbers live in constants.py (one source of truth): the L4 training env (envs/arena.py) runs the same servo,
# so the brains train in the world the match plays.  GAZE_GAIN, GAZE_MAX_STEP, WATCH_MAX_STEP and GAZE_ON are
# imported at the top of this file.
# NB (2026-10-25 QA): an imposed post-slap POSE hold was tried and rejected - freezing any joints after the
# touch fought the brain's own balance and tripled striker self-knockdowns (7-9/3 matches vs a 1.3 baseline,
# even at barely-there strengths and restricted to the striking arm). The striker's "stay balanced" duty is
# served by leaving the brain in charge of the body; the servo contributes only the WATCH (below).


def mix(*v):
    """32-bit integer hash (USlapController::Mix, Web/sim.js mix: the same bits everywhere): the match's dice."""
    h = 0x9E3779B9
    for x in v:
        h ^= int(x) & 0xFFFFFFFF
        h = (h * 0x85EBCA6B) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 0xC2B2AE35) & 0xFFFFFFFF
        h ^= h >> 16
    return h


def faint_step(card, seed, rnd, s, i, dt):
    """Fainting (card faint_chance_per_turn, user 2026-10-26: Matt "passes out randomly on rare occasion"): the physics
    step of athlete i's faint this turn (1-10 s in, on a brain step), or None."""
    p = card.get("faint_chance_per_turn", 0.0)
    if p <= 0.0 or mix(seed, rnd, s, i, 0) / 4294967296.0 >= p:
        return None
    return int(round((1.0 + 9.0 * mix(seed, rnd, s, i, 1) / 4294967296.0) / dt / 4)) * 4


def dist(a, b):   # spelled out so Unreal's C++ rounds identically (np.linalg.norm may sum in another order)
    return math.sqrt(sum((float(x) - float(y)) * (float(x) - float(y)) for x, y in zip(a, b)))


def load(mjb=None, character="grandpa", blue_character=None):
    """The arena model plus both corners' fingerprints: (model, (red_fp, blue_fp), prefixes), from the physics version
    SLAP_PHYSICS names (constants.models_dir: models/ = the shipped physics 1, models/v2/ = the realism batch)."""
    bc = blue_character or character
    md = models_dir()
    fps = {ch: json.loads((md / f"{ch}_fingerprint.json").read_text()) for ch in {character, bc}}
    fp = fps[character]
    if mjb:   # the model Unreal runs (URLab's compiled .mjb), with our options (URLab drops a child model's <option>)
        m = mujoco.MjModel.from_binary_path(str(mjb))
        o = fp["option"]
        m.opt.timestep, m.opt.integrator, m.opt.solver, m.opt.iterations = o["timestep"], o["integrator"], o["solver"], o["iterations"]
        m.opt.gravity[:] = o["gravity"]
    else:
        m = mujoco.MjModel.from_xml_path(str(md / f"{character}_vs_{bc}_arena.xml"))
    pre = next(re.sub(r"red_pelvis$", "", m.body(i).name) for i in range(m.nbody) if m.body(i).name.endswith("red_pelvis"))
    return m, (fp, fps[bc]), (pre + "red_", pre + "blue_")


def in_face_zone(m, d, site, p):
    """Physics 3's referee rule (every engine runs the same test): the contact point p inside the face_zone box,
    in the head's frame. The site carries the box: its world position, its frame and its half-sizes."""
    r = np.asarray(p, float) - d.site_xpos[site]
    R = d.site_xmat[site]   # row-major 3x3, site -> world: local = R^T r
    h = m.site_size[site]
    return all(abs(R[k] * r[0] + R[3 + k] * r[1] + R[6 + k] * r[2]) <= h[k] for k in range(3))


def stand_both(m, d, sides):
    """Both athletes to their own stand pose (their own fingerprint): reset once, then set each corner's joints by
    its full prefixed name - a suffix match would give the second corner's pose to both in a mixed pairing."""
    mujoco.mj_resetData(m, d)
    for s in sides:
        for j, rad in s.fp["stand_pose_rad"].items():
            i = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, s.prefix + j)
            if i >= 0:
                d.qpos[m.jnt_qposadr[i]] = rad
    settle_muscles(m, d)
    mujoco.mj_forward(m, d)


class Side:
    """One athlete in the arena: brain I/O (brain_cpu.Athlete) plus the geoms the referee watches."""

    def __init__(self, m, fp, prefix, policy, getup=None):
        self.a, self.policy, self.card, self.fp, self.prefix = Athlete(m, fp, prefix), policy, card(fp), fp, prefix
        self.getup = getup   # rules 2: the get-up brain (G1) that takes over while he is down under a count, or None
        self.a.lpf = lpf_on(getattr(policy, "n_in", OBS_V3))   # interface 4 brains only (unknown width: the shipped 136)
        self.fr0 = m.actuator_forcerange[self.a.neck].copy()
        self.fr_all = m.actuator_forcerange[self.a.act].copy()   # every motor's own force (fainting slackens them all)
        self.name = prefix.split("_")[-2] if prefix.count("_") >= 1 else prefix
        g = lambda n: m.geom(prefix + n).id
        self.head, self.hand, self.feet = g("head"), g("hand_r"), (g("foot_l"), g("foot_r"))
        self.hands = (g("hand_l"), g("hand_r"))
        self.head_body = m.geom_bodyid[self.head]
        # the face (physics 3, build_body.face_parts): the cranium sphere and the jaw capsules; a touch counts where its
        # contact point lies inside the head's face_zone site (cheek and jaw). v1/v2: the sphere, anywhere on it
        self.face = {i for i in (mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, prefix + n) for n in ("head", "jaw_l", "jaw_r")) if i >= 0}
        self.face_zone = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, prefix + "face_zone")
        self.geoms = {i for i in range(m.ngeom) if m.body_rootid[m.geom_bodyid[i]] == self.a.pelvis}


class Match:
    def __init__(self, m, fps, prefixes, policies, first, log=None, brains=None, seed=None, jitter=0.0, getups=(None, None)):
        """fps: both corners' fingerprints (a mixed pairing has one per athlete; a mirror match passes the same twice).
        brains: both corners' brain names, logged with match_start (training/scouting.py credits them; compare skips it).
        jitter: each turn's start angles get ± this many rad per joint from the seed (exam only: Unreal has none, so
        play and compare keep 0)."""
        self.m, self.first, self.log, self.brains, self.jitter = m, first, log, brains, jitter
        self.seed = random.getrandbits(31) if seed is None else seed   # the match's dice (fainting); Unreal logs its own
        self.d = mujoco.MjData(m)
        self.sides = [Side(m, fp, p, pol, g) for fp, p, pol, g in zip(fps, prefixes, policies, getups)]
        # eye-contact state: each athlete stares at the OTHER'S head (indices into its own joint_order)
        for i, sd in enumerate(self.sides):
            opp = self.sides[1 - i]
            sd.chest = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, sd.prefix + "chest")
            sd.own_head = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, sd.prefix + "head")
            sd.op_head = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, opp.prefix + "head")
            sd.i_yaw = sd.a.order.index("neck_turn")
        self.surface = {m.geom(i).id for i in range(m.ngeom) if m.geom(i).name.endswith(("floor", "podium"))}
        self.podium = next(i for i in range(m.ngeom) if m.geom(i).name.endswith("podium"))
        self.floor = self.surface - {self.podium}
        self.blowup = False
        self.vmax = muscle_vmax(m, [x.a for x in self.sides])
        # physics 2 (build_body.py): the strike impulse and speed come from the part that touched, not hand_r's
        self.physics = int(fps[0].get("physics", 1))
        self.r2 = rules2(self.physics)   # rules 2 (rules.json): physics 4 on

    def slack(self, x, gain):
        """Every motor of x at `gain` x its own force (a stun); 1.0 = back, with the neck's damage on top."""
        self.m.actuator_forcerange[x.a.act] = x.fr_all * gain
        if gain == 1.0:
            self.m.actuator_forcerange[x.a.neck] = x.fr0 * x.a.neck_strength

    def emit(self, **k):
        if self.log:
            self.log.write(json.dumps(k) + "\n")

    def down(self, side, defender=False, floor_only=False):
        """Blueprint 9: a non-foot geom of this athlete on floor or podium (from the last step's contacts); the defender's
        palms may rest on the podium (his brace, 2026-10-22). floor_only (rules 2's count): the floor alone."""
        for c in self.d.contact[:self.d.ncon]:
            g = {c.geom[0], c.geom[1]}
            if g & (self.floor if floor_only else self.surface):
                o = (g - self.surface)
                if o and (x := o.pop()) in side.geoms and x not in side.feet:
                    if defender and self.podium in g and x in side.hands:
                        continue
                    return self.m.geom(x).name
        return None

    def gaze_desire(self, side, m, d):
        """Unit world direction from this athlete's eyes to the opponent's eyes (the eye line)."""
        u = d.geom_xpos[side.op_head] - d.geom_xpos[side.own_head]
        n = math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2]) or 1.0
        return u / n

    def gaze_walk(self, side, d, step):
        """Walk the WORLD-space gaze bias toward the desire by at most `step` radians (great-circle, never a
        teleport). World space matters: the chest WHIPS during a slap's follow-through, and a chest-relative
        bias would chase a target spinning hundreds of degrees per second - the head stays glued to the
        opponent in the world while the neck quietly counter-rotates the torso."""
        wd = self.gaze_desire(side, None, d)
        bw = side.wbias
        c = max(-1.0, min(1.0, float(bw[0] * wd[0] + bw[1] * wd[1] + bw[2] * wd[2])))
        theta = math.acos(c)
        if theta <= step:
            side.wbias = wd
            return
        px, py, pz = wd[0] - c * bw[0], wd[1] - c * bw[1], wd[2] - c * bw[2]
        pn = math.sqrt(px * px + py * py + pz * pz)
        if pn < 1e-9:   # antipodal: snap (physically unreachable for two facing athletes)
            side.wbias = wd
            return
        cs, sn = math.cos(step), math.sin(step)
        v = [bw[0] * cs + px / pn * sn, bw[1] * cs + py / pn * sn, bw[2] * cs + pz / pn * sn]
        vn = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
        side.wbias = [v[0] / vn, v[1] / vn, v[2] / vn]

    def gaze_apply(self, side, m, d, step=None):
        """Drive the neck GOAL to the world bias projected into the chest frame (the yaw only - the nod stays the brain's; the head hangs
        rigid off the neck body, whose hinge axes are the chest frame's Z/Y/X), blended with the brain's own
        neck goal by GAZE_GAIN. Called right after each decision, as Unreal does."""
        step = GAZE_MAX_STEP if step is None else step
        self.gaze_walk(side, d, step)
        Rc = d.xmat[side.chest].reshape(3, 3)
        u = Rc.T @ side.wbias
        # turn only (2026-10-29): the nod tilted Kim's face up toward a taller Grandpa, off where his slap trained to
        # land; the nod stays the brain's in every engine (constants.py GAZE_* block: Unreal ApplyGaze, arena.py, sim.js)
        idx = side.i_yaw
        lo, hi = side.a.lo[idx], side.a.hi[idx]
        want = max(lo, min(hi, math.atan2(u[1], u[0])))
        blend = (1.0 - GAZE_GAIN) * side.a.goal[idx] + GAZE_GAIN * want
        side.a.goal[idx] = max(lo, min(hi, blend))

    def strike_hold(self, side, m, d):
        """Post-slap watch (user 2026-10-24: the striker "awaits to see if his opponent falls"): his eyes track
        the defender through the recovery window. Deliberately NO pose takeover - the brain keeps full command
        of the body (see the QA note on the constants: imposed poses tripled self-knockdowns). Mirrors the
        post-touch branch of USlapController::ApplyGaze."""
        self.gaze_apply(side, m, d, step=WATCH_MAX_STEP)   # watching him, fast enough for a fall

    def turn(self, rnd, s):
        """One turn, striker index s. Returns the turn record (touch, fouls, KO, effect parts)."""
        m, d = self.m, self.d
        S, D = self.sides[s], self.sides[1 - s]
        stand_both(m, d, self.sides)   # each corner to its own stand pose (per-corner fingerprints)
        if self.jitter:
            rng = np.random.default_rng((self.seed, rnd, s))
            for x in self.sides:
                d.qpos[x.a.qa] += rng.uniform(-self.jitter, self.jitter, len(x.a.qa))
            settle_muscles(m, d)
        for x in self.sides:
            x.a.goal, x.a.prev, x.a.hist = x.a.stand.copy(), np.zeros(len(x.a.order), dtype=np.float32), []
            x.wbias = self.gaze_desire(x, m, self.d).copy()   # eye contact: world bias starts on the eye line
            m.actuator_forcerange[x.a.act] = x.fr_all   # back from any faint
            m.actuator_forcerange[x.a.neck] = x.fr0 * x.a.neck_strength   # damage: a weaker neck
        mujoco.mj_forward(m, d)
        feet0 = {id(x): np.array([d.geom_xpos[f][:2] for f in x.feet]) for x in self.sides}
        hand_prev = d.geom_xpos[S.hand].copy()
        # physics 2+: every geom where the last physics step left it (envs/arena.py gprev). Kept after each step, not
        # copied before it: on a brain step mj_kinematics has already moved geom_xpos to this step's pose, and a copy
        # taken then made the touching part's velocity 0 (no kick) on strikes that landed right after a decision
        glast = d.geom_xpos.copy()
        head_prev = d.geom_xpos[D.head].copy()
        rec = dict(round=rnd, striker=S.name, touch=None, part=None, palm=False, speed=0.0, head_peak=0.0, stagger=False,
                   fouls=[], ko=False, end=None)
        r2 = self.r2
        if r2:   # rules 2's own fields (rules 1 logs are unchanged, field for field)
            rec.update(kd=False, stun=0.0)
        touch_k, com0 = None, None
        # rules 2: the defender's head and pelvis at the turn's start (the flinch foul, the count's "up"), the count
        # (the step it began, the step he was first up again), the stun's end, the daze
        head0, stand_z = d.geom_xpos[D.head].copy(), float(d.xpos[D.a.pelvis][2])
        count_k, up_k, stun_end, flinched, s_down = None, None, None, False, False
        react_n = REACT + (daze_lag(D.a.neck_strength) if r2 else 0)
        stun_steps = int(round(RULES["stun_window_s"] / m.opt.timestep))
        seen = []   # the striker's hand at the last REACT + 1 decisions: the defender sees the oldest (0.12 s late)
        vh_prev, w_prev, head_g, head_alpha = np.zeros(3), np.zeros(3), 0.0, 0.0   # head readout (the "head" event)
        # realism readout (the "realism" event, realism_stats.py): action jerk, kinetic chain (the trunk's twist should
        # peak before the shoulder's swing), foot slide
        rz = rec["_rz"] = dict(jerk=0.0, n=0, swing=deque(maxlen=100), slide=0.0, head_off=0.0)   # swing: the last 0.5 s before the touch
        pa = {id(x): [x.a.prev.copy()] * 2 for x in self.sides}
        iw, ish = S.a.va[S.a.order.index("waist_twist")], S.a.va[S.a.order.index("shoulder_swing_r")]
        kick = np.zeros(6)   # the slap's impact on the defender's head: force, torque for KICK_STEPS physics steps
        react = []           # physics 3: (body, mass share) of the striker's arm taking -kick
        # the feint read (constants.py FEINT_*, Unreal's SlapHUD F10): the striker's palm peaked and pulled back untouched
        fe = dict(peak=0.0, at=0.0, swing=False, tagged=False, n=0)
        speed = 0.0
        self.emit(type="turn_start", round=rnd, striker=S.name, defender=D.name)
        faints = [(x, k0, k0 + int(round(x.card.get("faint_seconds", 2.0) / m.opt.timestep)))
                  for i, x in enumerate(self.sides) if (k0 := faint_step(x.card, self.seed, rnd, s, i, m.opt.timestep)) is not None]
        k = 0
        while True:
            t = k * m.opt.timestep
            for x, k0, k1 in faints:   # out cold: every motor slack, then back (the referee calls whatever follows)
                if k == k0:
                    m.actuator_forcerange[x.a.act] = x.fr_all * x.card.get("faint_strength", 0.05)
                    self.emit(type="faint", t=round(t, 3), athlete=x.name)
                elif k == k1:
                    m.actuator_forcerange[x.a.act] = x.fr_all
                    m.actuator_forcerange[x.a.neck] = x.fr0 * x.a.neck_strength
            if touch_k is not None and k - touch_k == stun_steps:
                rz["head_window"] = rec["head_peak"]   # readout (calibrate_rules.py): what the stun line reads, any rules
            if r2 and touch_k is not None:   # rules 2: the stun, read once the window after the touch has run
                if k - touch_k == stun_steps:
                    sev = rec["head_peak"] / (RULES["stun_speed"] * D.card.get("chin", 1.0))
                    if sev >= 1.0:
                        sev = min(sev, RULES["stun_max"])
                        rec["stun"] = round(sev, 2)
                        stun_end = k + int(RULES["stun_s"] * sev / m.opt.timestep + 0.5)
                        self.slack(D, RULES["stun_strength"])
                        self.emit(type="stun", t=round(t, 3), athlete=D.name, severity=rec["stun"])
                elif k == stun_end:
                    self.slack(D, 1.0)
            if k % 4 == 0:   # the referee looks every brain step, before the brains decide (as Unreal)
                for x in (D, S):
                    part = self.down(x, x is D)
                    if r2 and touch_k is not None:   # rules 2: after the strike a fall is counted, not called at once
                        if x is D:
                            # down = a part other than a sole on the FLOOR, or the pelvis under up_height x his stand
                            # height (folded over the podium, on his knees). A belly or a forearm brushing the podium while
                            # he stands is not a knockdown here (rules 1 calls it one): that contact flickers from step to
                            # step, and a count hung on it ran a different length in each engine (2026-11-13 parity run).
                            part = self.down(x, True, floor_only=True)
                            low = float(d.xpos[D.a.pelvis][2]) < RULES["up_height"] * stand_z
                            if (part or low) and count_k is None:
                                count_k = k
                                self.emit(type="knockdown", t=round(t, 3), athlete=x.name,
                                          part=re.sub(r"^.*?(red|blue)_", "", part) if part else "pelvis")
                                self.emit(type="count", t=round(t, 3), athlete=x.name)
                            if count_k is not None:
                                up = part is None and not low
                                up_k = (k if up_k is None else up_k) if up else None
                                beat = up_k is not None and (k - up_k) * m.opt.timestep >= RULES["up_hold_s"]
                                if beat or (k - count_k) * m.opt.timestep >= RULES["count_s"]:
                                    self.emit(type="count_end", t=round(t, 3), athlete=x.name, up=beat,
                                              count=round((k - count_k) * m.opt.timestep, 3))
                                    if beat:
                                        rec["kd"], rec["end"] = True, "beat the count"
                                    else:
                                        rec["ko"], rec["end"] = True, "KO"
                                    return self.finish(rec, S, D, com0)
                            continue
                        if count_k is not None:   # the striker down while the count runs: his foul, the count goes on
                            if part and not s_down:
                                s_down = True
                                self.emit(type="knockdown", t=round(t, 3), athlete=x.name, part=re.sub(r"^.*?(red|blue)_", "", part))
                                rec["fouls"].append((x.name, "knocked down"))
                            continue
                    if part:
                        self.emit(type="knockdown", t=round(t, 3), athlete=x.name, part=re.sub(r"^.*?(red|blue)_", "", part))
                        if x is D and touch_k is not None:
                            rec["ko"], rec["end"] = True, "KO"
                        else:
                            rec["fouls"].append((x.name, "knocked down" if x is S else "down before the strike"))
                            rec["end"] = "knockdown"
                        return self.finish(rec, S, D, com0)
                for x in self.sides:
                    if count_k is not None and (x is D or s_down):
                        continue   # rules 2: a man down is not held to his foot box
                    off = np.abs(np.array([d.geom_xpos[f][:2] for f in x.feet]) - feet0[id(x)])
                    rz["slide"] = max(rz["slide"], float(np.linalg.norm(off, axis=1).max()))
                    if (off[:, 0] > BOX[0]).any() or (off[:, 1] > BOX[1]).any():
                        rec["fouls"].append((x.name, "out of the box"))
                        rec["end"] = "out of the box"
                        self.emit(type="foul", t=round(t, 3), athlete=x.name, why="out of the box")
                        return self.finish(rec, S, D, com0)
                if touch_k is None:
                    # the flinch line: where the defender's head was when the palm last was slow (under FEINT_MIN_PEAK: no
                    # swing on) is his set position - a brace, a lean on the podium are his to take before the swing; how
                    # far the head is from it while the palm is fast is the flinch (readout in "realism": head_off_m)
                    if speed <= FEINT_MIN_PEAK:
                        head0 = d.geom_xpos[D.head].copy()
                    rz["head_off"] = max(rz["head_off"], dist(d.geom_xpos[D.head], head0))
                if r2 and touch_k is None and not flinched and dist(d.geom_xpos[D.head], head0) > RULES["flinch_m"]:
                    flinched = True   # rules 2: he moved his head out of the swing's way (once a turn; the turn goes on)
                    rec["fouls"].append((D.name, "flinch"))
                    self.emit(type="foul", t=round(t, 3), athlete=D.name, why="flinch")
            if touch_k is None and t >= STRIKE_CLOCK:
                rec["fouls"].append((S.name, "no strike in 30 s"))
                rec["end"] = "clock"
                return self.finish(rec, S, D, com0)
            if touch_k is not None and count_k is None and (k - touch_k) * m.opt.timestep >= RECOVERY:
                rec["end"] = "recovered"
                return self.finish(rec, S, D, com0)
            if k % 4 == 0 and touch_k is None:   # the feint read, every brain step until the touch
                self.feint_read(fe, speed, t, S)
            if k % 4 == 0:
                mujoco.mj_kinematics(m, d)
                clock = 1.0 - t / STRIKE_CLOCK
                hs, hd = d.geom_xpos[S.head].copy(), d.geom_xpos[D.head].copy()
                cmd = getattr(self, "cmd", None) or (float(os.environ.get("SLAP_CMD_POWER", "0")), float(os.environ.get("SLAP_CMD_BRACE", "0")))
                S.a.decide(S.policy(S.a.obs(d, 1.0, clock, hd, cmd=cmd)))
                if count_k is not None and D.getup is not None:
                    # the director's hand-over (AGENTS.md 3): down under a count, the get-up brain drives him from the body
                    # as it is - same inputs (role 0: his own foot box), the last action carried, no pose snapped
                    D.a.decide(D.getup(D.a.obs(d)))
                elif DEFENDER_ROLE < 0:
                    seen = (seen or [d.geom_xpos[S.hand].copy()] * react_n)[-react_n:] + [d.geom_xpos[S.hand].copy()]
                    D.a.decide(D.policy(D.a.obs(d, DEFENDER_ROLE, clock, hs, seen[0], cmd=cmd)))
                else:
                    D.a.decide(D.policy(D.a.obs(d, cmd=cmd)))
                for x in self.sides:
                    p1, p2 = pa[id(x)]
                    rz["jerk"] += float(((x.a.prev - 2 * p1 + p2) ** 2).sum())
                    rz["n"] += 1
                    pa[id(x)] = [x.a.prev.copy(), p1]
                if GAZE_ON:
                    if touch_k is None:
                        self.gaze_apply(D, m, d)   # eye contact until the slap lands (then he braces for it)
                    else:
                        self.strike_hold(S, m, d)  # the striker plants and awaits the verdict
                if k % 20 == 0:
                    self.emit(type="frame", t=round(t, 3), **{x.name: dict(
                        pelvis=np.round(d.xpos[x.a.pelvis], 3).tolist(), head=np.round(d.geom_xpos[x.head], 3).tolist(),
                        action=np.round(x.a.prev, 2).tolist()) for x in self.sides})
            for x in self.sides:
                d.ctrl[x.a.act] = x.a.goal
            on = touch_k is not None and k - touch_k < KICK_STEPS
            d.xfrc_applied[D.head_body] = kick if on else 0.0
            for b, w in react:   # physics 3: the reaction on the striker's arm (force at each body's COM)
                d.xfrc_applied[b, :3] = -w * kick[:3] if on else 0.0
            gprev = glast if self.physics >= 2 and touch_k is None else None
            muscle_step(m, d, self.vmax)
            if self.physics >= 2:
                glast = d.geom_xpos.copy()
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
            if touch_k is None:
                rz["swing"].append((k, abs(d.qvel[iw]), abs(d.qvel[ish])))
            vh, w = (head - head_prev) / m.opt.timestep, d.cvel[D.head_body][:3].copy()   # head velocity, angular (world)
            if touch_k is not None:
                rec["head_peak"] = max(rec["head_peak"], dist(head, head_prev) / m.opt.timestep)
                head_g = max(head_g, dist(vh, vh_prev) / m.opt.timestep / 9.81)
                head_alpha = max(head_alpha, dist(w, w_prev) / m.opt.timestep)
                rec["_head"] = (head_g, head_alpha)
            head_prev, vh_prev, w_prev = head, vh, w
            if touch_k is None:
                for c in d.contact[:d.ncon]:
                    if c.geom[0] in D.face or c.geom[1] in D.face:
                        other = c.geom[1] if c.geom[0] in D.face else c.geom[0]
                        if other in S.geoms and (self.physics < 3 or in_face_zone(m, d, D.face_zone, c.pos)):
                            sign = 1.0 if c.geom[0] == other else -1.0   # normal from the striker's part to the head
                            ph = other if r2 and other in S.hands else S.hand   # rules 2: either hand's palm (rules 1: the right)
                            z = d.geom_xmat[ph]                          # palm = the hand box's -z axis (column 2)
                            palm = bool(other == ph and -sign * (c.frame[0] * z[2] + c.frame[1] * z[5] + c.frame[2] * z[8]) > 0.5)   # numpy bool is not JSON
                            touch_k, com0 = k, d.subtree_com[D.a.pelvis][:2].copy()
                            hand_speed = speed
                            if gprev is not None:   # physics 2: the velocity of the part that touched (v1: hand_r's)
                                vel = (d.geom_xpos[other] - gprev[other]) / m.opt.timestep
                                speed = dist(d.geom_xpos[other], gprev[other]) / m.opt.timestep
                            # the body behind the palm: the arm's motors are too soft to pass on more than the hand's
                            # own mass, so the rest of the striker's mass driving through it comes as an impulse
                            if not r2:   # rules 2: nothing added - what the contact passes on is the blow
                                f = vel * (S.card["strike_mass"] / (KICK_STEPS * m.opt.timestep))
                                kick = np.concatenate([f, np.cross(c.pos - d.xipos[D.head_body], f)])
                                if self.physics >= 3 and KICK_REACTION:
                                    react = list(zip(*reaction_chain(m, other)))
                            rec.update(touch=round(k * m.opt.timestep, 3), part=re.sub(r"^.*?(red|blue)_", "", m.geom(other).name),
                                       palm=palm, speed=round(speed, 2), feint=fe["n"] > 0)
                            # hand_speed (striker's right palm, m/s) and the feint read are additive fields (DOCS/20261103/
                            # events.md): compare() ignores them when the other engine does not log them yet
                            self.emit(type="strike", t=rec["touch"], striker=S.name, part=rec["part"], palm=palm, speed=rec["speed"],
                                      hand_speed=round(hand_speed, 2), feint=fe["n"] > 0, feints=fe["n"])
                            if r2 and not palm:   # rules 2: a strike that is not a palm is clubbing (it still lands)
                                rec["fouls"].append((S.name, "clubbing"))
                                self.emit(type="foul", t=rec["touch"], athlete=S.name, why="clubbing")
                            break

    def feint_read(self, fe, speed, t, S):
        """Unreal's HUD feint classifier (SlapHUD.cpp F10) on the striker's palm speed, once per brain step before the
        touch: the palm peaked above FEINT_MIN_PEAK and FEINT_AFTER_S later is under FEINT_PULLBACK x the peak."""
        if speed > fe["peak"]:
            fe.update(peak=speed, at=t, swing=True)
        if (fe["swing"] and not fe["tagged"] and t - fe["at"] > FEINT_AFTER_S and speed < FEINT_PULLBACK * fe["peak"]
                and fe["peak"] > FEINT_MIN_PEAK):
            fe["tagged"], fe["n"] = True, fe["n"] + 1
            self.emit(type="feint", t=round(t, 3), striker=S.name, peak=round(fe["peak"], 2), n=fe["n"])
        if fe["tagged"] and speed > FEINT_REARM * fe["peak"] and t - fe["at"] > FEINT_REARM_S:
            fe.update(swing=False, tagged=False, peak=0.0)

    def finish(self, rec, S, D, com0):
        if com0 is not None:
            rec["stagger"] = dist(self.d.subtree_com[D.a.pelvis][:2], com0) > 0.05
        rec["head_peak"] = round(rec["head_peak"], 2)
        if rec["touch"] is not None:   # the strike's outcome, once the recovery window has measured it (events.md)
            self.emit(type="strike_result", t=rec["touch"], striker=S.name, defender=D.name, part=rec["part"], speed=rec["speed"],
                      head_peak=rec["head_peak"], feint=rec.get("feint", False), stagger=rec["stagger"], ko=rec["ko"])
        if "_head" in rec:   # readout only (Python): peak linear g and rotational rad/s^2 after the touch, 5 ms differences.
            g, al = rec.pop("_head")   # concussion is usually put at ~70-100 g / ~4500-6000 rad/s^2 (football studies)
            self.emit(type="head", t=rec["touch"], athlete=D.name, peak_g=round(g, 1), peak_rad_s2=round(al))
        rz = rec.pop("_rz")
        self.emit(type="realism", striker=S.name, touched=rec["touch"] is not None, jerk=round(rz["jerk"] / max(1, rz["n"]), 4),
                  chain_ms=round((max(rz["swing"], key=lambda r: r[2])[0] - max(rz["swing"], key=lambda r: r[1])[0])
                                 * self.m.opt.timestep * 1000) if rec["touch"] is not None and rz["swing"] else None,
                  slide_m=round(rz["slide"], 3), head_off_m=round(rz["head_off"], 3),
                  head_window=round(rz["head_window"], 2) if "head_window" in rz else None)
        self.emit(type="turn_end", **{k: v for k, v in rec.items() if k not in ("fouls", "feint")}, fouls=[f"{a}: {w}" for a, w in rec["fouls"]])
        S.a.stamina = max(MIN_STAMINA, S.a.stamina - S.card["stamina_per_strike"])
        if rec["touch"] is not None:
            D.a.mem = (rec["touch"] / 30.0, rec["speed"] / 10.0)
            if not rec["ko"]:
                D.a.neck_strength = max(MIN_NECK, D.a.neck_strength - D.card["neck_damage_per_snap"] * rec["head_peak"])
        self.emit(type="condition", **{x.name: [round(x.a.stamina, 3), round(x.a.neck_strength, 3)] for x in self.sides})
        return rec

    def play(self):
        names = [x.name for x in self.sides]
        self.emit(type="match_start", first=names[self.first], rules=dict(rounds=ROUNDS, strike_clock=STRIKE_CLOCK, recovery=RECOVERY,
                                                                          **({"version": 2} if self.r2 else {})), seed=self.seed,
                  **({"brains": dict(zip(names, self.brains))} if self.brains else {}), **({"jitter": self.jitter} if self.jitter else {}))
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
                if self.r2:   # rules 2: the judge's taste this round, from the match's dice
                    w = [w[i] * (1.0 + RULES["judge_noise"] * (2.0 * (mix(self.seed, rnd, j, i, 7) / 4294967296.0) - 1.0)) for i in range(4)]
                eff = [0.0, 0.0]
                stag = [False, False]
                kd = [False, False]   # rules 2: he put the other down and the other beat the count
                for s, r in recs:   # in turn order (Unreal adds in the same order)
                    if r["touch"] is not None:
                        eff[s] += w[0] * r["speed"] + w[1] * 5 * r["head_peak"] + w[2] * 10 * r["stagger"]
                        stag[s] = r["stagger"]
                        kd[s] = r.get("kd", False)
                        if not r["ko"]:   # the defender absorbed it: stayed put, small head snap
                            eff[1 - s] += w[3] * (10 * (not r["stagger"]) + max(0.0, 10.0 - 5 * r["head_peak"]))
                hi = int(eff[1] > eff[0])
                pts = [10, 10]
                if abs(eff[0] - eff[1]) > 0.05 * max(eff):
                    pts[1 - hi] = 8 if (stag[hi] and eff[hi] >= 2 * eff[1 - hi]) or kd[hi] else 9
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
            how, winner = tiebreak(cards, turns, names, self.first)
        res = dict(type="result", how=how, winner=winner, round=ROUNDS, cards=cards)
        self.emit(**res)
        return res, turns


def tiebreak(cards, turns, names, first):
    """The judges drew, but a match always has a winner (user 2026-10-30: "a clear winner"): the higher total over the
    three cards; level, the harder slap landed in the match; neither landed one, the athlete who struck second (the
    coin toss gave the other the first swing). Web/sim.js and SlapReferee.cpp break the tie the same way."""
    tot = [sum(c[0] for c in cards), sum(c[1] for c in cards)]
    if tot[0] != tot[1]:
        return "decision on total points", names[int(tot[1] > tot[0])]
    hard = [max([r["speed"] for r in turns if r["striker"] == n and r["touch"] is not None], default=0.0) for n in names]
    if hard[0] != hard[1]:
        return "decision on the harder slap", names[int(hard[1] > hard[0])]
    return "decision: struck second", names[1 - first]


ADDITIVE = {"strike": ("hand_speed", "feint", "feints")}   # strike fields added 2026-11-03 (events.md)


def log_path(first):
    day = time.strftime("%y%m%d")
    p = HERE.parent / "Logs" / "Gameplay" / day
    p.mkdir(parents=True, exist_ok=True)
    return p / f"match_{time.strftime('%H%M%S')}_{first}_{os.getpid()}_{time.perf_counter_ns() % 100000}.jsonl"


def getup_brain(character):
    """The character's graduated get-up brain (G1) for the physics in play, or None: models/brains/<char>_G1[_v<n>].onnx."""
    from constants import physics
    for v in range(physics(), 0, -1):
        p = HERE / "models" / "brains" / (f"{character}_G1_v{v}.onnx" if v > 1 else f"{character}_G1.onnx")
        if p.exists():
            return onnx_policy(str(p))
    return None


def play(onnx, first, mjb=None, log=True, blue=None, character="grandpa", blue_character=None, seed=None, jitter=0.0, getup=True):
    m, fps, pre = load(mjb, character, blue_character)
    f = open(log_path(first), "w") if log else None
    red = onnx_policy(onnx)
    # rules 2: each corner's own get-up brain, when it has graduated one (getup=False: nobody gets help up)
    gu = tuple(getup_brain(c) for c in (character, blue_character or character)) if getup and rules2(fps[0].get("physics", 1)) else (None, None)
    try:
        res, turns = Match(m, fps, pre, (red, onnx_policy(blue) if blue else red), first, f, (Path(onnx).stem, Path(blue or onnx).stem), seed,
                           jitter, gu).play()
    finally:
        if f:
            f.close()
    return res, turns, (f.name if f else None)


def _exam_one(args):
    onnx, i, mjb, character, blue_character, jitter, blue = args
    res, turns, path = play(onnx, i % 2, mjb, blue=blue, character=character, blue_character=blue_character, seed=i, jitter=jitter)
    return res, turns, path


def exam(onnx, matches, mjb=None, character="grandpa", blue_character=None, jitter=EXAM_JITTER, blue=None):
    """L5: `matches` full matches (coin toss alternating, seed = match index), all must end without a sim blow-up.
    Start angles jittered per turn, or every match without a faint chance would be the same match."""
    from multiprocessing import Pool
    with Pool(min(20, matches)) as p:
        out = p.map(_exam_one, [(onnx, i, mjb, character, blue_character, jitter, blue) for i in range(matches)])
    blowups = sum(r["how"] == "blow-up" for r, _, _ in out)
    kos = sum(r["how"] == "KO" for r, _, _ in out)
    strikes = [t for _, ts, _ in out for t in ts]
    landed = [t for t in strikes if t["touch"] is not None]
    print(f"{matches} matches: {blowups} blow-ups, {kos} KOs, {len(strikes)} turns, {len(landed)} strikes landed "
          f"({sum(t['palm'] for t in landed)} palm-led), mean hand speed "
          f"{np.mean([t['speed'] for t in landed] or [0]):.1f} m/s, decisions: "
          f"{ {h: sum(r['how'] == h for r, _, _ in out) for h in sorted({r['how'] for r, _, _ in out})} }, "
          f"KO per landed strike {sum(t['ko'] for t in landed) / max(1, len(landed)):.0%} (L4 band 10-40 %), "
          f"rules 2: {sum(t.get('stun', 0) > 0 for t in landed)} stuns, {sum(t.get('kd', False) for t in landed)} beat the count, "
          f"fouls { {w: sum(any(x == w for _, x in t['fouls']) for t in strikes) for w in ('flinch', 'clubbing')} }, "
          f"striker self-falls {sum(any(w == 'knocked down' for _, w in t['fouls']) for t in strikes) / max(1, len(strikes)):.0%}, "
          f"distinct matches {len({json.dumps(r['cards']) + str([(t['touch'], t['speed']) for t in ts]) for r, ts, _ in out})}")
    ok = blowups == 0
    print("L5 exam:", "PASS" if ok else "FAIL")
    return ok


def compare(onnx, ue_log, mjb, blue=None, character="grandpa", blue_character=None):
    """Unreal's match log vs this simulator, same first striker, same model: every event must match."""
    ue = [json.loads(l) for l in Path(ue_log).read_text().splitlines() if l.strip()]
    start = next(e for e in ue if e["type"] == "match_start")
    res, turns, path = play(onnx, ["red", "blue"].index(start["first"]), mjb, blue=blue, character=character, blue_character=blue_character,
                            seed=start.get("seed"))
    py = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    keys = ("strike", "faint", "knockdown", "foul", "turn_end", "condition", "round", "result", "stun", "count", "count_end")
    a = [e for e in py if e["type"] in keys]
    b = [e for e in ue if e["type"] in keys]
    for x, y in zip(a, b):   # the additive fields (DOCS/20261103/events.md) count only once the other engine logs them
        for k in ADDITIVE.get(x["type"], ()):
            if k not in y:
                x.pop(k, None)
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
    ap.add_argument("--char", default="grandpa", help="red's character (<char>_vs_<char>_arena.xml)")
    ap.add_argument("--blue-char", default=None, help="blue's own character for a mixed pairing (default: the same as red's)")
    ap.add_argument("--defender-role", type=float, default=DEFENDER_ROLE, help="-1 absorb (brains from L3), 0 guard (Unreal's DefenderRole)")
    ap.add_argument("--physics", type=int, default=None, help="the physics version's models (1 shipped, n = models/v<n>; 4 plays rules 2); default SLAP_PHYSICS or 1")
    x = ap.parse_args()
    if x.physics is not None:   # before the exam's worker processes start: they read it from the environment
        os.environ["SLAP_PHYSICS"] = str(x.physics)
    DEFENDER_ROLE = x.defender_role
    if x.cmd == "play":
        res, turns, path = play(x.onnx, x.first, x.mjb, blue=x.blue, character=x.char, blue_character=x.blue_char)
        for t in turns:
            print(t)
        print(res, "\nlog:", path)
    elif x.cmd == "exam":
        sys.exit(0 if exam(x.onnx, x.matches, x.mjb, x.char, x.blue_char, blue=x.blue) else 1)
    else:
        sys.exit(0 if compare(x.onnx, x.ue_log, x.mjb, x.blue, x.char, x.blue_char) else 1)
