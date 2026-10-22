#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "SlapHUD.generated.h"

class ACameraActor;
class UMaterialInstanceDynamic;
class UNiagaraSystem;
class USoundBase;
class USlapController;
struct FSlapHud;

/**
 * The broadcast: match panel + telemetry (palm-speed gauge, head-g trace, balance rings, fatigue, judges' slide-in card,
 * tale of the tape, commentary line), the director camera (wide / tension close-up / strike whip / KO crane), and impact
 * effects (spray, shockwave, flash, cheek ripple, shake, fringe, sound). All cosmetic: MuJoCo decides everything.
 * `-SlapShot=<seconds>` saves Saved/Screenshots/slapshot.png then quits (automated checks).
 */
UCLASS()
class POSLAPUNREAL_API ASlapHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void BeginPlay() override;
	virtual void DrawHUD() override;

private:
	enum class EShot : uint8 { Wide, Tension, Strike, KO };

	void DrawMatch(const FSlapHud& H);
	void DrawTelemetry(const FSlapHud& H);
	void DrawOverlays(const FSlapHud& H);	// slide-in cards, tale of the tape, result banner, commentary, shockwave, flash
	void Direct(ACameraActor* Cam, const FSlapHud& H);
	void Cues(const FSlapHud& H);			// one-shot effects when the controller's counters move on
	void Text(const FString& S, float X, float Y, FColor C, float Scale, bool bCentre = false, UFont* Font = nullptr);

	TWeakObjectPtr<USlapController> Controller;
	TArray<float> Frames;
	double FpsSince = 0.0;
	float ShotAt = -1.f;
	bool bShotTaken = false;

	// cue state (game time)
	int32 SeenStrike = 0, SeenKO = 0, SeenMatch = 0, SeenRound = 0;
	double StrikeAt = -100.0, KOAt = -100.0, TapeAt = -100.0, CardAt = -100.0;
	FVector StrikePos = FVector::ZeroVector;
	double StrikeSpeed = 0.0, PeakHand = 0.0;
	int32 Struck = 0;
	TArray<float> HeadG;	// the defender's head g, one sample per frame (last ~2 s)

	// director
	EShot Shot = EShot::Wide;
	double ShotStart = 0.0;
	FRotator BaseRot = FRotator::ZeroRotator;
	bool bHaveBase = false;

	// commentary (training/commentator.py writes it)
	FString Commentary;
	double CommentaryAt = -100.0, CommentaryPoll = 0.0;
	FDateTime CommentaryStamp;

	UPROPERTY(Transient) TObjectPtr<UNiagaraSystem> Spray;
	UPROPERTY(Transient) TObjectPtr<USoundBase> SlapSound;
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdReact;
	UPROPERTY(Transient) TObjectPtr<USoundBase> CrowdLoop;
	UPROPERTY(Transient) TObjectPtr<USoundBase> Bell;
	UPROPERTY(Transient) TArray<TObjectPtr<UMaterialInstanceDynamic>> Skin;	// red, blue: the cheek ripple
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
