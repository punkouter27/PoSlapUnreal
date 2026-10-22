#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/LightComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Light.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "MuJoCo/Components/Geometry/MjGeom.h"
#include "NiagaraFunctionLibrary.h"
#include "NiagaraSystem.h"
#include "SlapAthleteVisual.h"
#include "SlapController.h"
#include "Sound/SoundAttenuation.h"
#include "Sound/SoundBase.h"
#include "UObject/UObjectIterator.h"
#include "UnrealClient.h"

namespace
{
	constexpr double CleanSpeed = 8.0;	// m/s: a clean strike (Blueprint L2)
	constexpr double KOShot = 4.0;		// s of crane after a KO (USlapController::PostMatchHold)
	const FColor TeamColor[2] = { FColor(235, 70, 60), FColor(70, 130, 240) };
	const FColor Gold(255, 220, 120), Grey(200, 200, 200);
	int32 Team(const FString& Name) { return Name == TEXT("blue") ? 1 : 0; }
	template <typename T> T* Load(const TCHAR* Path) { return LoadObject<T>(nullptr, Path, nullptr, LOAD_Quiet | LOAD_NoWarn); }
}

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
	FParse::Value(FCommandLine::Get(), TEXT("SlapFxTest="), FxTestAt);
	// broadcast assets (Scripts/broadcast_fx.py); a missing one is skipped
	Spray = Load<UNiagaraSystem>(TEXT("/Game/FX/NS_SlapSpray.NS_SlapSpray"));
	SlapSound = Load<USoundBase>(TEXT("/Game/Audio/S_Slap.S_Slap"));
	CrowdReact = Load<USoundBase>(TEXT("/Game/Audio/S_CrowdReact.S_CrowdReact"));
	CrowdLoop = Load<USoundBase>(TEXT("/Game/Audio/S_CrowdLoop.S_CrowdLoop"));
	Bell = Load<USoundBase>(TEXT("/Game/Audio/S_Bell.S_Bell"));
	if (CrowdLoop) UGameplayStatics::SpawnSound2D(this, CrowdLoop, 0.35f);
	Ringside = NewObject<USoundAttenuation>(this);	// spatialised, no fall-off inside the arena: the slap pans, it doesn't fade
	Ringside->Attenuation.bAttenuate = true;
	Ringside->Attenuation.bSpatialize = true;
	Ringside->Attenuation.AttenuationShapeExtents = FVector(1500.f, 0.f, 0.f);
	Ringside->Attenuation.FalloffDistance = 3000.f;
	for (TActorIterator<ALight> It(GetWorld()); It; ++It)	// Scripts/arena_env.py's rig, by label
	{
		// ponytail: actor labels are editor-only data (every -game run here uses the editor binary); a packaged build needs actor tags
		const FString L = It->GetActorNameOrLabel();
		ULightComponent* C = It->GetLightComponent();
		const FStageLight S{ C, C ? C->Intensity : 0.f };
		if (L == TEXT("KeyLight")) Key = S;
		else if (L == TEXT("RimRed")) Rim[0] = S;
		else if (L == TEXT("RimBlue")) Rim[1] = S;
		else if (L.StartsWith(TEXT("TrussSpot")))
		{
			const int32 k = FCString::Atoi(*L.RightChop(9));	// in order round the truss: the KO chase runs round it
			if (k >= Truss.Num()) Truss.SetNum(k + 1);
			Truss[k] = S;
		}
	}
	CommentaryStamp = IFileManager::Get().GetTimeStamp(*(FPaths::ProjectSavedDir() / TEXT("Commentary/latest.txt")));	// only lines from now on
	UE_LOG(LogTemp, Log, TEXT("SlapHUD: spray %s, sounds slap %d crowd %d/%d bell %d, lights key %d rims %d/%d truss %d"), Spray ? TEXT("yes") : TEXT("MISSING"),
		SlapSound != nullptr, CrowdReact != nullptr, CrowdLoop != nullptr, Bell != nullptr, Key.Light.IsValid(), Rim[0].Light.IsValid(), Rim[1].Light.IsValid(), Truss.Num());
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

	Frames.Add(GetWorld()->GetDeltaSeconds());	// performance pass: average fps every 10 s in the log
	if (Frames.Num() > 1 && GetWorld()->GetRealTimeSeconds() - FpsSince >= 10.0)
	{
		double Sum = 0.0, Worst = 0.0;
		for (float F : Frames) { Sum += F; Worst = FMath::Max<double>(Worst, F); }
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: %.1f fps average, worst frame %.1f ms (%d frames)"), Frames.Num() / Sum, Worst * 1000.0, Frames.Num());
		Frames.Reset();
		FpsSince = GetWorld()->GetRealTimeSeconds();
	}

	if (!Controller.IsValid())	// the controller playing the match (URLab may keep more than one instance around)
	{
		for (TObjectIterator<USlapController> It; It; ++It)
		{
			if (!It->HasAnyFlags(RF_ClassDefaultObject | RF_ArchetypeObject) && It->GetHud().Match > 0) { Controller = *It; break; }
		}
	}
	FSlapHud H = Controller.IsValid() ? Controller->GetHud() : FSlapHud();
	const int32 FxDef = Team(H.Defender);
	if (FxTestAt >= 0.f && H.Match > 0 && Body[FxDef].IsValid() && GetWorld()->GetTimeSeconds() >= FxTestAt)	// -SlapFxTest
	{
		H.StrikeSeq = -1;
		H.StrikeDefender = FxDef;
		H.Speed = 9.0;
		H.StrikePos = Body[FxDef]->GetBoneTransformByName(TEXT("head"), EBoneSpaces::WorldSpace).GetLocation() + FVector(FxDef ? -9.f : 9.f, -5.f, -4.f);	// the cheek toward the striker
		H.StrikeDir = FVector(0.f, 1.f, 0.f);
		H.Neck[FxDef] = 0.6;
		H.Stamina[0] = H.Stamina[1] = 0.75;
		if (GetWorld()->GetTimeSeconds() >= FxTestAt + 1.5f) H.KOSeq = -1;
	}
	if (H.Match > 0) Cues(H);
	if (Camera) Direct(*Camera, H);
	if (H.Match > 0)
	{
		DrawTelemetry(H);
		DrawMatch(H);
		DrawOverlays(H);
	}

	const FString Stamp = TEXT(POSLAP_BUILD_VERSION);
	float W = 0, Ht = 0;
	Canvas->StrLen(GEngine->GetSmallFont(), Stamp, W, Ht);
	Canvas->SetDrawColor(FColor(255, 255, 255, 160));
	Canvas->DrawText(GEngine->GetSmallFont(), Stamp, Canvas->ClipX - W - 12.f, Canvas->ClipY - Ht - 12.f);

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

void ASlapHUD::Text(const FString& S, float X, float Y, FColor C, float Scale, bool bCentre, UFont* Font)
{
	Font = Font ? Font : GEngine->GetSmallFont();
	float W = 0, Ht = 0;
	Canvas->StrLen(Font, S, W, Ht);
	Canvas->SetDrawColor(C);
	Canvas->DrawText(Font, S, bCentre ? X - W * Scale * 0.5f : X, Y, Scale, Scale);
}

void ASlapHUD::Cues(const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	if (Skin.Num() == 0)	// one material instance per athlete: M_Grandpa's ripple (RipplePoint / RippleTime / RippleAmp), wear (FlushPoint / Flush / Tired)
	{
		Skin.SetNum(2);
		for (TActorIterator<ASlapAthleteVisual> It(GetWorld()); It; ++It)
		{
			const int32 a = It->BodyPrefix == TEXT("blue_") ? 1 : 0;
			Skin[a] = It->Mesh->CreateDynamicMaterialInstance(0);
			Body[a] = It->Mesh;
		}
	}
	HeadG.Add(H.HeadG);
	if (HeadG.Num() > 120) HeadG.RemoveAt(0);

	if (H.StrikeSeq != SeenStrike)	// the touch: spray along the palm's path, sound, ripple on the struck cheek
	{
		SeenStrike = H.StrikeSeq;
		StrikeAt = Now;
		StrikePos = H.StrikePos;
		StrikeSpeed = H.Speed;
		Struck = H.StrikeDefender;
		const float K = FMath::Clamp(H.Speed / CleanSpeed, 0.25, 1.5);
		if (Spray) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, H.StrikePos, FRotationMatrix::MakeFromZ(H.StrikeDir).Rotator(), FVector(0.3f * K));
		if (SlapSound) UGameplayStatics::PlaySoundAtLocation(this, SlapSound, H.StrikePos, K, FMath::FRandRange(0.92f, 1.08f), 0.f, Ringside);
		if (Body[Struck].IsValid()) FlushLocal[Struck] = Body[Struck]->GetBoneTransformByName(TEXT("head"), EBoneSpaces::WorldSpace).InverseTransformPosition(H.StrikePos);
		if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, 0.3f + 0.5f * K);
		if (Skin.IsValidIndex(Struck) && Skin[Struck])
		{
			Skin[Struck]->SetVectorParameterValue(TEXT("RipplePoint"), FLinearColor(H.StrikePos));
			Skin[Struck]->SetScalarParameterValue(TEXT("RippleAmp"), 0.2f * H.Speed);	// cm: 1.6 at a clean 8 m/s
		}
	}
	for (int32 a = 0; a < Skin.Num(); ++a)	// wear: the flush follows the head; neck strength floors at 0.5, stamina at 0.7
	{
		if (!Skin[a]) continue;
		Skin[a]->SetScalarParameterValue(TEXT("RippleTime"), a == Struck ? Now - StrikeAt : 100.f);
		if (Body[a].IsValid()) Skin[a]->SetVectorParameterValue(TEXT("FlushPoint"), FLinearColor(Body[a]->GetBoneTransformByName(TEXT("head"), EBoneSpaces::WorldSpace).TransformPosition(FlushLocal[a])));
		const double Sting = a == Struck ? 0.5 * FMath::Clamp(StrikeSpeed / CleanSpeed, 0.0, 1.5) * FMath::Exp((StrikeAt - Now) / 2.0) : 0.0;
		Skin[a]->SetScalarParameterValue(TEXT("Flush"), FMath::Clamp((1.0 - H.Neck[a]) / 0.5 + Sting, 0.0, 1.0));
		Skin[a]->SetScalarParameterValue(TEXT("Tired"), FMath::Clamp((1.0 - H.Stamina[a]) / 0.3, 0.0, 1.0));
	}
	if (H.KOSeq != SeenKO)
	{
		SeenKO = H.KOSeq;
		KOAt = Now;
		if (Bell) UGameplayStatics::PlaySound2D(this, Bell);
		if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, 1.4f, 0.9f);
		if (Spray) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, StrikePos, FRotator(90, 0, 0), FVector(0.6f));
	}
	if (H.Round != SeenTurnRound || H.Striker != SeenTurnStriker)	// a new turn: the last turn's commentary is stale
	{
		SeenTurnRound = H.Round;
		SeenTurnStriker = H.Striker;
		TurnAt = Now;
	}
	if (H.MatchSeq != SeenMatch)
	{
		SeenMatch = H.MatchSeq;
		TapeAt = Now;
		HeadG.Reset();
	}
	if (H.ScoredRound != SeenRound)
	{
		SeenRound = H.ScoredRound;
		if (H.ScoredRound > 0) CardAt = Now;
	}
	if (Now - CommentaryPoll > 0.5)	// training/commentator.py writes one line per call
	{
		CommentaryPoll = Now;
		const FString Path = FPaths::ProjectSavedDir() / TEXT("Commentary/latest.txt");
		const FDateTime Stamp = IFileManager::Get().GetTimeStamp(*Path);
		if (Stamp != CommentaryStamp && FFileHelper::LoadFileToString(Commentary, *Path))
		{
			CommentaryStamp = Stamp;
			CommentaryAt = Now;
		}
	}
	Stage(Now);
}

void ASlapHUD::Stage(double Now)
{
	// a strike flares the key and the striker's rim, scaled by the palm's speed; a KO runs a chase round the truss
	auto Set = [](FStageLight& S, double Gain) { if (ULightComponent* L = S.Light.Get()) L->SetIntensity(S.Base * Gain); };
	const double Ts = Now - StrikeAt, Tk = Now - KOAt;
	const double K = FMath::Clamp(StrikeSpeed / CleanSpeed, 0.25, 1.5);
	Set(Key, Ts < 1.0 ? 1.0 + 1.5 * K * FMath::Exp(-8.0 * Ts) : 1.0);
	for (int32 a = 0; a < 2; ++a) Set(Rim[a], a != Struck && Ts < 1.5 ? 1.0 + 4.0 * K * FMath::Exp(-4.0 * Ts) : 1.0);
	for (int32 k = 0; k < Truss.Num(); ++k) Set(Truss[k], Tk < KOShot ? 1.0 + 3.0 * FMath::Pow(FMath::Max(0.0, FMath::Sin(10.0 * Tk - 0.8 * k)), 4.0) : 1.0);
}

void ASlapHUD::Direct(ACameraActor* Cam, const FSlapHud& H)
{
	// Director: WIDE frames both athletes head to toe; TENSION cuts to the defender's face while the palm closes in;
	// STRIKE whips onto the contact point; KO cranes up and around. Escalations cut in at once, the way back waits 1 s.
	const double Now = GetWorld()->GetTimeSeconds();
	if (!bHaveBase)
	{
		BaseRot = Cam->GetActorRotation();
		BaseSat = Cam->GetCameraComponent()->PostProcessSettings.ColorSaturation;
		BaseVignette = Cam->GetCameraComponent()->PostProcessSettings.VignetteIntensity;
		bHaveBase = true;
	}
	FBox Box(ForceInit);
	FVector Head[2] = {}, Pelvis[2] = {};
	for (TActorIterator<ASlapAthleteVisual> It(GetWorld()); It; ++It)
	{
		const int32 a = It->BodyPrefix == TEXT("blue_") ? 1 : 0;
		Head[a] = It->Mesh->GetBoneLocation(TEXT("head"));
		Pelvis[a] = It->Mesh->GetBoneLocation(TEXT("pelvis"));
		for (const TCHAR* Bone : { TEXT("head"), TEXT("pelvis"), TEXT("foot_l"), TEXT("foot_r") })
		{
			const FVector P = It->Mesh->GetBoneLocation(Bone);
			if (!P.IsZero()) Box += P;
		}
	}
	if (!Box.IsValid) return;

	EShot Want = EShot::Wide;
	if (Now - KOAt < KOShot) Want = EShot::KO;
	else if (Now - StrikeAt < 1.6) Want = EShot::Strike;
	else if (H.Match > 0 && !H.bStruck && H.HandDist < 0.55 && H.HandSpeed > 1.5) Want = EShot::Tension;
	bool bCut = false;
	if (Want != Shot && (Want > Shot || Now - ShotStart > 1.0))
	{
		bCut = Want == EShot::Tension || Want == EShot::KO;
		Shot = Want;
		ShotStart = Now;
	}

	const UCameraComponent* CC = Cam->GetCameraComponent();
	const float Aspect = FMath::Max(CC->AspectRatio, 0.1f);
	const float HalfV = FMath::Atan(FMath::Tan(FMath::DegreesToRadians(CC->FieldOfView) * 0.5f) / Aspect);
	const FVector Fwd = BaseRot.Vector(), Up = FVector::UpVector;
	const int32 Def = Team(H.Defender);
	FVector Pos, Look;
	float Speed = 3.f;
	switch (Shot)
	{
	case EShot::Tension:	// the defender's face, close
		Look = Head[Def];
		Pos = Look - Fwd.RotateAngleAxis(Def == 1 ? 18.f : -18.f, Up) * 120.f + Up * 6.f;
		break;
	case EShot::Strike:		// the contact, medium, swung round toward the striker's back
		Look = StrikePos;
		Pos = Look - Fwd.RotateAngleAxis(Def == 1 ? -28.f : 28.f, Up) * 210.f + Up * 10.f;
		Speed = 10.f;
		break;
	case EShot::KO:			// crane: up and around the fallen
	{
		const float T = Now - KOAt;
		Look = 0.5f * (Pelvis[0] + Pelvis[1]);
		Pos = Look - Fwd.RotateAngleAxis(12.f * T, Up) * 330.f + Up * (140.f + 45.f * T);
		Speed = 1.5f;
		break;
	}
	default:				// wide, side-on: both athletes framed (~60 % of the portrait height), below the HUD panel
	{
		const float Height = FMath::Max(Box.GetSize().Z, Box.GetSize().X / Aspect) + 40.f;
		const float Dist = Height * 0.5f / 0.55f / FMath::Tan(HalfV);
		Pos = Box.GetCenter() - Fwd * Dist + Up * (0.12f * 2.f * Dist * FMath::Tan(HalfV));
		Look = Pos + Fwd;
	}
	}
	const FRotator Rot = Shot == EShot::Wide ? BaseRot : (Look - Pos).Rotation();
	const float Dt = GetWorld()->GetDeltaSeconds();
	FVector L = bCut ? Pos : FMath::VInterpTo(Cam->GetActorLocation(), Pos, Dt, Speed);
	const FRotator R = bCut ? Rot : FMath::RInterpTo(Cam->GetActorRotation(), Rot, Dt, Speed * 1.5f);

	// impact: shake and colour fringe, scaled by the palm's speed
	UCameraComponent* Comp = Cam->GetCameraComponent();
	const double Ts = Now - StrikeAt;
	const float K = FMath::Clamp(StrikeSpeed / CleanSpeed, 0.25, 1.5);
	Comp->PostProcessSettings.bOverride_SceneFringeIntensity = true;
	Comp->PostProcessSettings.SceneFringeIntensity = Ts < 1.0 ? 5.f * K * FMath::Exp(-6.0 * Ts) : 0.f;
	// KO grade: the picture drains and closes in while the crane runs
	const double Tk = Now - KOAt;
	const double W = Tk < KOShot ? FMath::Clamp(FMath::Min(Tk / 0.3, (KOShot - Tk) / 0.5), 0.0, 1.0) : 0.0;
	FPostProcessSettings& PP = Comp->PostProcessSettings;
	PP.bOverride_ColorSaturation = PP.bOverride_VignetteIntensity = true;
	PP.ColorSaturation = FMath::Lerp(BaseSat, FVector4(0.3, 0.3, 0.35, 1.0), W);
	PP.VignetteIntensity = FMath::Lerp(BaseVignette, 1.f, float(W));
	if (Ts < 0.6)
	{
		const float A = 3.f * K * FMath::Exp(-7.0 * Ts);
		L += FVector(FMath::PerlinNoise1D(Ts * 40.0), FMath::PerlinNoise1D(Ts * 40.0 + 17.0), FMath::PerlinNoise1D(Ts * 40.0 + 31.0)) * A;
	}
	Cam->SetActorLocationAndRotation(L, R);
}

void ASlapHUD::DrawMatch(const FSlapHud& H)
{
	UFont* Big = GEngine->GetLargeFont();
	UFont* Small = GEngine->GetSmallFont();
	const float S = Canvas->ClipX / 540.f;	// portrait layout, authored at 540 wide
	const float X = 16.f * S;
	float Y = 16.f * S;
	auto Line = [&](UFont* F, const FString& Str, FColor C, float Scale = 1.f)
	{
		Text(Str, X, Y, C, S * Scale, false, F);
		float W, Ht;
		Canvas->StrLen(F, Str, W, Ht);
		Y += Ht * S * Scale + 4.f * S;
	};
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 8.f * S, 8.f * S, 300.f * S, 250.f * S);	// legible over the truss
	const FColor Tc = TeamColor[Team(H.Striker)];
	Line(Big, FString::Printf(TEXT("ROUND %d / 3"), H.Round), FColor::White, 1.2f);
	Line(Small, FString::Printf(TEXT("%s strikes  -  %s %.1f s"), *H.Striker.ToUpper(), H.bStruck ? TEXT("recovery") : TEXT("clock"), FMath::Max(0.0, H.Clock)), Tc, 1.3f);
	if (H.bStruck || H.Speed > 0)
	{
		Line(Small, FString::Printf(TEXT("Hand speed %.1f m/s  (%s)"), H.Speed, *H.Part), Gold, 1.2f);
		Line(Small, FString::Printf(TEXT("Head snap %.1f m/s"), H.HeadPeak), Gold, 1.2f);
	}
	if (!H.Event.IsEmpty()) Line(Small, H.Event, FColor(230, 230, 230), 1.2f);
	Y += 6.f * S;
	Line(Small, TEXT("Judges   RED  BLUE"), Grey, 1.1f);
	for (int32 j = 0; j < 3; ++j)
	{
		Line(Small, FString::Printf(TEXT("  %d        %3d   %3d"), j + 1, H.Cards[j][0], H.Cards[j][1]), Grey, 1.1f);
	}
	Line(Small, FString::Printf(TEXT("Series  RED %d - %d BLUE%s"), H.Wins[0], H.Wins[1], H.Draws ? *FString::Printf(TEXT("  (%d drawn)"), H.Draws) : TEXT("")), FColor(120, 230, 140), 1.1f);
}

void ASlapHUD::DrawTelemetry(const FSlapHud& H)
{
	const float S = Canvas->ClipX / 540.f;
	const int32 Sk = Team(H.Striker);

	// momentum: each brain's critic (its own expected reward over the next ~2 s) against what it usually expects in that
	// role -- a guard expects thousands, a striker about nothing, so raw values don't compare. The bar is the logistic of
	// the difference: who is doing better than usual right now. Not a calibrated win probability.
	double Z[2];
	for (int32 a = 0; a < 2; ++a)
	{
		const int32 R = FMath::Clamp(H.Role[a] + 1, 0, 2);
		if (!bRoleSeen[R])
		{
			RoleMean[R] = H.Value[a];
			RoleVar[R] = FMath::Square(0.1 * FMath::Abs(H.Value[a]) + 1.0);
			bRoleSeen[R] = true;
		}
		const double D = H.Value[a] - RoleMean[R];
		RoleMean[R] += 0.002 * D;	// exponential averages over ~500 frames (~8 s)
		RoleVar[R] = 0.998 * (RoleVar[R] + 0.002 * D * D);
		Z[a] = (H.Value[a] - RoleMean[R]) / FMath::Sqrt(RoleVar[R]);
	}
	Momentum = FMath::FInterpTo(Momentum, 1.f / (1.f + FMath::Exp(Z[1] - Z[0])), GetWorld()->GetDeltaSeconds(), 2.f);
	const float MX = 8.f * S, MY = 264.f * S, MW = 524.f * S, MH = 16.f * S;	// under the panel
	DrawRect(FLinearColor(TeamColor[1]), MX, MY, MW, MH);
	DrawRect(FLinearColor(TeamColor[0]), MX, MY, MW * Momentum, MH);
	DrawLine(MX + MW * 0.5f, MY - 2.f * S, MX + MW * 0.5f, MY + MH + 2.f * S, FLinearColor::White, 2.f * S);
	const int32 Pct = FMath::RoundToInt(100.f * Momentum);
	Text(FString::Printf(TEXT("RED %d%%        MOMENTUM        %d%% BLUE"), Pct, 100 - Pct), Canvas->ClipX * 0.5f, MY + 2.f * S, FColor::White, 0.9f * S, true);

	// balance: a ring under each athlete's soles, the dot is his centre of mass over the floor (green < 5 cm off, red > 10)
	for (int32 a = 0; a < 2; ++a)
	{
		const double Off = FVector2D(H.Com[a] - H.Feet[a]).Size();
		const FColor C = Off < 5.0 ? FColor(90, 230, 120) : Off < 10.0 ? FColor(250, 210, 60) : FColor(250, 80, 60);
		FVector Prev = FVector::ZeroVector;
		for (int32 k = 0; k <= 32; ++k)
		{
			const float Ang = 2.f * PI * k / 32.f;
			const FVector P = Canvas->Project(FVector(H.Feet[a].X, H.Feet[a].Y, 1.0) + FVector(FMath::Cos(Ang), FMath::Sin(Ang), 0.f) * 20.f);
			if (k > 0 && P.Z > 0 && Prev.Z > 0) DrawLine(Prev.X, Prev.Y, P.X, P.Y, FLinearColor(C), 2.f * S);
			Prev = P;
		}
		const FVector Dot = Canvas->Project(FVector(H.Com[a].X, H.Com[a].Y, 1.0));
		if (Dot.Z > 0) DrawRect(FLinearColor(C), Dot.X - 4.f * S, Dot.Y - 4.f * S, 8.f * S, 8.f * S);
	}

	// palm-speed gauge, right edge: live before the touch, the touch's speed after it; the line is the clean 8 m/s
	const float GX = 504.f * S, GY = 380.f * S, GH = 230.f * S, GW = 16.f * S, Max = 12.f;
	const float V = H.bStruck ? H.Speed : H.HandSpeed;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), GX - 4.f * S, GY - 26.f * S, GW + 8.f * S, GH + 52.f * S);
	DrawRect(FLinearColor(TeamColor[Sk]), GX, GY + GH * (1.f - FMath::Clamp(V / Max, 0.f, 1.f)), GW, GH * FMath::Clamp(V / Max, 0.f, 1.f));
	const float Y8 = GY + GH * (1.f - CleanSpeed / Max);
	DrawLine(GX - 4.f * S, Y8, GX + GW + 4.f * S, Y8, FLinearColor::White, 2.f * S);
	Text(TEXT("PALM"), GX + GW * 0.5f, GY - 22.f * S, Grey, S, true);
	Text(FString::Printf(TEXT("%.1f"), V), GX + GW * 0.5f, GY + GH + 6.f * S, H.bStruck && H.Speed >= CleanSpeed ? Gold : FColor::White, S, true);

	// the defender's head acceleration, last 2 s
	const float BX = 16.f * S, BY = 800.f * S, BW = 230.f * S, BH = 70.f * S;
	float Peak = 0.f;
	for (float G : HeadG) Peak = FMath::Max(Peak, G);
	const float Top = FMath::Max(20.f, Peak);
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), BX - 6.f * S, BY - 22.f * S, BW + 12.f * S, BH + 28.f * S);
	Text(FString::Printf(TEXT("HEAD G  %s  peak %.0f g"), *H.Defender.ToUpper(), Peak), BX, BY - 20.f * S, Grey, S);
	for (int32 i = 1; i < HeadG.Num(); ++i)
	{
		const float X0 = BX + BW * (i - 1) / 119.f, X1 = BX + BW * i / 119.f;
		DrawLine(X0, BY + BH * (1.f - HeadG[i - 1] / Top), X1, BY + BH * (1.f - HeadG[i] / Top), FLinearColor(Gold), 2.f * S);
	}

	// fatigue: stamina (joint speed) and neck strength, both from 1 fresh down to their floors
	const float FX = 290.f * S, FY = 800.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), FX - 6.f * S, FY - 22.f * S, 240.f * S, 98.f * S);
	Text(TEXT("CONDITION     STAMINA     NECK"), FX, FY - 20.f * S, Grey, S);
	for (int32 a = 0; a < 2; ++a)
	{
		const float Y = FY + 6.f * S + a * 34.f * S;
		Text(a ? TEXT("BLUE") : TEXT("RED"), FX, Y, TeamColor[a], 1.1f * S);
		const double Bars[2] = { (H.Stamina[a] - 0.7) / 0.3, (H.Neck[a] - 0.5) / 0.5 };
		for (int32 b = 0; b < 2; ++b)
		{
			const float X = FX + (64.f + 82.f * b) * S;
			DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.15f), X, Y + 2.f * S, 70.f * S, 12.f * S);
			DrawRect(FLinearColor(TeamColor[a]), X, Y + 2.f * S, 70.f * S * FMath::Clamp((float)Bars[b], 0.f, 1.f), 12.f * S);
		}
	}
}

void ASlapHUD::DrawOverlays(const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	const float S = Canvas->ClipX / 540.f, Cx = Canvas->ClipX * 0.5f;
	UFont* Big = GEngine->GetLargeFont();

	// impact: a white flash and a shockwave ring from the contact point
	const double Ts = Now - StrikeAt;
	if (Ts < 0.12) DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.3f * (1.f - Ts / 0.12f)), 0, 0, Canvas->ClipX, Canvas->ClipY);
	if (Ts < 0.4)
	{
		const FVector C = Canvas->Project(StrikePos);
		const float R = (40.f + 140.f * Ts / 0.4f) * S * FMath::Clamp(StrikeSpeed / CleanSpeed, 0.5, 1.5);
		const FLinearColor Col(1.f, 0.95f, 0.8f, 1.f - Ts / 0.4f);
		for (int32 k = 0; k < 40 && C.Z > 0; ++k)
		{
			const float A0 = 2.f * PI * k / 40.f, A1 = 2.f * PI * (k + 1) / 40.f;
			DrawLine(C.X + R * FMath::Cos(A0), C.Y + R * FMath::Sin(A0), C.X + R * FMath::Cos(A1), C.Y + R * FMath::Sin(A1), Col, 3.f * S);
		}
	}
	const double Tk = Now - KOAt;
	if (Tk < 0.3) DrawRect(FLinearColor(0.9f, 0.1f, 0.05f, 0.35f * (1.f - Tk / 0.3f)), 0, 0, Canvas->ClipX, Canvas->ClipY);

	// tale of the tape, at every match start: under the panel and the momentum bar, clear of the athletes' heads
	if (Now - TapeAt < 4.0 && H.Result.IsEmpty())	// a match over in under 4 s: the result wins
	{
		const float Y = 286.f * S;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.7f), 8.f * S, Y, 524.f * S, 92.f * S);
		Text(TEXT("TALE OF THE TAPE"), Cx, Y + 4.f * S, Grey, 0.9f * S, true);
		for (int32 a = 0; a < 2; ++a)
		{
			const float Ya = Y + (22.f + 34.f * a) * S;
			Text(H.Name[a], 16.f * S, Ya, TeamColor[a], 1.2f * S);
			Text(H.Tape[a] + TEXT("  ") + H.Brain[a], 16.f * S, Ya + 17.f * S, Grey, 0.85f * S);
		}
	}

	// judges' cards for the round just scored: slide in from the right, hold, slide out
	const double Tc = Now - CardAt;
	if (Tc < 5.0)
	{
		const float In = FMath::InterpEaseOut(0.f, 1.f, FMath::Clamp((float)Tc / 0.4f, 0.f, 1.f), 3.f);
		const float Out = FMath::InterpEaseIn(0.f, 1.f, FMath::Clamp(((float)Tc - 4.5f) / 0.5f, 0.f, 1.f), 3.f);
		const float W = 200.f * S, X = Canvas->ClipX - W * (In - Out) - 8.f * S, Y = 8.f * S;	// top right, beside the panel
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.75f), X, Y, W, 120.f * S);
		Text(FString::Printf(TEXT("ROUND %d CARDS"), H.ScoredRound), X + W * 0.5f, Y + 8.f * S, FColor::White, 1.1f * S, true);
		for (int32 j = 0; j < 3; ++j)
		{
			const float Yj = Y + (36.f + 26.f * j) * S;
			Text(FString::Printf(TEXT("JUDGE %d"), j + 1), X + 12.f * S, Yj, Grey, S);
			Text(FString::FromInt(H.RoundCards[j][0]), X + 120.f * S, Yj, TeamColor[0], 1.2f * S);
			Text(FString::FromInt(H.RoundCards[j][1]), X + 160.f * S, Yj, TeamColor[1], 1.2f * S);
		}
	}

	// the result, while both athletes hold where it ended
	if (!H.Result.IsEmpty())
	{
		const bool KO = H.Result.Contains(TEXT("KO"));
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.7f), 0, 286.f * S, Canvas->ClipX, 70.f * S);	// below the panel and the momentum bar
		Text(KO ? TEXT("KNOCKOUT") : TEXT("DECISION"), Cx, 292.f * S, KO ? FColor(250, 80, 60) : Gold, 1.6f * S, true, Big);
		Text(H.Result, Cx, 328.f * S, FColor::White, 1.2f * S, true);
	}

	// commentary, bottom: the latest line for 8 s, wrapped at ~44 characters
	if (Now - CommentaryAt < 8.0 && CommentaryAt >= TurnAt && !Commentary.IsEmpty())
	{
		TArray<FString> Lines;
		FString Cur;
		TArray<FString> Words;
		Commentary.TrimStartAndEnd().ParseIntoArrayWS(Words);
		for (const FString& Wd : Words)
		{
			if (Cur.Len() + Wd.Len() > 44) { Lines.Add(Cur); Cur.Reset(); }
			Cur += (Cur.IsEmpty() ? TEXT("") : TEXT(" ")) + Wd;
		}
		if (!Cur.IsEmpty()) Lines.Add(Cur);
		const float Y = 712.f * S - Lines.Num() * 20.f * S;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), 16.f * S, Y - 6.f * S, 508.f * S, (Lines.Num() * 20.f + 10.f) * S);
		for (int32 i = 0; i < Lines.Num(); ++i) Text(Lines[i], Cx, Y + i * 20.f * S, FColor::White, 1.1f * S, true);
	}
}
