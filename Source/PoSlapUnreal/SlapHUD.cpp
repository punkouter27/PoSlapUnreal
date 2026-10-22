#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Engine/Canvas.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Engine/Engine.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"

void ASlapGameMode::StartPlay()
{
	Super::StartPlay();
	int32 Turned = 0, Total = 0;
	for (TActorIterator<AActor> It(GetWorld()); It; ++It)
	{
		TInlineComponentArray<UPrimitiveComponent*> Prims(*It);
		for (UPrimitiveComponent* P : Prims)
		{
			++Total;
			if (P->GetCollisionEnabled() != ECollisionEnabled::NoCollision || P->IsSimulatingPhysics())
			{
				P->SetSimulatePhysics(false);
				P->SetCollisionEnabled(ECollisionEnabled::NoCollision);
				++Turned;
			}
		}
	}
	UE_LOG(LogTemp, Log, TEXT("Chaos audit: %d primitive components, %d had collision/physics and were turned off"), Total, Turned);
}

void ASlapHUD::BeginPlay()
{
	Super::BeginPlay();
	FParse::Value(FCommandLine::Get(), TEXT("SlapShot="), ShotAt);
}

void ASlapHUD::DrawHUD()
{
	Super::DrawHUD();

	APlayerController* PC = GetOwningPlayerController();	// view through the level's (portrait) camera
	TActorIterator<ACameraActor> Camera(GetWorld());
	if (PC && Camera && PC->GetViewTarget() != *Camera)
	{
		PC->SetViewTarget(*Camera);
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: viewing through %s at %s"), *Camera->GetName(), *Camera->GetActorLocation().ToString());
	}

	const FString Stamp = TEXT(POSLAP_BUILD_VERSION);
	float W = 0, H = 0;
	Canvas->StrLen(GEngine->GetSmallFont(), Stamp, W, H);
	Canvas->SetDrawColor(FColor(255, 255, 255, 160));
	Canvas->DrawText(GEngine->GetSmallFont(), Stamp, Canvas->ClipX - W - 12.f, Canvas->ClipY - H - 12.f);

	if (ShotAt >= 0.f && GetWorld()->GetTimeSeconds() >= ShotAt)
	{
		if (!bShotTaken)
		{
			bShotTaken = true;
			FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir() / TEXT("Screenshots/slapshot.png"), false, false);
		}
		else if (GetWorld()->GetTimeSeconds() >= ShotAt + 1.f)	// a frame later the file is written
		{
			UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
		}
	}
}
