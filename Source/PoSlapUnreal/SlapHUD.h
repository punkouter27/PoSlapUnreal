#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "SlapHUD.generated.h"

class ACameraActor;
class USlapController;

/** Match HUD top-left (round, clocks, strike telemetry, judges' cards), build stamp bottom-right. `-SlapShot=<seconds>` saves Saved/Screenshots/slapshot.png then quits (automated checks). */
UCLASS()
class POSLAPUNREAL_API ASlapHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void BeginPlay() override;
	virtual void DrawHUD() override;

private:
	void DrawMatch();
	void TrackCamera(ACameraActor* Cam);
	TWeakObjectPtr<USlapController> Controller;
	TArray<float> Frames;
	double FpsSince = 0.0;
	float ShotAt = -1.f;
	bool bShotTaken = false;
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
