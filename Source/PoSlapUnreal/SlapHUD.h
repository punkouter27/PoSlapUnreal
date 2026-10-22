#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "SlapHUD.generated.h"

/** Build stamp bottom-right. `-SlapShot=<seconds>` saves Saved/Screenshots/slapshot.png then quits (automated checks). */
UCLASS()
class POSLAPUNREAL_API ASlapHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void BeginPlay() override;
	virtual void DrawHUD() override;

private:
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
