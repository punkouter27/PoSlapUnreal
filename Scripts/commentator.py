"""Live commentary for the Unreal match: follows the newest Logs/Gameplay/<YYMMDD>/*_ue.jsonl as Unreal writes it, turns
the latest key event into one spoken line, shows it in the HUD (Saved/Commentary/latest.txt, ASlapHUD reads it) and says
it with a Windows voice.

    training/.venv/Scripts/python.exe Scripts/commentator.py [--voice "Microsoft David Desktop"] [--no-ai] [--mute]

Lines come from Claude (claude-opus-5-5, low effort) when credentials are available (ANTHROPIC_API_KEY or an
`ant auth login` profile), else from templates. One line at a time: while a line is spoken, events pile up and only the
most important new one is called next (latest wins), so the voice never falls behind the match. Nothing leaves the
machine except the match events sent to the API.
"""
import argparse, json, os, random, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Saved" / "Commentary" / "latest.txt"
PRIORITY = {"result": 6, "knockdown": 5, "strike": 4, "round": 3, "match_start": 2, "foul": 2, "turn_start": 1}
SYSTEM = ("You are the play-by-play voice of a Power Slap broadcast. The athletes are Red and Blue (e.g. Grandpa vs Kim), "
          "driven by trained AI brains in a physics simulator with a pre-match ring walk-in and post-match victory celebration. "
          "Call the event you are given in ONE punchy spoken "
          "sentence of at most 18 words: vivid, warm, a little funny, never cruel. Use the numbers when they add drama "
          "(hand speed in m/s, head snap, stamina and neck from 0 to 1). No emojis, no stage directions, no quotes, and "
          "never repeat a previous line.")

TEMPLATES = {
    "match_start": ["Ring walk-in underway! {first} wins the toss and marches up the aisle to strike first!",
                    "Both athletes stride down the aisle toward the podium as {first} takes the opening toss!"],
    "turn_start": ["{striker} steps up. {defender}, brace yourself.", "{striker} to strike, {defender} on the podium."],
    "strike": ["{striker} lands it at {speed} meters per second!", "Contact! {speed} meters per second from {striker}.",
               "{striker} connects with the {part}, {speed} meters per second."],
    "knockdown": ["{athlete} is down! The {part} touched the deck.", "Down goes {athlete}!"],
    "foul": ["Foul on {athlete}: {why}.", "The referee steps in: {athlete}, {why}."],
    "round": ["That is round {round} in the books.", "End of round {round}, the judges are writing."],
    "result": ["{winner} wins by {how} and raises both arms in victory!",
               "Victory celebration at the podium! {winner} takes it by {how}!"],
}


def newest_log():
    day = ROOT / "Logs" / "Gameplay" / time.strftime("%y%m%d")
    logs = sorted(day.glob("*_ue.jsonl"), key=lambda p: p.stat().st_mtime) if day.is_dir() else []
    return logs[-1] if logs else None


def template(e):
    if e["type"] == "result" and not e.get("winner"):
        return "No winner tonight: it's a draw." if e.get("how") == "draw" else "No contest."
    f = {k: (str(v).upper() if k in ("first", "striker", "defender", "athlete", "winner") else v) for k, v in e.items()}
    if "part" in f:   # hand_r -> right hand
        f["part"] = {"hand_r": "right hand", "hand_l": "left hand"}.get(f["part"], str(f["part"]).replace("_", " "))
    try:
        return random.choice(TEMPLATES[e["type"]]).format(**f)
    except (KeyError, IndexError):
        return None


class Voice:
    """One Windows SAPI process for the session: a line in, 'ok' out once it has been spoken."""

    def __init__(self, voice):
        pick = f"$s.SelectVoice('{voice}');" if voice else ""
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"{pick} $s.Rate = 1; while (($l = [Console]::In.ReadLine()) -ne $null) "
                  "{ $s.Speak($l); [Console]::Out.WriteLine('ok'); [Console]::Out.Flush() }")
        self.p = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8")

    def say(self, line):
        self.p.stdin.write(line.replace("\n", " ") + "\n")
        self.p.stdin.flush()
        self.p.stdout.readline()


class Claude:
    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=15.0, max_retries=1)

    def line(self, event, context, previous):
        msg = json.dumps({"event": event, "match_so_far": context, "your_previous_lines": previous[-4:]})
        r = self.client.beta.messages.create(
            model="claude-opus-5-5", max_tokens=1000, system=SYSTEM,
            output_config={"effort": "low"},                          # one sentence: speed over depth
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": msg}])
        if r.stop_reason == "refusal":
            return None
        return " ".join(b.text for b in r.content if b.type == "text").strip() or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="")
    ap.add_argument("--no-ai", action="store_true", help="templates only")
    ap.add_argument("--mute", action="store_true", help="HUD line only, no voice")
    ap.add_argument("--once", action="store_true", help="replay the newest log from the start and exit")
    a = ap.parse_args()
    ai = None
    if not a.no_ai:
        try:
            ai = Claude()
        except Exception as e:   # no credentials: the SDK refuses to build a client
            print(f"Claude unavailable ({type(e).__name__}): template lines")
    voice = None if a.mute else Voice(a.voice)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    log, context, previous = newest_log(), {}, []
    pos = 0 if a.once else (log.stat().st_size if log else 0)   # join the match where it is now unless --once
    print("commentator: following", ROOT / "Logs" / "Gameplay")
    while True:
        newest = newest_log()
        if newest and newest != log:
            log, pos, context = newest, 0, {}
        events = []
        if log:
            with open(log, encoding="utf-8") as f:
                f.seek(pos)
                for raw in f:
                    if not raw.endswith("\n"):
                        break   # Unreal is mid-line
                    pos += len(raw.encode("utf-8"))
                    e = json.loads(raw)
                    if e["type"] == "match_start":
                        context = {"first": e["first"]}
                    elif e["type"] == "condition":
                        context["condition"] = {k: v for k, v in e.items() if k != "type"}
                    elif e["type"] in ("round", "result"):
                        context["cards"] = e.get("cards")
                    elif e["type"] == "turn_start":
                        context["round"] = e["round"]
                    if e["type"] in PRIORITY:
                        events.append(e)
        if not events:
            time.sleep(0.2)
            continue
        e = max(reversed(events), key=lambda x: PRIORITY[x["type"]])   # the most important, the latest of equals
        line = None
        if ai:
            try:
                line = ai.line(e, context, previous)
            except (ai.anthropic.AuthenticationError, TypeError):   # TypeError: the SDK found no credentials
                print("Claude: no valid credentials, template lines from now on")
                ai = None
            except (ai.anthropic.APIConnectionError, ai.anthropic.RateLimitError, ai.anthropic.APIStatusError) as err:
                print(f"Claude: {type(err).__name__}, a template line this time")
        line = line or template(e)
        if not line:
            continue
        previous.append(line)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(line, encoding="utf-8")
        os.replace(tmp, OUT)   # the HUD never reads half a line
        print(f"[{e['type']}] {line}", flush=True)
        if voice:
            voice.say(line)
        elif a.once:
            break
        else:
            time.sleep(2.0)


if __name__ == "__main__":
    main()
