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
 * `-SlapShot=<seconds>` saves Saved/Screenshots/slapshot.png then quits; `-SlapFxTest=<seconds>` fakes a clean strike on
 * the defender's cheek then, and a KO 1.5 s later (automated checks of the effects without a striking brain).
 * Slate layer inside the safe zone: title, FPS, menu, debug line, build stamp at the corners; the menu is a tabbed bottom
 * sheet: MATCH (pick the pairing and each corner's brain, start a new match), ROSTER (every character card), VIEW (toggles), ABOUT
 * (legend). `-SlapMenu=<0-3>` opens it on that tab (screenshots); `-SlapUiTest` clicks through it.
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
	bool bShotTaken = false;

	// layout: canvas scale (authored at 540 x 960) and the free rows between the Slate bars, in canvas pixels
	float S = 1.f, Top = 0.f, Bot = 0.f;
	float Fps = 60.f, WorstMs = 0.f;

	// Slate layer and menu
	TSharedPtr<SWidget> Ui;
	int32 Tab = 0;
	static constexpr int32 RosterSize = 16;	// the roster grows to 16 athletes (user, 2026-10-22); open slots are placeholders
	TArray<TPair<FString, FString>> Slots;	// per slot: tile text, detail text (empty tile text = open)
	int32 RosterSel = 0;
	bool bMenu = false, bShowTelemetry = false, bShowCommentary = false, bShowDebug = false;	// the match alone by default; overlays are opt-in: VIEW tab
	bool bShowScience = false, bShowXRay = false, bSlowMo = true, bLive = false;	// slow motion is an effect, on by default
	TArray<FAssetData> Brains;	// /Game/Brains, by name; a corner may pick those made for its character ("<card>_...")
	int32 Pick[2] = { INDEX_NONE, INDEX_NONE };	// index into Brains; none = keep the corner's current brain
	TArray<FAssetData> Pairings;	// /Game/Levels/Arena*: one level per pairing (build_scene.py SLAP_RED / SLAP_BLUE)
	int32 Pairing = INDEX_NONE, HerePairing = INDEX_NONE;	// the one START plays, the one loaded now
	void Travel(int32 P);	// START on another pairing: open its level (a fresh match there, its own default brains)
	bool bPairTest = false;	// -SlapPairTest: from /Game/Levels/Arena open the next pairing at 3 s, then log what loaded
	enum EHit { HitMenu, HitTabMatch, HitTabView, HitTelemetry, HitScrim, HitClose, HitRedNext, HitStart, HitCount };
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
