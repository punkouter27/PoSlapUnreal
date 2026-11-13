#pragma once

#include "CoreMinimal.h"
#include "AssetRegistry/AssetData.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "Math/IntRect.h"
#include "SlapController.h"
#include "SlapSwingRead.h"
#include "SlapHUD.generated.h"

class ACameraActor;
class ASlapAthleteVisual;
class UDecalComponent;
class UMaterialInterface;
class USpotLightComponent;
class IWebSocket;
class UAudioComponent;
class ALevelSequenceActor;
class USceneCaptureComponent2D;
class UStaticMeshComponent;
class UText3DComponent;
class UTextureRenderTarget2D;
class ULightComponent;
class UPoseableMeshComponent;
class UMaterialInstanceDynamic;
class UNiagaraSystem;
class UReverbEffect;
class UTexture;
class USoundAttenuation;
class USoundBase;
class USoundSubmixBase;
class SWidget;

/**
 * The broadcast. All cosmetic: MuJoCo decides everything, nothing here reaches the physics, the brains or the timing.
 *
 * Files: SlapHUD.cpp (frame loop, assets, loading curtain, screenshots), SlapHUDMenu.cpp (the Slate layer), SlapHUDDraw.cpp
 * (scoreboard, name plates, momentum, telemetry, overlays, judges, result), SlapHUDScience.cpp (science, X-ray),
 * SlapHUDFx.cpp (strike / KO cues, skin wear and the cheek dent, lights, scuffs, dust, quality tiers, burst capture),
 * SlapHUDAudio.cpp (crowd, breathing, footsteps, reverb), SlapHUDCamera.cpp (the director), SlapHUDLive.cpp (second
 * screen, commentary, chat, league), SlapHUDRoundTrip.cpp (menu -> match -> result -> menu, Elo), SlapHUDTourney.cpp
 * (the tournament), SlapHUDTests.cpp.
 *
 * Screen (portrait, one viewport, nothing scrolls): Slate corner anchors inside the safe zone - TL title, TC FPS + MuJoCo
 * step counter, TR menu, BL live telemetry (+ physics debug with DBG), BR build stamp - and a grip at the bottom centre
 * that opens the menu under the thumb. The menu is a bottom sheet: tabs MATCH (status, the two picks, replay) / BRAINS
 * (each corner's brain, the roster cards) / VIEW (overlays, camera, quality) / TOURNEY (the 16-place bracket; START there
 * plays it to a champion), and START at its bottom centre. TAB toggles
 * the detailed stats; by default the HUD is the scoreboard, the momentum bar and the name plates. A finished match shows
 * a result card (winner, judges, a stats table, the Elo change; REMATCH / OK) that stands until OK, then the menu with the
 * same two picked (a running tournament moves on by itself after ResultHold).
 *
 * Flags: -SlapShot=<s> (screenshot, quit; -SlapShotResult once a match ends), -SlapFxTest=<s> (a faked strike and KO),
 * -SlapMenu=<tab>, -SlapView=telemetry+science+xray+commentary+debug+noslow, -SlapCam=auto|wide|side|close,
 * -SlapTier=0|1|2, -SlapSafeZone=<top>+<bottom>, -SlapBurst, -SlapLive=<url>; checks -SlapUiTest, -SlapRoundTripTest,
 * -SlapLayoutTest, -SlapPairTest; -SlapTourney (a new bracket, played to the end; -SlapTourneyQuit: then quit).
 */
UCLASS()
class POSLAPUNREAL_API ASlapHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
	virtual void DrawHUD() override;

	enum ETab : int32 { TabMatch, TabBrains, TabView, TabTourney, NumTabs };
	enum class ECam : uint8 { Auto, Wide, Side, Close };	// the director (Auto) or a pinned shot (the VIEW tab, the C key)

private:
	enum class EShot : uint8 { WalkIn, Wide, Side, Tension, Strike, Impact, KO, Replay, Winner };	// escalating: a higher shot cuts in at once

	// ---- frame loop (SlapHUD.cpp)
	void LoadAssets();
	void FindController();
	void Layout();							// the canvas scale, the free rows between the Slate bars, the sheet's budget
	void Keys();							// TAB: the stats panel; C: the camera mode
	bool Curtain();							// the loading curtain until the shaders are compiled; true while it is up
	void Screenshot();						// -SlapShot / -SlapShotResult
	void EventShots(const FSlapHud& H);		// -SlapShotEvents: one shot per new widget as it first shows (the report)
	bool bEventShots = false;
	double EventShotAt = -100.0;
	TSet<FString> EventShotsTaken;
	void CacheAthletes();					// Skin and Body, until both athletes are found

	// ---- canvas (SlapHUDDraw.cpp)
	void DrawMatch(const FSlapHud& H);		// the scoreboard: round pips, the clock, the last call, judges and series
	void DrawMomentum(const FSlapHud& H);	// who is winning, always on
	void DrawNamePlates(const FSlapHud& H);	// each athlete: name (shrinks to fit), KD / KO badges, stamina, neck, danger
	void DrawPlates(const FSlapHud& H);		// the display pass's plates: the clock ring, the health chips, danger x neck, mirrored
	void DrawTelemetry(const FSlapHud& H);	// TAB: balance rings, palm-speed gauge, head g trace, the swing census
	void DrawOverlays(const FSlapHud& H);	// flash, shockwave, FEINT / REAL, tale of the tape, coin toss, 10-count, chat, commentary
	void DrawImpactCard(const FSlapHud& H);	// the strike's numbers and the 2 s head-snap sparkline after it
	void DrawJudges(const FSlapHud& H);		// the judges' scorecards, revealed one at a time, then the round's verdict
	void DrawTell(const FSlapHud& H);		// the AI read of the wind-up, and the tell as the palm closes in
	void DrawWinner(const FSlapHud& H);		// the winner marquee and the match's numbers
	void Text(const FString& Str, float X, float Y, FColor C, float Scale, bool bCentre = false, UFont* Font = nullptr);
	float TextBox(const FString& Str, float X, float Y, float MaxW, FColor C, float Scale, bool bCentre = false, UFont* Font = nullptr);	// shrinks to fit MaxW (to MinTextScale); returns the scale used
	void TrackBox(const FString& Name, float X, float Y, float W, float H);
	// ---- (SlapHUDScience.cpp)
	void DrawScience(const FSlapHud& H);	// KO danger on the floor, head impact, kinetic chain, the palm's trail
	void DrawXRay(const FSlapHud& H);		// each brain's target pose (ghost) and a glow on what it reacts to

	// ---- the display pass of 2026-11-04 (SlapHUDPanels.cpp). Display only: it reads MuJoCo's render snapshot (positions,
	// actuator forces) and the HUD copy, and writes nothing back. -SlapDisplayPass=0 (or slap.DisplayPass 0) turns it all off
	// (the parity proof: a match log with it on and off has identical events).
	void Sense(const FSlapHud& H);			// the snapshot: heads (the flinch), arm actuator strain, per-strike side and impulse
	void BindSense();						// body and actuator ids, once the model is up
	double DangerOf(const FSlapHud& H, int32 a) const;	// KO danger: the capture point combined with the neck damage taken
	static double CombineDanger(double Capture, double NeckLeftShare);	// 1 - (1 - capture)(1 - NeckWeight x neck damage)
	void DrawStrain(const FSlapHud& H);		// TAB: shoulder / elbow / wrist |torque| / cap as heat on the joints
	void DrawTicker(const FSlapHud& H);		// one line above the bottom row: the latest event
	void DrawScouting(const FSlapHud& H);	// between rounds: Elo, KO rate, favourite slap side per corner
	void DrawFlinch(const FSlapHud& H);		// FLINCH over the defender whose head moved before the contact
	void DrawStrikeReplayCaption(const FSlapHud& H);
	void Ticker(const FString& Line, FColor C);	// a ticker line (and the log, at slap.HudLog 1)
	void StrikeReplay(double Now);			// the per-strike instant replay at the turn's end (skippable)
	void SkipReplay();						// back to live (a tap on the arena, SPACE)
	void LoadScoutLogs();					// Logs/Gameplay/*/*_ue.jsonl: each character's slap side and KO rate
	FString ScoutLine(int32 a) const;
	bool bPass = true;						// the display pass is on
	// the snapshot's ids per athlete (red, blue): head, pelvis, and the arm joints (shoulder, elbow, wrist) x (left, right)
	enum EJoint : int32 { JShoulderL, JElbowL, JWristL, JShoulderR, JElbowR, JWristR, NumJoints };
	bool bSenseBound = false;
	int32 HeadBody[2] = { -1, -1 }, PelvisBody[2] = { -1, -1 }, JointBody[2][NumJoints];
	TArray<TPair<int32, double>> JointActs[2][NumJoints];	// actuator id and its force cap (N m)
	int32 SenseActs = 0;					// arm actuators found (12 per athlete)
	uint64 SenseFrame = 0;
	FVector HeadNow[2], PelvisNow[2], JointNow[2][NumJoints];	// Unreal cm, live (a replay draws older poses)
	float Strain[2][NumJoints] = {};		// |force| / cap, eased; PeakStrain: the most this turn
	float PeakStrain[2][NumJoints] = {};
	// the flinch (training/envs/arena.py's own rule: the defender's head > 5 cm off where it stood, before the contact)
	int32 FlinchTurnKey = -1;
	double FlinchTurnAt = -100.0, FlinchAt = -100.0;
	FVector Head0 = FVector::ZeroVector;
	bool bHead0 = false, bFlinched = false, bFlinchFeint = false;
	float FlinchCm = 0.f;
	int32 Flinches[2] = {}, FlinchArmed = 0;
	// the strike replay
	bool bStrikeReplays = true, bAutoReplay = false;
	int32 StrikeReplays = 0, ReplayDue = 0;
	double ReplayTouch = -100.0, SkippedAt = -100.0;
	double ReplayPeak = 0.0, ReplaySnap = 0.0;
	bool bReplayChain = false;
	bool bSpaceDown = false;
	// the crowd's impulse (strike mass x palm speed, N s) and the side tally
	double Impulse = 0.0, ImpulseK = 1.0;
	int32 SideHits[2][2] = {};				// this session, per corner: left, right
	TMap<FString, FIntPoint> LogSides;		// per character card, from the logs: (left, right) touches
	TMap<FString, FIntPoint> LogKOs;		// per character card: (KOs, strikes landed)
	// the ticker
	FString TickerLine;
	FColor TickerColor = FColor::White;
	double TickerAt = -100.0;
	int32 TickerDowns[2] = {}, TickerCount = 0, TickerSeenDown = 0, TickerSeenTurn = 0, TickerSeenRound = 0, TickerSeenKO = 0;
	FString TickerSeenEvent, TickerSeenResult;
	// what the UI test checks
	int32 ScoutShown = 0, RingDrawn = 0, ChipsDrawn = 0, StrainDrawn = 0;
	double ScoutAt = -100.0;
	int32 ScoutRound = 0, ScoutLogged = 0, ScoutRoundsSeen = 0;
	TMap<FString, double> LeagueElo, LeagueKO;	// training/models/league.json per brain
	float TickerY = 0.f;					// canvas y of the ticker row (Layout reserves it above the bottom anchors)
	int32 WidgetChecks(FString& Out) const;	// -SlapUiTest: the display pass's assertions; returns the fails

	// ---- effects (SlapHUDFx.cpp)
	void Cues(const FSlapHud& H);			// one-shot effects when the controller's counters move on
	void OnStrike(const FSlapHud& H, double Now);
	void OnKO(double Now);
	void OnMatch(const FSlapHud& H, double Now);
	void Wear(const FSlapHud& H, double Now);	// the skin: ripple, flush and bruise, the palm print, tired grey
	void Dent(double Now);					// the cheek dent (morph targets CheekDent_L / _R when the mesh has them)
	void Stage(const FSlapHud& H, double Now);	// key and rim flare on a strike, the defender's rim, chase on a KO
	void Spotlight(const FSlapHud& H);		// follows the striker, snaps onto the defender at the touch
	void Scuffs(const FSlapHud& H);			// a skidding sole or a palm braced on the podium: a mark and a squeak
	void Scuff(const FVector& At, float Yaw, int32 Kind);	// 0, 1 sole skids, 2 a palm smudge
	void Dust(const FSlapHud& H);			// a puff of dust where a bone (not a sole, not a palm) hit the ground
	void Burst(double Now);					// the 120 fps burst capture at the impact (render side only)
	int32 DetectTier() const;				// the quality tier this device can afford (0 low, 1 medium, 2 high)
	void ApplyTier(int32 Live);
	void RenderAudit();						// HIGH and up: contact shadows and screen-space reflections on; logs what is in force
	void ApplyCinema(bool bOn);	// tier 3 CINEMA (desktop): epic scalability, Lumen GI and reflections, contact shadows, SSS, a denser spray
	FString CineFeatures = TEXT("epic+gi+refl+contact+sss+spray");	// -SlapCine=: the features tier 3 turns on (the fps ladder)
	float SprayMul = 1.f;				// a tier in force: the spray rate, the crowd, the shadows, the engine's scalability
	void AutoTier();						// AUTO: one tier down after 3 s under 30 fps
	// ---- audio (SlapHUDAudio.cpp)
	void Crowd(const FSlapHud& H, double Now);	// the crowd rises and roars with the tension; a gasp when a defender wobbles
	void Pant(const FSlapHud& H);			// breathing, louder and faster as stamina drains
	void Steps(const FSlapHud& H);			// a footstep when a sole really touches the floor (MuJoCo's own contacts)
	void ReverbMix(double Now);				// the hall vs the ringside: blended by the camera's distance to the contact
	// ---- camera (SlapHUDCamera.cpp)
	void Direct(ACameraActor* Cam, const FSlapHud& H);
	EShot Pick(const FSlapHud& H, double Now) const;	// the shot the director (or the pinned mode) wants now
	// ---- sidecars (SlapHUDLive.cpp)
	void Live(const FSlapHud& H);			// second screen: poses and events over a WebSocket (Web/live.py or Azure Web PubSub)
	void LiveSend(const FString& Msg);
	void PollSidecars(double Now);			// Scripts/commentator.py's line, Scripts/chat.py's hype
	void LoadLeague();						// training/models/league.json (training/scouting.py): one scouting line per brain

	// ---- the Slate layer (SlapHUDMenu.cpp)
	void BuildUi();
	TSharedRef<SWidget> BuildAnchors();
	TSharedRef<SWidget> BuildResultCard();
	TSharedRef<SWidget> BuildResultStats();	// the match's numbers, red | what | blue
	TSharedRef<SWidget> BuildSheet();
	TSharedRef<SWidget> BuildMatchTab();
	TSharedRef<SWidget> BuildBrainsTab();
	TSharedRef<SWidget> BuildViewTab();
	void FindBrainsAndLevels();
	void LoadRoster();	// training/characters/*.json into the 16 roster slots
	void OpenMenu(bool bOpen);
	void Start();		// START / REMATCH: this pairing's next match, or open the picked pairing's level
	void Travel(int32 P);	// START on another pairing: open its level (a fresh match there, its own default brains)
	int32 PairingFor(int32 RedSlot, int32 BlueSlot) const;	// the level for these two slots (either corner order), or none
	bool Playable(int32 Slot) const;	// the slot's character has an arena level with somebody (Matt: still in training)
	bool IsRematch() const;	// START would replay the loaded pairing after a match
	void StepBrain(int32 Corner, int32 Dir);
	void StepFighter(int32 Corner, int32 Dir);
	TArray<int32> BrainsFor(int32 Corner) const;	// brains made for the corner's character
	void InstantReplay();	// the replay button or a double tap: the last 2.5 s at 1/2 (or 1/4) speed, or back to live
	void CyclePreset(int32 Dir);	// a swipe on the arena: none, TEL, TEL+SCI, XRAY
	void SetCam(ECam Mode);

	// ---- the round trip and the ratings (SlapHUDRoundTrip.cpp; user 2026-10-30: "a full round trip for 1v1 and a clear
	// winner", "elo is saved as games are played"): the menu at launch, the result for ResultHold s, then the menu again
	// with the same two picked; each result moves the two characters' Elo (K 32, from 1000), saved to Saved/PoSlap/elo.json
	struct FRating { double Elo = 1000.0; int32 W = 0, L = 0; };
	static FString RatingsPath();
	void LoadRatings();
	void SaveRatings() const;
	void RoundTrip();
	FString EloOf(const FString& Card) const;
	double EloNum(const FString& Card) const;
	static constexpr double ResultHold = 6.0;

	// ---- the tournament (SlapHUDTourney.cpp; user 2026-11-09: "a 16 player (with byes) bracket tourney for the 1v1
	// matches, play through the full tourney automatically and declare a winner"). Every character in the roster enters,
	// seeded by Elo (1 v 16, 8 v 9, ...), the empty places are BYEs. PLAY runs each match in its pairing's level, one after
	// another, until a champion; a pairing with no level in this build is a walkover (no Elo change); a draw is replayed
	// (the third draw: the higher Elo goes through). Every played match moves the Elo as any match does. The state lives in
	// Saved/PoSlap/tournament.json, so it survives the level changes between matches; an app launched cold pauses it.
	static constexpr int32 TourneyRounds = 4;	// 16 -> 8 -> 4 -> 2 -> 1
	static FString TourneyPath();
	void LoadTourney();
	void SaveTourney() const;
	void NewTourney();						// reseed from the roster and the Elo now
	void PlayTourney(bool bPlay);			// PLAY / PAUSE
	FString TourneyEntrant(int32 Round, int32 Index, int32 Side) const;	// "" undecided, "BYE", or a card
	bool NextTourneyMatch(int32& OutRound, int32& OutIndex);	// settles BYEs and walkovers; false when the champion is crowned
	void StartTourneyMatch(int32 Round, int32 Index);
	void TourneyTick(double Now);
	void TourneyResult(int32 W, const FSlapHud& H);	// RoundTrip: the match just rated
	FString TourneyStatus() const;
	TSharedRef<SWidget> BuildTourneyTab();
	TArray<FString> TourneySeeds;			// 16 bracket positions: a card or "BYE"
	TArray<TArray<FString>> TourneyWon;		// per round: each match's winner ("" = not yet)
	TArray<TArray<FString>> TourneyHow;		// per round: "KO", "DEC", "W/O", "BYE", "SEED" (third draw)
	TArray<FString> TourneyChampions;		// "YYYY-MM-DD card", the history
	bool bTourney = false;					// running
	int32 TourneyPend[2] = { -1, -1 };		// the match being played (round, index)
	int32 TourneyDraws = 0;
	double TourneyNextAt = -1.0;			// game time of the next start (-1: waiting on a match)
	static constexpr double TourneyShow = 4.0;	// s the bracket shows between matches
	bool bTourneyAuto = false;				// -SlapTourney: a new bracket at launch, played to the end (+ -SlapTourneyQuit: then quit)
	FTimerHandle TourneyQuitTimer;

	// ---- commentary (SlapHUDCommentary.cpp; user 2026-11-09: make it clear what is happening, who wins, when it is over):
	// the ring announcer (Scripts/announcer.py -> /Game/Audio/Announcer/S_A_<key>, one line at a time), big centre captions
	// (ROUND 1, KNOCKDOWN, KNOCKOUT, the winner), the turn banner, play-by-play lines, danger calls, the judges' running
	// score after each round, the plain-language result and the result card's summary. Display only.
	void Announce(const FString& LineKey);			// queue a line (a missing one is logged once and skipped)
	void AnnounceTick();						// plays the next queued line once the last has been said
	void Commentate(const FSlapHud& H, double Now);	// every cue above, from the controller's counters
	void Caption(const FString& Big, const FString& Small, FColor C, double Seconds);
	void DrawCaptions(const FSlapHud& H);		// the caption, the turn banner, the score strip
	void PlayByPlay(const FString& Line);		// a line in the commentary box (wrapped as the sidecar's)
	FString ResultPlain(const FSlapHud& H) const;	// "GRANDPA wins by unanimous decision, 30-27 30-27 29-28"
	FString WayKey(const FSlapHud& H) const;	// ko / ud / sd / md / dec, "" for a draw
	FString Who(const FSlapHud& H, int32 a) const;	// the fighter's name, upper case
	FString SummaryText() const;				// the result card's three lines: turning point, fastest slap, accuracy
	TArray<FString> AnnounceQueue;
	double AnnounceFree = 0.0;					// real time the announcer is free again
	UPROPERTY(Transient) TMap<FString, TObjectPtr<USoundBase>> AnnounceSounds;
	FString CapBig, CapSmall;
	FColor CapColor = FColor::White;
	double CapAt = -100.0, CapFor = 0.0;
	FString TurnBanner;
	FColor TurnBannerColor = FColor::White;
	double TurnBannerAt = -100.0;
	FString ScoreStrip;
	double ScoreStripAt = -100.0;
	int32 CmMatch = -1, CmRound = 0, CmStrike = 0, CmTurn = 0, CmKO = 0, CmScored = 0, CmDowns[2] = {}, CmTurnStrike = 0, CmTurnDef = 1;
	FString CmTurnKey, CmResult;
	bool bCmTrouble[2] = {}, bCmOneMore[2] = {};
	int32 Attempts[2] = {}, Landed[2] = {};		// turns struck / strikes landed, this match
	int32 FirstDownRound = 0, FirstDownWho = -1;	// the turning point: the first knockdown
	int32 BigRound = 0, BigRoundMargin = 0, BigRoundWho = -1;	// or the round won by the most

	// ---- the broadcast pass of 2026-11-12 (user: "do all" of the ten spectator features; render side first). Display only.
	// the win probability (SlapHUDWinProb.cpp; training/winprob.py -> training/models/winprob.json)
	void LoadWinProb();
	double WinProb(const FSlapHud& H);		// P(red wins) from the score, knockdowns, neck, stamina; -1 = no model
	void DrawWinProb(const FSlapHud& H);	// the thin row under the momentum bar
	TArray<double> WinW;					// the model's five weights (empty: no model, no row)
	int32 WinMatches = 0, WinMatch = -1, WinTurn0 = 0, WinDrawn = 0;
	float WinShown = 0.5f;
	// the stage (SlapHUDStage.cpp): the jumbotron, the 3D titles, the Sequencer shots, soft tissue. Everything is made
	// once in BuildStage (LoadAssets); a match only moves, shows and hides it
	void BuildStage();
	void Jumbo(const FSlapHud& H, double Now);		// the arena screen: a second camera at 30 Hz
	void Titles(const FSlapHud& H, double Now);		// Text3D: the fighters' names, the big caption
	void SeqTick(const FSlapHud& H, double Now);	// plays the authored camera moves; grades and focuses their camera
	void PlaySeq(int32 k, const FTransform& Origin);
	void StopSeq();
	void Jiggle(const FSlapHud& H);					// belly and jowl on damped springs, drawn by the skin material
	void LoadSoftness(const FSlapHud& H);			// per character, from the card's mass and height
	UPROPERTY(Transient) TObjectPtr<AActor> StageActor;	// owns the screen and the letters (an AHUD is hidden: its primitives never draw)
	UPROPERTY(Transient) TObjectPtr<UTextureRenderTarget2D> JumboTarget;
	UPROPERTY(Transient) TObjectPtr<UStaticMeshComponent> JumboScreen;
	UPROPERTY(Transient) TObjectPtr<USceneCaptureComponent2D> JumboCam;
	UPROPERTY(Transient) TObjectPtr<UMaterialInstanceDynamic> JumboSkin;
	bool bJumboPlaced = false, bJumboAimed = false;
	int32 JumboSubject = -1, JumboFrames = 0;
	double JumboAt = 0.0;
	FVector StageCentre = FVector::ZeroVector;
	UPROPERTY(Transient) TObjectPtr<UText3DComponent> NameTitle[2];
	UPROPERTY(Transient) TObjectPtr<UText3DComponent> BigTitle;
	FString TitleName[2], TitleBig;
	float NameShown[2] = {}, TitleYaw = -90.f, TitleSize = 0.5f;	// the letters read from the broadcast side (+Y)
	int32 TitlesShown = 0;
	enum ESeq : int32 { SeqIntro, SeqKORed, SeqKOBlue, SeqWinRed, SeqWinBlue, NumSeq };
	TWeakObjectPtr<ALevelSequenceActor> SeqActor[NumSeq];
	int32 SeqLoaded = 0, SeqPlaying = -1, SeqPlays = 0, SeqSeenKO = INT32_MIN;
	bool bSeqWalk = false, bSeqWon = false;
	struct FSoft { bool bSeen = false; FVector Prev = FVector::ZeroVector, Vel = FVector::ZeroVector, X = FVector::ZeroVector, XVel = FVector::ZeroVector; };
	FSoft Soft[2][2];								// per athlete: belly, jowl
	FString SoftCard[2];
	float Softness[2] = { 0.5f, 0.5f }, JigglePeak = 0.f;
	bool bJiggle = true;
	// the highlight reel (SlapHUDReel.cpp, SlapReel.h): saved after a knockout, played back one physics step per frame
	void ReelBegin();						// -SlapReel=<file>: playback mode
	void ReelWatch(const FSlapHud& H, double Now);	// live: save the window 3 s after a KO or a 9 m/s slap
	void ReelFrame();						// playback: the step, the camera, the caption, the frame's PNG
	bool bReel = false, bReelDone = false;
	FString ReelOut, ReelWhy;
	int32 ReelFirst = 0, ReelLast = 0, ReelShot = 0, ReelWarmed = 0, ReelsSaved = 0;
	int32 ReelSeenKO = INT32_MIN, ReelSeenStrike = 0;
	double ReelSaveAt = -1.0, ReelSpeed = 0.0;

	// ---- checks (SlapHUDTests.cpp)
	void UiTest(const FSlapHud& H);			// -SlapUiTest: every click target, then Menu -> Gameplay -> Win/Loss -> Menu -> Reset
	void RoundTripTest(const FSlapHud& H);	// -SlapRoundTripTest: launch menu -> START -> result -> menu (Elo saved) -> REMATCH
	void LayoutAudit();						// -SlapLayoutTest: the sheet against the budget, the safe zone, the anchors, the touch minimum
	void PairTest(const FSlapHud& H);
	void Tour(const FSlapHud& H);			// -SlapTour: menu tabs -> START -> walk-in -> match -> result -> menu -> REMATCH, a screenshot per stage
	bool bTour = false;
	int32 TourStep = 0, TourShots = 0, TourIgnored = 0, TourSeq = 0, TourStrike = 0, TourStrikes = 0;
	double TourAt = 0.0, TourStart = -1.0, TourEvery = 0.0, TourResultAt = -1.0, TourShotAt = -100.0;
	FString TourTrail;
	double TourCapSeen = -1.0;
	TSet<FString> TourDone;		// -SlapPairTest: open the next pairing, log what loaded
	bool ClickHit(int32 Target);			// a synthetic click at a bound target, hit-tested in the game window; false = it hit something else
	enum class EFlow : uint8 { None, Menu, Gameplay, Result, Reset };	// the state flow the UI test follows
	EFlow TourFlow = EFlow::None;
	EFlow Flow() const;

	TWeakObjectPtr<USlapController> Controller;
	FSlapHud Last;	// this frame's match state (DrawHUD); the Slate lambdas read it too
	double KOShot = 4.0;	// s of crane after a KO: the controller's PostMatchHold
	TWeakObjectPtr<ACameraActor> Camera;	// the level's (portrait) camera
	double FpsSince = 0.0;
	int32 FpsFrames = 0;
	double FpsSum = 0.0, FpsWorst = 0.0;
	bool bSceneReady = false;	// the loading curtain is up until the shaders are compiled
	double LoadStart = -1.0, QuietSince = -1.0;	// FPlatformTime: the first frame, and since when no shader job is left
	float ShotAt = -1.f, FxTestAt = -1.f;
	float ShotAfterStrike = -1.f;	// -SlapShotStrike=<s>: the screenshot this long after the first touch (the clamp, the close-up)
	FDateTime ShotRequested;	// when the last -SlapShot request went out (retried after 5 s)
	int32 ShotRetries = 0;
	bool bShotTaken = false, bShotOnResult = false;

	// layout: canvas scale (authored at 540 x 960) and the free rows between the Slate bars, in canvas pixels
	float S = 1.f, Top = 0.f, Bot = 0.f;
	float PanelBottom = 0.f;	// canvas y under the scoreboard, the momentum bar and the name plates: what hangs below starts here
	FMargin FakeSafe;	// -SlapSafeZone, as Slate's safe-zone ratios (zero = the device's own)
	FMargin Safe;		// the platform's safe insets this frame, pixels (a notch, a nav bar)
	float Fps = 60.f, WorstMs = 0.f;

	// Slate layer and menu
	TSharedPtr<SWidget> Ui;
	float SheetBudget = 0.f;	// the sheet's content height cap in Slate units, measured from the layer
	TSharedPtr<SWidget> SheetRoot, SheetBody, SheetContent, SheetTabs;	// the audit measures them
	TSharedPtr<SWidget> Anchor[5];	// TL, TC, TR, BL, BR: the sheet must never cover one
	TArray<TSharedPtr<SWidget>> Targets;	// the 44-unit targets
	bool bLayoutTest = false;
	int32 LayoutFails = 0, LayoutStep = 0, LayoutWait = 0;
	float DpiScale = 1.f;
	float SafeBottomSlate = 0.f;	// the bottom safe inset in Slate units: the sheet sits above it and the anchor row
	float SafeTopSlate = 0.f;		// the top one: the menu screen starts under it and the top anchor row
	int32 Tab = TabMatch;
	int32 TextOverflows = 0, TextShrunk = 0;	// lines that did not fit even at the smallest scale / that were shrunk
	struct FCanvasBox { FString Name; FIntRect Rect; };
	TArray<FCanvasBox> Boxes;
	static constexpr int32 RosterSize = 16;	// the roster grows to 16 athletes (user, 2026-10-22); open slots are placeholders
	TArray<TPair<FString, FString>> Slots;	// per slot: tile text, detail text (empty tile text = open)
	TArray<FString> SlotCards;				// per slot: the character card's name ("grandpa", "kim", "matt", "")
	int32 SelectedFighter[2] = { 0, 1 };	// the picks: roster slot for red and blue
	int32 ActiveCornerSelect = 0;			// the corner a tap on the grid assigns
	int32 RosterSel = 0;
	bool bMenu = false, bShowTelemetry = false, bShowCommentary = true, bShowDebug = false;	// COM: the play-by-play, on by default (2026-11-09)
	bool bShowScience = false, bShowXRay = false, bSlowMo = true, bLive = false;
	TArray<FAssetData> Brains;	// /Game/Brains in training/roster.json; a corner may pick those made for its character
	int32 BrainPick[2] = { INDEX_NONE, INDEX_NONE };	// index into Brains; none = keep the corner's current brain
	TArray<FAssetData> Pairings;	// /Game/Levels/L_Arena*: one level per pairing
	int32 Pairing = INDEX_NONE, HerePairing = INDEX_NONE;
	bool bPairTest = false;
	enum EHit { HitMenu, HitTabMatch, HitTabBrains, HitTabView, HitTelemetry, HitReplay, HitScrim, HitClose, HitRedNext, HitStart, HitGrip, HitPad,
		HitRematch, HitResultMenu, HitCamSide, HitCamAuto, HitTabTourney, HitTourneyNew, HitCount };
	TSharedPtr<SWidget> Hit[HitCount];	// click targets for the checks
	bool bQuarter = false;	// the replay runs at 1/4 speed instead of 1/2
	FVector2D PadDown = FVector2D::ZeroVector;	// where a press on the play area began
	int32 Preset = 0;
	ECam CamMode = ECam::Auto;

	TMap<FString, FRating> Ratings;	// by character card
	FString HereCard[2];			// the characters this level plays (red, blue)
	int32 RatedSeq = 0, SeenWins[2] = { -1, -1 };
	double ResultAt = -1.0;			// when the result showed (game time); -1 = none pending
	double MenuAt = -100.0;			// when the menu last opened (game time): the checks wait for it to lay out
	bool bLaunchMenu = false;
	FString EloLine;
	bool bTravelling = false;	// a pairing's level is opening: START, replay and the pickers stay inert until it has
	double TravelledAt = -1.0;

	// checks
	bool bUiTest = false, bUiChecked = false, bUiDone = false, bRoundTripTest = false;
	int32 UiStep = 0, UiFails = 0, UiSeq = 0;
	FString UiBrain;
	double UiAt = 0.0;
	EFlow FlowSeen = EFlow::None;	// the last state the UI test saw
	int32 FlowMatchSeq = 0;			// the match the flow last saw (a new one after a result is the Reset)
	FString FlowTrail;				// the states in order ("Menu > Gameplay > ...")
	int32 RtStep = 0, RtSeq = 0;
	double RtAt = 0.0;
	FString RtElo;
	bool bRtOk = false;	// the round trip test has tapped OK on the result card

	// cue state (game time)
	int32 SeenStrike = 0, SeenKO = 0, SeenMatch = 0, SeenRound = 0, SeenTurn = 0, SeenDown = 0, SeenStep[2] = {};
	int32 SeenTurnKey = -1;			// the swing read's turn (round x 2 + striker)
	double StrikeAt = -100.0, KOAt = -100.0, TapeAt = -100.0, CardAt = -100.0, DownAt = -100.0;
	FVector StrikePos = FVector::ZeroVector;
	double StrikeSpeed = 0.0;
	FVector StrikeCom0 = FVector::ZeroVector;	// the defender's centre of mass at the touch (the near-miss test)
	bool bNearMiss = false;
	double StrikeK = 0.25;	// the last strike's speed over a clean one, clamped to 0.25-1.5: scales every effect
	double MassK = 1.0;		// the last strike's mass over the card's 3 kg
	int32 Struck = 0;
	TArray<float> HeadG;	// the defender's head g, one sample per frame (last ~2 s)
	TArray<FVector2f> Snap;	// the head-snap sparkline: (s since the touch, head g) for SnapSeconds after each strike
	int32 TotalStrikes[2] = {};
	double PeakSpeed[2] = {};
	FString CoinTossWinner;
	FSlapSwingRead Read;	// FEINT / REAL and the AI read (SlapSwingRead.h)
	float Tell = 0.f;		// the palm closing in, fast
	double ChainRevealAt = -100.0;	// the kinetic chain's slow reveal

	// impact close-up and the 120 fps burst capture (render side only; the physics step never changes)
	double ImpactAt = -100.0;
	bool bBurst = false;
	int32 BurstFrames = 0;
	float BurstCap = 0.f;

	// danger meters
	float DangerShown[2] = {};
	bool bDangerHigh[2] = {};

	// quality: Tier is the pinned choice (-1 = AUTO); TierLive the one in force; AUTO steps down under load
	int32 Tier = -1, TierLive = 1, TierCap = 2;
	int32 CrowdKeep = 0;
	double SlowSince = -1.0;
	int32 BaseQuality[5] = { -1, -1, -1, -1, -1 };	// the engine's scalability at launch: shadows, post, effects, foliage, shading
	bool bTabDown = false, bCamDown = false;

	// momentum bar: what each brain's critic usually says in each role (index role + 1), and the bar's red share
	double RoleMean[3] = {}, RoleVar[3] = {};
	bool bRoleSeen[3] = {};
	float Momentum = 0.5f;

	// stage lights at their level intensity; wear: where the last strike landed on each athlete, in head-bone space
	struct FStageLight { TWeakObjectPtr<ULightComponent> Light; float Base = 0.f; };
	FStageLight Key, Rim[2];
	TArray<FStageLight> Truss;
	float RimNow[2] = { 1.f, 1.f };	// the rims' gain, eased
	TWeakObjectPtr<UPoseableMeshComponent> Body[2];
	TWeakObjectPtr<ASlapAthleteVisual> Visual[2];
	FVector FlushLocal[2] = { FVector::ZeroVector, FVector::ZeroVector };
	FVector PrintU[2], PrintV[2];
	float Print[2] = {};			// how deep the palm print is (0-1)
	float Bruise[2] = {};			// redness that builds with every head snap and fades over a minute (on top of the neck damage)
	float DentAmount[2] = {};		// the cheek dent's peak for the last strike (0-1)
	int32 DentSide[2] = { -1, -1 };	// 0 = CheekDent_L, 1 = CheekDent_R, -1 = none
	double DentAt[2] = { -100.0, -100.0 };
	bool bDentMorphs[2] = {};		// the mesh has the morph targets (checked once)

	double SweatAt = -1.0, RoarAt = -1.0, ReplayAt = -1.0, ReplayEnd = -1.0;
	FVector SweatHead0 = FVector::ZeroVector;
	float RoarVolume = 0.f;

	// scuffs
	FBox Podium = FBox(ForceInit);
	FVector FootPrev[2][2];
	float FootLow[2][2] = { { 1e9f, 1e9f }, { 1e9f, 1e9f } };
	bool bPalmDown[2][2] = {};
	int32 Slide[2][2] = {};
	double FootNext[2][2] = {};
	TArray<TWeakObjectPtr<UDecalComponent>> Marks;
	TWeakObjectPtr<USpotLightComponent> Spot;
	FVector SpotAim = FVector::ZeroVector;
	float SpotBase = 0.f;
	TWeakObjectPtr<UAudioComponent> Breath[2];
	double LastMix = -100.0;	// ReverbMix: 5 Hz

	// director
	EShot Shot = EShot::Wide;
	double ShotStart = 0.0;
	FRotator BaseRot = FRotator::ZeroRotator;
	FVector4 BaseSat = FVector4(1, 1, 1, 1);
	float BaseVignette = 0.f;
	bool bHaveBase = false;

	// commentary (Scripts/commentator.py writes it)
	TArray<FString> Commentary;	// the latest line, wrapped at ~44 characters
	double CommentaryAt = -100.0, CommentaryPoll = 0.0, TurnAt = 0.0;
	int32 SeenTurnRound = -1;
	FString SeenTurnStriker;
	FDateTime CommentaryStamp;

	// broadcast science and X-ray
	TArray<TPair<double, FVector>> Trail;	// the striker's palm, last 0.35 s
	int32 TrailOwner = -1;
	float Dilation = 1.f;	// the world clock follows the physics pace (live slow motion)

	// crowd
	float Tension = 0.f;
	bool bGasped = false;
	TWeakObjectPtr<UAudioComponent> CrowdAudio;

	// sidecars: chat hype (Scripts/chat.py), the league (training/scouting.py)
	float Hype = 0.f;
	FString ChatLine;
	double ChatAt = -100.0;
	TMap<FString, FString> Scout;

	// second screen
	TSharedPtr<IWebSocket> Socket;
	FString LiveUrl = TEXT("ws://127.0.0.1:8765/ue");
	bool bAzure = false;
	double LiveSent = 0.0, LiveRetry = 0.0;

	UPROPERTY(Transient) TObjectPtr<UNiagaraSystem> Spray;
	UPROPERTY(Transient) TObjectPtr<UNiagaraSystem> DustPuff;
	UPROPERTY(Transient) TArray<TObjectPtr<USoundBase>> SlapTakes;	// S_Slap, S_Slap1-3
	UPROPERTY(Transient) TArray<TObjectPtr<USoundBase>> Squeaks;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Thud;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Scrape;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Step;
	UPROPERTY(Transient) TObjectPtr<USoundBase> BreathLoop;
	UPROPERTY(Transient) TObjectPtr<UMaterialInterface> ScuffMat;
	UPROPERTY(Transient) TObjectPtr<UMaterialInterface> DecalMat;	// M_DecalTex (Scripts/broadcast_fx.py add_decals)
	UPROPERTY(Transient) TArray<TObjectPtr<UTexture>> DecalTex;		// sole skid 1, 2, palm smudge (the assets stream)
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdReact;
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdNear;		// the "ooooh" of a near miss
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdLoop;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Bell;
	UPROPERTY(Transient) TObjectPtr<USoundAttenuation> Ringside;
	UPROPERTY(Transient) TObjectPtr<UReverbEffect> HallVerb;		// the arena (wide shots)
	UPROPERTY(Transient) TObjectPtr<UReverbEffect> CloseVerb;	// ringside (tight shots)
	// sound pass 2 (Scripts/broadcast_audio.py, as Web/audio.js): the hard stage deck outside the mat, the PA before the bout,
	// each athlete's own grunt and breath (S_Grunt_<card>, S_Breath_<card>; Grandpa's breath is S_Breath), the arena's
	// impulse response as a convolution reverb (/Game/Audio/RV_ArenaIR, desktop: a phone keeps the two reverbs)
	UPROPERTY(Transient) TObjectPtr<USoundBase> StepDeck;
	UPROPERTY(Transient) TObjectPtr<USoundBase> PAChime;
	UPROPERTY(Transient) TObjectPtr<USoundBase> ImpactSynth;	// MS_ImpactSynth (Scripts/broadcast_metasound.py): Energy, Pitch, Bright, SkinDecay, BodyDecay
	UPROPERTY(Transient) TMap<FString, TObjectPtr<USoundBase>> Voices;	// "Grunt_<card>" / "Breath_<card>" -> sound (null: none)
	UPROPERTY(Transient) TObjectPtr<USoundSubmixBase> ArenaIR;
	FString BreathCard[2];	// the card each breathing loop is voiced for
	USoundBase* Voice(const FString& Kind, const FString& Card);
	// each character's voice pack (Scripts/voice_pack.py -> /Game/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<take>_<level>):
	// four emotions x 5 takes x 5 levels (1 weakest, 5 strongest; the loudness is in the file). A take is picked at random,
	// never the same one twice running; one line per athlete per EmoteGap s (HURT cuts in regardless).
	enum class EEmote : uint8 { Happy, Angry, Hurt, Insult };
	void Emote(int32 Athlete, EEmote Kind, int32 Level);
	void EmoteCues(const FSlapHud& H);	// HURT when struck, INSULT at the turn's start, HAPPY on a dodge or a KO, ANGRY on a knockdown
	static constexpr double EmoteGap = 0.9;
	static constexpr double IntroEach = 3.5;	// s per fighter's introduction card at the walk-in (SlapHUDDraw.cpp)
	int32 IntroSaid = -1;	// the introduction that has had its taunt (0 red, 1 blue)
	UPROPERTY(Transient) TMap<FString, TObjectPtr<USoundBase>> EmoteSounds;	// by asset name (null: not imported)
	double EmoteAt[2] = { -100.0, -100.0 };
	int32 EmoteTake[2] = { -1, -1 };
	int32 EmoteSeenStrike = 0, EmoteSeenTurn = 0, EmoteSeenKO = 0, EmoteDowns[2] = {}, EmoteTurnStrike = 0, EmoteTurnDef = 1;
	FString EmoteTurnKey;
	UPROPERTY(Transient) TArray<TObjectPtr<UMaterialInstanceDynamic>> Skin;	// red, blue
	UPROPERTY(Transient) TArray<TObjectPtr<UMaterialInstanceDynamic>> CrowdSkin;
};

UCLASS()
class POSLAPUNREAL_API ASlapGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ASlapGameMode() { HUDClass = ASlapHUD::StaticClass(); DefaultPawnClass = nullptr; }	// nothing to possess; the HUD picks the camera

	/** Chaos stays out (Blueprint core rule): no collision or simulation on any primitive. */
	virtual void StartPlay() override;
};
