#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Engine/Canvas.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Engine/Engine.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "SlapController.h"
#include "UObject/UObjectIterator.h"
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

	DrawMatch();

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

void ASlapHUD::DrawMatch()
{
	if (!Controller.IsValid())
	{
		for (TObjectIterator<USlapController> It; It; ++It)
		{
			if (It->GetWorld() == GetWorld()) { Controller = *It; break; }
		}
		if (!Controller.IsValid()) return;
	}
	const FSlapHud H = Controller->GetHud();
	if (H.Match == 0) return;
	UFont* Big = GEngine->GetLargeFont();
	UFont* Small = GEngine->GetSmallFont();
	const float S = Canvas->ClipX / 540.f;	// portrait layout, authored at 540 wide
	const float X = 16.f * S;
	float Y = 16.f * S;
	auto Line = [&](UFont* F, const FString& Text, FColor C, float Scale = 1.f)
	{
		Canvas->SetDrawColor(C);
		Canvas->DrawText(F, Text, X, Y, S * Scale, S * Scale);
		float W, Ht;
		Canvas->StrLen(F, Text, W, Ht);
		Y += Ht * S * Scale + 4.f * S;
	};
	const FColor Team = H.Striker == TEXT("red") ? FColor(235, 70, 60) : FColor(70, 130, 240);
	Line(Big, FString::Printf(TEXT("ROUND %d / 3"), H.Round), FColor::White, 1.2f);
	Line(Small, FString::Printf(TEXT("%s strikes  -  %s %.1f s"), *H.Striker.ToUpper(), H.bStruck ? TEXT("recovery") : TEXT("clock"), FMath::Max(0.0, H.Clock)), Team, 1.3f);
	if (H.bStruck || H.Speed > 0)
	{
		Line(Small, FString::Printf(TEXT("Hand speed %.1f m/s  (%s)"), H.Speed, *H.Part), FColor(255, 220, 120), 1.2f);
		Line(Small, FString::Printf(TEXT("Head snap %.1f m/s"), H.HeadPeak), FColor(255, 220, 120), 1.2f);
	}
	if (!H.Event.IsEmpty()) Line(Small, H.Event, FColor(230, 230, 230), 1.2f);
	Y += 6.f * S;
	Line(Small, TEXT("Judges   RED  BLUE"), FColor(200, 200, 200), 1.1f);
	for (int32 j = 0; j < 3; ++j)
	{
		Line(Small, FString::Printf(TEXT("  %d        %3d   %3d"), j + 1, H.Cards[j][0], H.Cards[j][1]), FColor(200, 200, 200), 1.1f);
	}
	if (!H.Result.IsEmpty()) Line(Small, TEXT("Last match: ") + H.Result, FColor(120, 230, 140), 1.2f);
}
