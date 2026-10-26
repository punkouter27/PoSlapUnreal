"""Chat hype for the broadcast: reads a Twitch channel's chat - anonymously, read-only, no account or token - and writes
Saved/Commentary/hype.json once a second: {"hype": 0-1, "rate": messages a minute, "line": "<user>: <latest message>",
"channel": ...}. ASlapHUD shows it top right and lifts the crowd with it; Scripts/commentator.py reads the mood.
Chat never touches the match: no votes, no triggers (2026-10-22: no viewer voting).

    training/.venv/Scripts/python.exe Scripts/chat.py <twitch channel>
    training/.venv/Scripts/python.exe Scripts/chat.py --demo        (made-up chat: checks without a stream)

Hype: messages over the last 20 s, a hype word or emote ("KO", "LETS GO", "PogChamp", "!!!") counting triple, through
1 - exp(-score / 15): a quiet chat sits near 0, one hype message a second reaches 0.98.
"""
import argparse, json, math, os, random, re, socket, time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Saved" / "Commentary" / "hype.json"
HYPE = re.compile(r"(!{3,}|\b(pogchamp|pog|poggers|kekw|lul|omegalul|wow|ko|slap|hype|lets ?go+|sheesh)\b)", re.I)
WINDOW, SCALE = 20.0, 15.0
DEMO = ["LETS GO GRANDPA", "KO KO KO", "that slap was nasty", "PogChamp", "who's winning?", "Kim is so fast",
        "WOW", "he's wobbling!!!", "no way he stays up", "clean palm", "lol", "SHEESH"]


def twitch(channel):
    """(user, text) per chat line; None about once a second so the caller can write even when chat is quiet."""
    while True:
        try:
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=10)
            s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{channel.lower().lstrip('#')}\r\n".encode())
            s.settimeout(1.0)
            buf = b""
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    yield None
                    continue
                if not chunk:
                    raise ConnectionError("closed")
                *lines, buf = (buf + chunk).split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif m := re.match(r":(\w+)!\S+ PRIVMSG #\S+ :(.*)", line):
                        yield m.group(1), m.group(2)
        except OSError as e:   # dropped: say so, wait, join again
            print(f"chat: {e}; reconnecting in 5 s", flush=True)
            yield None
            time.sleep(5)


def demo():
    while True:
        time.sleep(random.expovariate(0.3 + 0.6 * (math.sin(time.time() / 8) + 1)))   # waves: 0.3-1.5 messages a second
        yield f"viewer{random.randint(1, 99)}", random.choice(DEMO)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel", nargs="?")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if not (a.channel or a.demo):
        ap.error("a Twitch channel, or --demo")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    recent, line, wrote = deque(), "", 0.0
    print(f"chat: {'demo' if a.demo else '#' + a.channel} -> {OUT}", flush=True)
    for msg in (demo() if a.demo else twitch(a.channel)):
        now = time.time()
        if msg:
            user, text = msg
            recent.append((now, 3.0 if HYPE.search(text) else 1.0))
            line = f"{user}: {text}"[:80]
        while recent and now - recent[0][0] > WINDOW:
            recent.popleft()
        if now - wrote < 1.0:
            continue
        wrote = now
        score = sum(w for _, w in recent)
        state = {"hype": round(1.0 - math.exp(-score / SCALE), 3), "rate": round(len(recent) * 60.0 / WINDOW, 1),
                 "line": line, "channel": "demo" if a.demo else a.channel}
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, OUT)   # the HUD never reads half a file


if __name__ == "__main__":
    main()
