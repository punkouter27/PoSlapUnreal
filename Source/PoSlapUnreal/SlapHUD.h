#pragma once

#include "CoreMinimal.h"
#include "AssetRegistry/AssetData.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "SlapController.h"
#include "SlapHUD.generated.h"

class ACameraActor;
class ASlapAthleteVisual;
class UDecalComponent;
class UMaterialInterface;
class USpotLightComponent;
class IWebSocket;
class UAudioComponent;
class ULightComponent;
class UPoseableMeshComponent;
class UMaterialInstanceDynamic;
class UNiagaraSystem;
class USoundAttenuation;
class USoundBase;
class SWidget;

/**
 * The broadcast: match panel + telemetry (palm-speed gauge, head-g trace, balance rings, fatigue, judges' slide-in card,
 * tale of the tape, commentary line), the director camera (wide / tension close-up / strike whip / KO crane), and impact
 * effects (spray, sweat, shockwave, flash, cheek ripple, shake, fringe, light flare, KO grade, sound at the contact), wear on
 * the skin (cheek flush from neck damage, a palm print, greyer when tired), sole and palm scuffs with squeaks, panting, a
 * spotlight on the striker, and a half-speed replay of a KO. All cosmetic: MuJoCo decides everything.
 * `-SlapShot=<seconds>` saves Saved/Screenshots/slapshot.png then quits (`-SlapShotResult`: once a match ends); `-SlapFxTest=<seconds>` fakes a clean strike on
 * the defender's cheek then, and a KO 1.5 s later (automated checks of the effects without a striking brain).
 * Slate layer inside the safe zone, one corner each: TL title, TC FPS + MuJoCo step counter, TR menu (icon), BL physics
 * debug (opt-in), BR build stamp; a grip at the bottom centre opens the menu under the thumb. On the play area a swipe
 * steps the view presets and a double tap replays. A finished match shows a result card (judges, series, REMATCH). The menu is a tabbed bottom sheet, tabs at its foot (thumb reach): MATCH (status, pairing,
 * each corner's brain, START, instant replay, and the view badges TEL SCI XRAY SLOW LIVE COM DBG on one screen), ROSTER
  * (every character card), ABOUT (legend). Nothing scrolls: the sheet's content is capped to a measured height budget and
  * every tab is compacted to fit it. `-SlapMenu=<0-2>` opens it on that tab (screenshots); `-SlapUiTest` clicks through it;
  * `-SlapLayoutTest` measures the laid-out sheet against the budget, the safe zone, the corner anchors and the touch
  * minimum, and logs what would overflow. `-SlapSafeZone=<top>,<bottom>` (fractions of the screen height) fakes a notch
  * and a nav bar so the audit also checks every anchor sits inside them.
 */
UCLASS()
class POSLAPUNREAL_API ASlapHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
	virtual void DrawHUD() override;

private:
	enum class EShot : uint8 { WalkIn, Wide, Tension, Strike, KO, Replay };

	void DrawMatch(const FSlapHud& H);
	void DrawTelemetry(const FSlapHud& H);
	void DrawOverlays(const FSlapHud& H);	// slide-in cards, tale of the tape, result banner, commentary, shockwave, flash
	void Direct(ACameraActor* Cam, const FSlapHud& H);
	void Cues(const FSlapHud& H);			// one-shot effects when the controller's counters move on
	void Stage(double Now);					// light flare on a strike, chase on a KO
	void Spotlight(const FSlapHud& H);		// follows the striker, snaps onto the defender at the touch
	void Scuffs(const FSlapHud& H);			// a skidding sole or a palm braced on the podium: a mark and a squeak
	void Scuff(const FVector& At, float Yaw, const FVector& Size);
	void Pant(const FSlapHud& H);			// breathing, louder and faster as stamina drains
	void Text(const FString& Str, float X, float Y, FColor C, float Scale, bool bCentre = false, UFont* Font = nullptr);
	void BuildUi();
	void LoadRoster();	// training/characters/*.json into the 16 roster slots
	void UiTest(const FSlapHud& H);
	void DrawScience(const FSlapHud& H);	// KO danger on the floor, head impact, kinetic chain, the palm's trail
	void DrawXRay(const FSlapHud& H);		// each brain's target pose (ghost) and a glow on what it reacts to
	void Crowd(const FSlapHud& H, double Now);	// the crowd rises and roars with the tension; a gasp when a defender wobbles
	void Live(const FSlapHud& H);			// second screen: poses and events over a WebSocket (Web/live.py or Azure Web PubSub)
	void LiveSend(const FString& Msg);
	void LoadLeague();						// training/models/league.json (training/scouting.py): one scouting line per brain

	TWeakObjectPtr<USlapController> Controller;
	FSlapHud Last;	// this frame's match state (DrawHUD); the Slate lambdas read it too
	double KOShot = 4.0;	// s of crane after a KO: the controller's PostMatchHold
	TWeakObjectPtr<ACameraActor> Camera;	// the level's (portrait) camera
	void CacheAthletes();	// Skin and Body, until both athletes are found
	TArray<float> Frames;
	double FpsSince = 0.0;
	float ShotAt = -1.f, FxTestAt = -1.f;
	bool bShotTaken = false, bShotOnResult = false;	// -SlapShotResult: the shot 0.5 s after a match ends (the result card)

	// layout: canvas scale (authored at 540 x 960) and the free rows between the Slate bars, in canvas pixels
	float S = 1.f, Top = 0.f, Bot = 0.f;
	float PanelBottom = 0.f;	// canvas y under the round panel (DrawMatch): the momentum bar, the tape and the replay line hang off it
	FMargin FakeSafe;	// -SlapSafeZone, as Slate's safe-zone ratios (zero = the device's own)
	FMargin Safe;	// the platform's safe insets this frame, pixels (a notch, a nav bar; -SlapSafeZone fakes them)
	float Fps = 60.f, WorstMs = 0.f;

	// Slate layer and menu
	TSharedPtr<SWidget> Ui;
		float SheetBudget = 0.f;	// the sheet's content height cap in Slate units: measured from the layer, so it follows the safe zone and any resize
		TSharedPtr<SWidget> SheetRoot, SheetBody, SheetContent, SheetTabs;	// the sheet, its capped box, the tab content and the tab row: the audit measures them
		TSharedPtr<SWidget> Anchor[5];	// TL, TC, TR, BL, BR: the audit checks the sheet never covers one
		TArray<TSharedPtr<SWidget>> Targets;	// the 44-unit targets: the audit checks each is at least that big
		void LayoutAudit();	// -SlapLayoutTest: the sheet against the budget, the safe zone, the corner anchors and the touch minimum
		bool bLayoutTest = false;
		int32 LayoutFails = 0, LayoutStep = 0, LayoutWait = 0;
		float DpiScale = 1.f;	// the Slate scale the layer is laid out at: the audit reports it next to the measured sizes
	float SafeBottomSlate = 0.f;	// the platform's bottom safe inset in Slate units: the sheet sits above it and the anchor row
		int32 Tab = 0;
	static constexpr int32 RosterSize = 16;	// the roster grows to 16 athletes (user, 2026-10-22); open slots are placeholders
	TArray<TPair<FString, FString>> Slots;	// per slot: tile text, detail text (empty tile text = open)
	int32 RosterSel = 0;
	bool bMenu = false, bShowTelemetry = false, bShowCommentary = false, bShowDebug = false;	// the match alone by default; overlays are opt-in: MATCH tab badges or a swipe
	bool bShowScience = false, bShowXRay = false, bSlowMo = true, bLive = false;	// slow motion is an effect, on by default
	TArray<FAssetData> Brains;	// /Game/Brains, by name; a corner may pick those made for its character ("<card>_...")
	int32 Pick[2] = { INDEX_NONE, INDEX_NONE };	// index into Brains; none = keep the corner's current brain
	TArray<FAssetData> Pairings;	// /Game/Levels/Arena*: one level per pairing (build_scene.py SLAP_RED / SLAP_BLUE)
	int32 Pairing = INDEX_NONE, HerePairing = INDEX_NONE;	// the one START plays, the one loaded now
	void Travel(int32 P);	// START on another pairing: open its level (a fresh match there, its own default brains)
	bool bPairTest = false;	// -SlapPairTest: from /Game/Levels/Arena open the next pairing at 3 s, then log what loaded
	enum EHit { HitMenu, HitTabMatch, HitTabRoster, HitTelemetry, HitReplay, HitScrim, HitClose, HitRedNext, HitStart, HitGrip, HitPad, HitRematch, HitCount };
	bool bQuarter = false;	// the replay runs at 1/4 speed instead of 1/2 (the MATCH tab's x1/4 badge)
	FVector2D PadDown = FVector2D::ZeroVector;	// where a press on the play area began: a swipe cycles the view presets
	int32 Preset = 0;	// 0 none, 1 TEL, 2 TEL+SCI, 3 XRAY: what a swipe steps through
	void CyclePreset(int32 Dir);
	bool bRematched = false;	// -SlapUiTest: REMATCH was tapped on the result card
	bool bTravelling = false;	// a pairing's level is opening: START, replay and the pickers stay inert until it has
	double TravelledAt = -1.0;	// when the level was asked for: if it never opens, the menu comes back (a failed load would brick it)
	void InstantReplay();	// the MATCH tab's replay button or a double tap: the last 2.5 s at 1/2 (or 1/4) speed, or back to live
	TSharedPtr<SWidget> Hit[HitCount];	// click targets for -SlapUiTest
	bool bUiTest = false, bUiChecked = false, bUiDone = false;
	int32 UiStep = 0, UiFails = 0, UiMatch0 = 0, UiSeq = 0;
	FString UiBrain;	// the brain red should play after the test's START
	double UiAt = 0.0;

	// cue state (game time)
	int32 SeenStrike = 0, SeenKO = 0, SeenMatch = 0, SeenRound = 0;
	double StrikeAt = -100.0, KOAt = -100.0, TapeAt = -100.0, CardAt = -100.0;
	FVector StrikePos = FVector::ZeroVector;
	double StrikeSpeed = 0.0;
	double StrikeK = 0.25;	// the last strike's speed over a clean one, clamped to 0.25-1.5: scales every effect
	int32 Struck = 0;
	TArray<float> HeadG;	// the defender's head g, one sample per frame (last ~2 s)

	// momentum bar: what each brain's critic usually says in each role (index role + 1), and the bar's red share
	double RoleMean[3] = {}, RoleVar[3] = {};
	bool bRoleSeen[3] = {};
	float Momentum = 0.5f;

	// stage lights at their level intensity; wear: where the last strike landed on each athlete, in head-bone space
	struct FStageLight { TWeakObjectPtr<ULightComponent> Light; float Base = 0.f; };
	FStageLight Key, Rim[2];
	TArray<FStageLight> Truss;
	TWeakObjectPtr<UPoseableMeshComponent> Body[2];
	TWeakObjectPtr<ASlapAthleteVisual> Visual[2];
	FVector FlushLocal[2] = { FVector::ZeroVector, FVector::ZeroVector };
	FVector PrintU[2], PrintV[2];	// the palm print's axes in head-bone space: along the fingers, across the palm
	float Print[2] = {};			// how deep the print is (0-1): each strike adds to it, it fades over ~25 s

	// sweat on the head snap, the crowd's roar a beat after the touch, the KO replay (game time; < 0 = none pending)
	double SweatAt = -1.0, RoarAt = -1.0, ReplayAt = -1.0, ReplayEnd = -1.0;
	FVector SweatHead0 = FVector::ZeroVector;
	float RoarVolume = 0.f;

	// scuffs: the podium box (Unreal cm, from the MuJoCo model), each sole's last position, lowest height and next allowed mark
	FBox Podium = FBox(ForceInit);
	FVector FootPrev[2][2];
	float FootLow[2][2] = { { 1e9f, 1e9f }, { 1e9f, 1e9f } };
	bool bPalmDown[2][2] = {};
	int32 Slide[2][2] = {};	// frames in a row each sole has slid along the floor
	double FootNext[2][2] = {};
	TArray<TWeakObjectPtr<UDecalComponent>> Marks;
	TWeakObjectPtr<USpotLightComponent> Spot;
	FVector SpotAim = FVector::ZeroVector;
	float SpotBase = 0.f;
	TWeakObjectPtr<UAudioComponent> Breath[2];

	// director
	EShot Shot = EShot::Wide;
	double ShotStart = 0.0;
	FRotator BaseRot = FRotator::ZeroRotator;
	FVector4 BaseSat = FVector4(1, 1, 1, 1);	// the camera's grade, the KO grade leans away from it
	float BaseVignette = 0.f;
	bool bHaveBase = false;

	// commentary (Scripts/commentator.py writes it)
	TArray<FString> Commentary;	// the latest line, wrapped at ~44 characters
	double CommentaryAt = -100.0, CommentaryPoll = 0.0, TurnAt = 0.0;
	int32 SeenTurnRound = -1;
	FString SeenTurnStriker;
	FDateTime CommentaryStamp;

	// broadcast science and X-ray (game time)
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
	TMap<FString, FString> Scout;	// brain name -> one scouting line

	// second screen
	TSharedPtr<IWebSocket> Socket;
	FString LiveUrl = TEXT("ws://127.0.0.1:8765/ue");	// -SlapLive=<url>; an Azure Web PubSub client URL works too
	bool bAzure = false;
	double LiveSent = 0.0, LiveRetry = 0.0;

	UPROPERTY(Transient) TObjectPtr<UNiagaraSystem> Spray;
	UPROPERTY(Transient) TArray<TObjectPtr<USoundBase>> SlapTakes;	// S_Slap, S_Slap1-3
	UPROPERTY(Transient) TArray<TObjectPtr<USoundBase>> Squeaks;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Thud;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Scrape;
	UPROPERTY(Transient) TObjectPtr<USoundBase> BreathLoop;
	UPROPERTY(Transient) TObjectPtr<UMaterialInterface> ScuffMat;
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdReact;
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdLoop;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Bell;
	UPROPERTY(Transient) TObjectPtr<USoundAttenuation> Ringside;	// the slap pans with where it lands on screen
	UPROPERTY(Transient) TArray<TObjectPtr<UMaterialInstanceDynamic>> Skin;	// red, blue: the cheek ripple, flush, fatigue
	UPROPERTY(Transient) TArray<TObjectPtr<UMaterialInstanceDynamic>> CrowdSkin;	// the crowd mesh's materials: Rise, Cheer (Scripts/broadcast_fx.py)
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
