# 2026-11-09 — Character voice packs, the 16-place tournament

User asked for: a voice set per character (HAPPY, ANGRY, HURT, INSULT × 5 sounds × 5 levels, 1 weakest – 5 strongest,
placeholders until real WAVs exist), and a 16-player bracket in the main menu (current players + BYEs) that plays
itself to a single winner, with Elo recorded per agent.

Decisions (user, 2026-11-09): every roster character enters; a pairing with no Unreal arena level (Matt, Mom today) is a
walkover, no Elo change; the bracket lives in the Unreal menu; Elo is per character card (`Saved/PoSlap/elo.json`, as
before); the default voice cues below.

## Policies touched

None. Display and audio only: nothing reaches MuJoCo, the brains, the observation or the timing. No retraining, no
regression scenarios to rerun; the match log is unchanged (the voices and the bracket only read `FSlapHud`).

## Voice pack

- `Scripts/voice_pack.py` → `Content/Art/Exports/Audio/Voice/<card>/<EMOTION>_<sound>_<level>.wav` (32 kHz mono),
  100 per card, for every card in `training/characters/` (a new card gets its pack on the next run; its voice comes
  from its height until it is cast in `VOICES`). Synthesised from `broadcast_audio.py`'s glottal/formant voice, so the
  placeholders match each athlete's grunt and breath (Grandpa 105 Hz, Kim 212 Hz, Matt 88 Hz, Mom 196 Hz).
- `manifest.json` holds the hash of every placeholder written. **To replace one, save the real WAV over it with the
  same name**; a file whose hash no longer matches is treated as a recording and never overwritten. Output is
  deterministic (a rerun writes identical bytes).
- Loudness is in the file: level 1 peaks at 0.30, level 5 at 0.94; length, pitch, strain and syllable count also rise.
- `Scripts/voice_import.py` (Unreal Python) → `/Game/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<sound>_<level>`
  (400 imported; `/Game/Audio` is always cooked, so Android has them).
- Listening page: `SCREENSHOTS/261109_voice_pack/index.html`.

Cues (`ASlapHUD::EmoteCues`, `SlapHUDAudio.cpp`): INSULT from the striker at each turn's start (level = round, +1
once the defender has been down, +0–1 random); HURT on the defender at each strike (level from palm speed, a clean
8 m/s slap ≈ 4); HAPPY 2 for a defender whose turn ended untouched; HAPPY 5 for the KO winner; ANGRY 2 + knockdowns
for an athlete knocked down (not out); on a decision HAPPY 4 for the winner, ANGRY 4 for the loser; HAPPY 5 for the
tournament champion. One line per athlete per 0.9 s (HURT cuts in); a take is never repeated twice in a row.
Not in the browser engine yet (`Web/audio.js`).

## Tournament (`SlapHUDTourney.cpp`, TOURNEY tab)

- 16 places: every character card in the roster, seeded by Elo (1 v 16, 8 v 9, 4 v 13, 5 v 12 | 2 v 15, 7 v 10,
  3 v 14, 6 v 11), the rest BYEs. An unplayed bracket is reseeded each launch (roster + Elo as they are).
- START on the TOURNEY tab = PLAY / PAUSE TOURNAMENT. Each match opens its pairing level (`?SlapGo`) or is played
  in this one; result → bracket shown 4 s → next match. A draw is replayed (third draw: higher Elo, "SEED").
- Played matches move Elo through the existing round-trip rating (K 32). BYE and W/O don't.
- State: `Saved/PoSlap/tournament.json` (seeds, winners and how per round, pending match, champion history, the
  field's Elo). It survives the level changes; a cold launch pauses a running bracket.
- `-SlapTourney` starts a new bracket at launch and plays it; `-SlapTourneyQuit` quits 4 s after the champion. Needs a
  window: the bracket ticks in DrawHUD, which `-nullrhi` never calls.

With today's roster (Grandpa, Kim, Matt, Mom + 12 BYEs) only Grandpa v Kim has an Unreal level, so Matt and Mom
leave by walkover. They will play for real once `L_Arena_<a>_vs_<b>` levels exist for them (and Mom graduates).

## Scene tour and fixes (same day)

`-SlapTour` (new, `SlapHUDTests.cpp`): real clicks through menu tabs → START → walk-in → match → result → OK → menu →
REMATCH, a screenshot per stage in `Saved/Screenshots/tour/`; gallery `SCREENSHOTS/261109_scene_tour/`. Fixed after it:

1. Walk-in camera: capped at 1.7 m (inside the camera jibs at 1.8 m; it sat 2.6–3.4 m out and a jib filled half the
   frame) and raised to look over the ringside tables.
2. Walk-in crawl: `HudLive()` 0.12 → 0.35 s. At 6–8 fps (first run, shaders warming, a hot GPU) the walk-in froze on
   most frames (0 → 7 % in 40 s).
3. Introductions: a card per fighter as the walk-in begins (red, then blue, 3.5 s each: corner, name, tape, Elo,
   brain), each with an INSULT taunt from the voice pack; the tale of the tape follows.
4. Result card: stands until **OK** (was: back to the menu after 6 s). A two-column stats table (strikes landed,
   fastest, knockdowns taken, flinches, stamina left, Elo) and the Elo change. A running tournament still advances by
   itself. `-SlapRoundTripTest` and the tour tap OK.
5. Winner banner opaque (the scoreboard showed through); with the menu open over a result only the arena and the
   menu are drawn.
6. Menu over a finished match: the wide shot (no replay orbit / KO crane left among the props); opening the menu ends
   any replay. In the menu the wide shot frames both smaller and higher, above the sheet.
7. Head snap read 0.0 m/s on every strike card: the referee's peak was only published at the turn's end. Now live
   (display only; the referee's value and the match log are unchanged). The corner's HEAD g shows the last ~2 s peak.
8. Two "ELO"s: the brain's league Elo is labelled BRAIN ELO; the tape shows the character's Elo (the menu's).
9. Menu idle: both hold MuJoCo's stand pose until START (the guard brain ran off its training there and flailed /
   fell). START resets to the same pose, so the match is unchanged.
10. Buttons fire on press (`EButtonClickMethod::MouseDown`, touch Down): one tap in the tour had not registered.
11. Open roster slot numbers readable.

Not code: the low frame rate on a first launch is the GPU (80 °C, clocks throttled to ~1080 MHz) plus the shader
warm-up; the same screens ran at 120–140 fps on the rematch. The build stamp is the last commit's date (Blueprint B9):
it reads 261106 until today's work is committed.

## Commentary (same day; user: "make it clear what is happening, who is the winner, when the match is over")

`SlapHUDCommentary.cpp` (display only) + `Scripts/announcer.py` (45 lines, Windows' speech engine through an arena PA, 
placeholders until recordings; `voice_import.py` → `/Game/Audio/Announcer/S_A_<key>`):

1. Ring announcer, one line at a time: each fighter's introduction (red / blue corner), "Round one / two / Final round",
   "Knockdown!", "Knock out! It's all over!", "That's the final bell…", "Your winner, by <way>… <name>!", "It's a draw",
   "<name> is in trouble!", "One more and it's over!", the tournament champion.
2. Big centre captions: ROUND 1 / FINAL ROUND (+ who strikes first), KNOCKDOWN!, KNOCKOUT!, <NAME> WINS!, IT'S A DRAW,
   CHAMPION!.
3. The end: bell, crowd, the call, then the camera on the winner (new `EShot::Winner`, after the KO crane / replays);
   the winner's arms-up hold was already in the controller.
4. Turn banner: "<STRIKER> STRIKES > <DEFENDER> DEFENDS" at each turn.
5. Play-by-play in the commentary box (on by default; the COM pill turns it off): each strike by speed ("HUGE SLAP! /
   Clean hit! / Glancing blow."), no-contact turns, knockdowns, the round's scores, the end.
6. Plain-language result everywhere the result shows: "GRANDPA wins by KNOCKOUT in round 2" / "… by UNANIMOUS
   DECISION (30-27 30-27 29-28)" / "A DRAW after 3 rounds (…)".
7. Running score strip after each round's judges' panel: "AFTER ROUND 2: GRANDPA LEADS ON ALL THREE CARDS (20-18 …)".
8. Danger calls: "<NAME> IN TROUBLE" right after a hit that leaves him near a knockdown or his neck low (once a round),
   "ONE MORE AND IT'S OVER" when the neck is almost at its floor (once a match).
9. Reactions: the voice-pack emotions (taunts at the intros, HURT on hits, HAPPY / ANGRY at the end), the winner shot.
10. Result card summary: turning point (first knockdown, else the round won by most), fastest slap, landed of attempts.

Also: HEAD g read 247 g at turn starts (the reset teleports the head; the first velocity came from the last turn): g now
needs two velocities from the same turn. Walk-in liveness 0.5 s. Checks: `-SlapTour`, `-SlapRoundTripTest`, `-SlapUiTest`
all PASS. Screenshots `SCREENSHOTS/261109_commentary/`. Known: on the first launch after a rebuild the first walk-in
can run at ~2.5 fps for ~20 s (shader / cache warm-up on a hot GPU); warm runs are 100–155 fps.
