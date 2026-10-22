#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "MuJoCo/Components/Geometry/MjGeom.h"
#include "SlapAthleteVisual.h"
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
	int32 Turned = 0, Total = 0, Hidden = 0;
	for (TActorIterator<AActor> It(GetWorld()); It; ++It)
	{
		TInlineComponentArray<UMjGeom*> Geoms(*It);
		for (UMjGeom* G : Geoms)	// an athlete's MuJoCo collision shape: physics only, the skinned mesh is what we draw
		{
			const FString N = G->GetMjName();
			if (N.Contains(TEXT("red_")) || N.Contains(TEXT("blue_")))
			{
				G->SetHiddenInGame(true, true);
				++Hidden;
			}
		}
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
	UE_LOG(LogTemp, Log, TEXT("Chaos audit: %d primitive components, %d had collision/physics and were turned off; %d athlete collision shapes hidden"), Total, Turned, Hidden);
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

	if (Camera) TrackCamera(*Camera);
	Frames.Add(GetWorld()->GetDeltaSeconds());	// performance pass: average fps every 10 s in the log
	if (Frames.Num() > 1 && GetWorld()->GetRealTimeSeconds() - FpsSince >= 10.0)
	{
		double Sum = 0.0, Worst = 0.0;
		for (float F : Frames) { Sum += F; Worst = FMath::Max<double>(Worst, F); }
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: %.1f fps average, worst frame %.1f ms (%d frames)"), Frames.Num() / Sum, Worst * 1000.0, Frames.Num());
		Frames.Reset();
		FpsSince = GetWorld()->GetRealTimeSeconds();
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
	if (!Controller.IsValid())	// the controller playing the match (URLab may keep more than one instance around)
	{
		for (TObjectIterator<USlapController> It; It; ++It)
		{
			if (!It->HasAnyFlags(RF_ClassDefaultObject | RF_ArchetypeObject) && It->GetHud().Match > 0) { Controller = *It; break; }
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
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 8.f * S, 8.f * S, 300.f * S, (H.Result.IsEmpty() ? 205.f : 228.f) * S);	// legible over the truss
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

namespace { constexpr double Recovery = 5.0; }	// match.py RECOVERY

void ASlapHUD::TrackCamera(ACameraActor* Cam)
{
	// Side-on, both athletes framed head to toe (~60 % of the portrait height), centred between them; a short push-in
	// after each strike. Keeps the camera's own direction; only its position moves, smoothly.
	FBox Box(ForceInit);
	for (TActorIterator<ASlapAthleteVisual> It(GetWorld()); It; ++It)
	{
		for (const TCHAR* Bone : { TEXT("head"), TEXT("pelvis"), TEXT("foot_l"), TEXT("foot_r") })
		{
			const FVector P = It->Mesh->GetBoneLocation(Bone);
			if (!P.IsZero()) Box += P;
		}
	}
	if (!Box.IsValid) return;
	const FVector Fwd = Cam->GetActorForwardVector();
	const float HalfV = FMath::Atan(FMath::Tan(FMath::DegreesToRadians(Cam->GetCameraComponent()->FieldOfView) * 0.5f) / FMath::Max(Cam->GetCameraComponent()->AspectRatio, 0.1f));
	const float Height = FMath::Max(Box.GetSize().Z, Box.GetSize().X / FMath::Max(Cam->GetCameraComponent()->AspectRatio, 0.1f)) + 40.f;
	float Dist = Height * 0.5f / 0.55f / FMath::Tan(HalfV);
	const FSlapHud H = Controller.IsValid() ? Controller->GetHud() : FSlapHud();
	if (H.bStruck && H.Clock > Recovery - 1.5) Dist *= 0.85f;	// the strike: push in for 1.5 s
	const FVector Goal = Box.GetCenter() - Fwd * Dist + FVector::UpVector * (0.12f * 2.f * Dist * FMath::Tan(HalfV));	// below the HUD panel
	const float Dt = GetWorld()->GetDeltaSeconds();
	Cam->SetActorLocation(FMath::VInterpTo(Cam->GetActorLocation(), Goal, Dt, 3.f));
}
