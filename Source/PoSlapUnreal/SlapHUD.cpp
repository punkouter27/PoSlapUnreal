#include "SlapHUD.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/AudioComponent.h"
#include "Components/DecalComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMeshActor.h"
#include "IWebSocket.h"
#include "WebSocketsModule.h"
#include "Components/LightComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/UserInterfaceSettings.h"
#include "Engine/Light.h"
#include "EngineUtils.h"
#include "Framework/Application/SlateApplication.h"
#include "GameFramework/PlayerController.h"
#include "InputCoreTypes.h"
#include "Input/HittestGrid.h"
#include "HAL/FileManager.h"
#include "Layout/Clipping.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Algo/Accumulate.h"
#include "Algo/Sort.h"
#include "NNEModelData.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "MuJoCo/Components/Geometry/MjGeom.h"
#include "MuJoCo/Core/AMjManager.h"
#include "MuJoCo/Core/MjPhysicsEngine.h"
#include "MuJoCo/Utils/MjUtils.h"
#include "NiagaraFunctionLibrary.h"
#include "NiagaraSystem.h"
#include "SlapAthleteVisual.h"
#include "SlapController.h"
#include "SlapNames.h"
#include "SceneView.h"
#include "Sound/SoundAttenuation.h"
#include "Sound/ReverbEffect.h"
#include "Sound/SoundBase.h"
#include "Styling/CoreStyle.h"
#include "UObject/UObjectIterator.h"
#include "UnrealClient.h"
#include "Brushes/SlateColorBrush.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SUniformGridPanel.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

namespace
{
	FString CommentaryFile() { return FPaths::ProjectSavedDir() / TEXT("Commentary/latest.txt"); }	// Scripts/commentator.py writes it
	FString HypeFile() { return FPaths::ProjectSavedDir() / TEXT("Commentary/hype.json"); }	// Scripts/chat.py writes it
	const FName BoneHead("head"), BonePelvis("pelvis"), BoneFootL("foot_l"), BoneFootR("foot_r"), BoneHandL("hand_l"), BoneHandR("hand_r");
	const FName PRippleTime("RippleTime"), PFlushPoint("FlushPoint"), PFlush("Flush"), PTired("Tired"), PRise("Rise"), PCheer("Cheer"),
		PPrint("Print"), PPrintU("PrintU"), PPrintV("PrintV");
	using Slap::Rounds, Slap::MinStamina, Slap::MinNeck, Slap::CleanSpeed;
	const FColor TeamColor[2] = { FColor(235, 70, 60), FColor(70, 130, 240) };
	const FColor Gold(255, 220, 120), Grey(200, 200, 200);
	constexpr float TopBar = 56.f, BottomBar = 40.f;	// Slate units: the corner anchors' rows (BuildUi)
	const TCHAR* ShotName[] = { TEXT("WALKIN"), TEXT("WIDE"), TEXT("TENSION"), TEXT("STRIKE"), TEXT("KO"), TEXT("REPLAY") };
	int32 Team(const FString& Name) { return Name == TEXT("blue") ? 1 : 0; }
	template <typename T> T* Load(const TCHAR* Path) { return LoadObject<T>(nullptr, Path, nullptr, LOAD_Quiet | LOAD_NoWarn); }
	TSharedPtr<FJsonObject> ReadJson(const FString& Path)	// quiet: a missing or bad file = null
	{
		FString Raw;
		TSharedPtr<FJsonObject> J;
		return FFileHelper::LoadFileToString(Raw, *Path) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Raw), J) ? J : nullptr;
	}
	void Ring(AHUD* Hud, const FVector& C, float R, int32 Sides, const FLinearColor& Col, float Thick)	// screen space
	{
		for (int32 k = 0; k < Sides; ++k)
		{
			const float A0 = 2.f * PI * k / Sides, A1 = 2.f * PI * (k + 1) / Sides;
			Hud->DrawLine(C.X + R * FMath::Cos(A0), C.Y + R * FMath::Sin(A0), C.X + R * FMath::Cos(A1), C.Y + R * FMath::Sin(A1), Col, Thick);
		}
	}
}

void ASlapGameMode::StartPlay()
{
	Super::StartPlay();
	int32 Turned = 0, Total = 0, Hidden = 0;
	for (TActorIterator<AActor> It(GetWorld()); It; ++It)
	{
		TInlineComponentArray<UMjGeom*> Geoms(*It);
		for (UMjGeom* G : Geoms)	// MuJoCo collision shapes are physics only: the skinned athletes and the arena art are drawn
		{
			const FString N = G->GetMjName();
			if (N.Contains(TEXT("red_")) || N.Contains(TEXT("blue_")) || N.EndsWith(TEXT("podium")) || N.EndsWith(TEXT("floor")))
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
	bShotOnResult = FParse::Param(FCommandLine::Get(), TEXT("SlapShotResult"));
	FParse::Value(FCommandLine::Get(), TEXT("SlapFxTest="), FxTestAt);
	bMenu = FParse::Value(FCommandLine::Get(), TEXT("SlapMenu="), Tab);
	bUiTest = FParse::Param(FCommandLine::Get(), TEXT("SlapUiTest"));
	bPairTest = FParse::Param(FCommandLine::Get(), TEXT("SlapPairTest"));
	bLayoutTest = FParse::Param(FCommandLine::Get(), TEXT("SlapLayoutTest"));
	FString View;	// -SlapView=telemetry+science+xray+commentary+debug+noslow: overlays on from the start (screenshots, checks; '+', not commas)
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapView="), View))
	{
		bShowTelemetry = View.Contains(TEXT("telemetry"));
		bShowScience = View.Contains(TEXT("science"));
		bShowXRay = View.Contains(TEXT("xray"));
		bShowCommentary = View.Contains(TEXT("commentary"));
		bShowDebug = View.Contains(TEXT("debug"));
		bSlowMo = !View.Contains(TEXT("noslow"));
	}
	bLive = FParse::Value(FCommandLine::Get(), TEXT("SlapLive="), LiveUrl);
	Tab = FMath::Clamp(Tab, 0, 2);
	FString Zone;	// -SlapSafeZone=0.05+0.03: a fake notch (top) and nav bar (bottom), fractions of the screen height ('+', not a comma)
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapSafeZone="), Zone) && FSlateApplication::IsInitialized())
	{
		FString T, B;
		Zone.Split(TEXT("+"), &T, &B);
		FakeSafe = FMargin(0.f, 2.f * FCString::Atof(*T), 0.f, 2.f * FCString::Atof(*B));	// ratios of the half height; DrawHUD keeps it applied
	}
	BuildUi();
	// broadcast assets (Scripts/broadcast_fx.py); a missing one is skipped
	Spray = Load<UNiagaraSystem>(TEXT("/Game/FX/NS_SlapSpray.NS_SlapSpray"));
	for (const TCHAR* N : { TEXT("S_Slap"), TEXT("S_Slap1"), TEXT("S_Slap2"), TEXT("S_Slap3") })
	{
		if (USoundBase* Snd = Load<USoundBase>(*FString::Printf(TEXT("/Game/Audio/%s.%s"), N, N))) SlapTakes.Add(Snd);
	}
	for (const TCHAR* N : { TEXT("S_Squeak0"), TEXT("S_Squeak1") })
	{
		if (USoundBase* Snd = Load<USoundBase>(*FString::Printf(TEXT("/Game/Audio/%s.%s"), N, N))) Squeaks.Add(Snd);
	}
	Thud = Load<USoundBase>(TEXT("/Game/Audio/S_Thud.S_Thud"));
	Scrape = Load<USoundBase>(TEXT("/Game/Audio/S_Scrape.S_Scrape"));
	BreathLoop = Load<USoundBase>(TEXT("/Game/Audio/S_Breath.S_Breath"));
	ScuffMat = Load<UMaterialInterface>(TEXT("/Game/FX/M_Scuff.M_Scuff"));
	if (UReverbEffect* Hall = Load<UReverbEffect>(TEXT("/Game/Audio/RV_Arena.RV_Arena"))) UGameplayStatics::ActivateReverbEffect(this, Hall, TEXT("Arena"), 0.f, 1.f, 0.f);
	CrowdReact = Load<USoundBase>(TEXT("/Game/Audio/S_CrowdReact.S_CrowdReact"));
	CrowdLoop = Load<USoundBase>(TEXT("/Game/Audio/S_CrowdLoop.S_CrowdLoop"));
	Bell = Load<USoundBase>(TEXT("/Game/Audio/S_Bell.S_Bell"));
	if (CrowdLoop) CrowdAudio = UGameplayStatics::SpawnSound2D(this, CrowdLoop, 0.35f);
	for (TActorIterator<AStaticMeshActor> It(GetWorld()); It; ++It)	// the crowd (Scripts/arena_env.py "Env_Crowd"): Rise and Cheer
	{
		UStaticMeshComponent* M = It->GetStaticMeshComponent();
		if (!M || !It->GetActorNameOrLabel().Contains(TEXT("Crowd"))) continue;
		for (int32 k = 0; k < M->GetNumMaterials(); ++k) CrowdSkin.Add(M->CreateDynamicMaterialInstance(k));
	}
	LoadLeague();
	Ringside = NewObject<USoundAttenuation>(this);	// spatialised, no fall-off inside the arena: the slap pans, it doesn't fade
	Ringside->Attenuation.bAttenuate = true;
	Ringside->Attenuation.bSpatialize = true;
	Ringside->Attenuation.AttenuationShapeExtents = FVector(1500.f, 0.f, 0.f);
	Ringside->Attenuation.FalloffDistance = 3000.f;
	for (int32 a = 0; a < 2 && BreathLoop; ++a) Breath[a] = UGameplayStatics::SpawnSoundAtLocation(this, BreathLoop, FVector::ZeroVector, FRotator::ZeroRotator, 0.01f, 1.f, 0.f, Ringside, nullptr, false);
	for (TActorIterator<ALight> It(GetWorld()); It; ++It)	// Scripts/arena_env.py's rig, by label
	{
		// ponytail: actor labels are editor-only data (every -game run here uses the editor binary); a packaged build needs actor tags
		const FString L = It->GetActorNameOrLabel();
		ULightComponent* C = It->GetLightComponent();
		const FStageLight Lit{ C, C ? C->Intensity : 0.f };
		if (L == TEXT("KeyLight")) Key = Lit;
		else if (L == TEXT("RimRed")) Rim[0] = Lit;
		else if (L == TEXT("RimBlue")) Rim[1] = Lit;
		else if (L.StartsWith(TEXT("TrussSpot")))
		{
			const int32 k = FCString::Atoi(*L.RightChop(9));	// in order round the truss: the KO chase runs round it
			if (k >= Truss.Num()) Truss.SetNum(k + 1);
			Truss[k] = Lit;
		}
	}
	if (USpotLightComponent* L = NewObject<USpotLightComponent>(this, TEXT("FollowSpot")))	// a narrow spot on the striker (Spotlight)
	{
		const ULocalLightComponent* Ref = Truss.Num() ? Cast<ULocalLightComponent>(Truss[0].Light.Get()) : nullptr;
		L->SetMobility(EComponentMobility::Movable);
		L->SetIntensityUnits(Ref ? Ref->IntensityUnits : ELightUnits::Lumens);
		SpotBase = Ref ? 2.f * Ref->Intensity : 30000.f;
		L->SetIntensity(SpotBase);
		L->SetAttenuationRadius(2000.f);
		L->SetInnerConeAngle(7.f);
		L->SetOuterConeAngle(12.f);
		L->SetLightColor(FLinearColor(1.f, 0.93f, 0.82f));
		L->SetCastShadows(!PLATFORM_ANDROID && !PLATFORM_IOS);	// a phone has movable spot shadows off (DefaultEngine.ini)
		if (!GetRootComponent()) SetRootComponent(L);
		L->RegisterComponent();
		Spot = L;
	}
	CommentaryStamp = IFileManager::Get().GetTimeStamp(*CommentaryFile());	// only lines from now on
	UE_LOG(LogTemp, Log, TEXT("SlapHUD: spray %s, sounds slap %d squeak %d thud %d scrape %d breath %d crowd %d/%d bell %d, scuff %d, lights key %d rims %d/%d truss %d, crowd materials %d, league %d brains"), Spray ? TEXT("yes") : TEXT("MISSING"),
		SlapTakes.Num(), Squeaks.Num(), Thud != nullptr, Scrape != nullptr, BreathLoop != nullptr, ScuffMat != nullptr, CrowdReact != nullptr, CrowdLoop != nullptr, Bell != nullptr, Key.Light.IsValid(), Rim[0].Light.IsValid(), Rim[1].Light.IsValid(), Truss.Num(), CrowdSkin.Num(), Scout.Num());
}

void ASlapHUD::DrawHUD()
{
	Super::DrawHUD();

	APlayerController* PC = GetOwningPlayerController();	// view through the level's (portrait) camera
	if (!Camera.IsValid())
	{
		TActorIterator<ACameraActor> It(GetWorld());
		if (It) Camera = *It;
	}
	if (PC && Camera.IsValid() && PC->GetViewTarget() != Camera.Get())
	{
		PC->SetViewTarget(Camera.Get());
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: viewing through %s at %s"), *Camera->GetName(), *Camera->GetActorLocation().ToString());
	}

	Frames.Add(GetWorld()->GetDeltaSeconds());	// performance pass: average fps every 10 s in the log
	if (Frames.Num() > 1 && GetWorld()->GetRealTimeSeconds() - FpsSince >= 10.0)
	{
		const double Sum = Algo::Accumulate(Frames, 0.0), Worst = FMath::Max(Frames);
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: %.1f fps average, worst frame %.1f ms (%d frames)"), Frames.Num() / Sum, Worst * 1000.0, Frames.Num());
		WorstMs = Worst * 1000.0;
		Frames.Reset();
		FpsSince = GetWorld()->GetRealTimeSeconds();
	}
	Fps += 0.05f * (1.f / FMath::Max(GetWorld()->GetDeltaSeconds(), 1e-4f) - Fps);

	// layout rows: below the top bar and above the bottom bar (BuildUi). The engine already shrinks this canvas into the
	// title-safe area (UCanvas::ApplySafeZoneTransform) and the camera keeps it 9:16, so the canvas starts at the safe edge
	// or the letterbox edge, whichever is further in; the bars start at the safe edge
	S = FMath::Min(Canvas->ClipX / 540.f, Canvas->ClipY / 960.f);
	FVector2D View(Canvas->ClipX, Canvas->ClipY);
	if (UGameViewportClient* VC = GetWorld()->GetGameViewport()) VC->GetViewportSize(View);
	if (FakeSafe != FMargin() && FSlateApplication::IsInitialized() && FSlateApplication::Get().GetCustomSafeZone() != FakeSafe)
	{
		FSlateApplication::Get().SetCustomSafeZone(FakeSafe);	// the viewport's own refresh zeroes it, so it is put back whenever it drifts
	}
	Safe = FMargin(0.f);
	if (FSlateApplication::IsInitialized()) FSlateApplication::Get().GetSafeZoneSize(Safe, View);	// pixels
	const float Dpi = DpiScale = GetDefault<UUserInterfaceSettings>()->GetDPIScaleBasedOnSize(FIntPoint(View.X, View.Y));
	const FIntRect R = Canvas->SceneView ? Canvas->SceneView->UnscaledViewRect : FIntRect(0, 0, View.X, View.Y);
	Top = FMath::Max(0.f, Safe.Top + TopBar * Dpi - FMath::Max<float>(R.Min.Y, Safe.Top));
	Bot = Canvas->ClipY - FMath::Max(0.f, Safe.Bottom + BottomBar * Dpi - FMath::Max<float>(View.Y - R.Max.Y, Safe.Bottom));

	// The menu sheet's height budget, in Slate units. The layer's own height is measured, so the budget follows the safe
	// zone and any resize; the sheet's content is then clipped inside it, so no tab can run off the screen and nothing
	// scrolls. The sheet sits above the corner anchors' row (and the platform's bottom inset), so the build stamp and the
	// debug line stay visible while the menu is open.
	{
		const float LayerH = Ui.IsValid() ? Ui->GetCachedGeometry().GetLocalSize().Y : 0.f;
		const float ScreenSlate = LayerH > 1.f ? LayerH : View.Y / FMath::Max(Dpi, 0.01f);
		SafeBottomSlate = Safe.Bottom / FMath::Max(Dpi, 0.01f);
		SheetBudget = FMath::Max(160.f, FMath::Min(ScreenSlate * 0.78f, ScreenSlate - SafeBottomSlate - BottomBar - 24.f));
	}

	if (!Controller.IsValid())	// the controller playing the match (URLab may keep more than one instance around)
	{
		for (TObjectIterator<USlapController> It; It; ++It)
		{
			if (It->HasAnyFlags(RF_ClassDefaultObject | RF_ArchetypeObject)) continue;
			It->GetHud(Last);
			if (Last.Match > 0) { Controller = *It; break; }
		}
	}
	if (Controller.IsValid())
	{
		Controller->GetHud(Last);
		KOShot = Controller->PostMatchHold;
	}
	else Last = FSlapHud();
	if (bTravelling && TravelledAt >= 0.0 && GetWorld()->GetTimeSeconds() - TravelledAt > 6.0)
	{
		// the level never opened: give the menu back instead of leaving every control inert for good
		bTravelling = false;
		TravelledAt = -1.0;
		UE_LOG(LogTemp, Warning, TEXT("SlapHUD: the pairing level did not open within 6 s: the menu is live again"));
	}
	FSlapHud& H = Last;	// -SlapFxTest's fakes below show in the menu too: only that check sees them
	CacheAthletes();
	const int32 FxDef = Team(H.Defender);
	if (FxTestAt >= 0.f && H.Match > 0 && Body[FxDef].IsValid() && GetWorld()->GetTimeSeconds() >= FxTestAt)	// -SlapFxTest
	{
		H.StrikeSeq = -1;
		H.StrikeDefender = FxDef;
		H.Speed = 9.0;
		H.StrikePos = Body[FxDef]->GetBoneTransformByName(BoneHead, EBoneSpaces::WorldSpace).GetLocation() + FVector(FxDef ? -9.f : 9.f, -5.f, -4.f);	// the cheek toward the striker
		H.StrikeDir = FVector(0.f, 1.f, 0.f);
		H.Neck[FxDef] = 0.6;
		H.Stamina[0] = H.Stamina[1] = 0.75;
		if (GetWorld()->GetTimeSeconds() >= FxTestAt + 1.5f) H.KOSeq = -1;
	}
	if (H.Match > 0) Cues(H);
	Spotlight(H);
	Scuffs(H);
	Pant(H);
	if (USlapController* C = Controller.Get())	// the VIEW switches the physics thread reads
	{
		C->bXRay = bShowXRay;
		C->bSlowMo = bSlowMo;
	}
	if (Dilation != H.SlowMo)	// live slow motion: the world clock (effects, camera, cues) runs at the physics pace
	{
		Dilation = H.SlowMo;
		UGameplayStatics::SetGlobalTimeDilation(this, Dilation);
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: slow motion %.2f at %.2f s"), Dilation, GetWorld()->GetTimeSeconds());
	}
	Crowd(H, GetWorld()->GetTimeSeconds());
	Live(H);
	if (Camera.IsValid()) Direct(Camera.Get(), H);
	if (H.Match > 0)
	{
		DrawMatch(H);	// first: the momentum bar and the tape hang off its panel
		if (bShowTelemetry) DrawTelemetry(H);
		if (bShowScience) DrawScience(H);
		if (bShowXRay) DrawXRay(H);
		DrawOverlays(H);
		if (GetWorld()->GetTimeSeconds() < ReplayEnd) Text(bQuarter ? TEXT("REPLAY   1/4 SPEED") : TEXT("REPLAY   1/2 SPEED"), Canvas->ClipX * 0.5f, PanelBottom + (bShowTelemetry ? 28.f : 6.f) * S, Gold, 1.4f * S, true, GEngine->GetMediumFont());
	}

	if (bUiTest) UiTest(H);
	if (bLayoutTest) LayoutAudit();
	if (bPairTest && H.Match > 0 && GetWorld()->GetTimeSeconds() >= 3.0 && Pairings.IsValidIndex(HerePairing))
	{
		bPairTest = false;
		if (Pairings[HerePairing].AssetName == TEXT("L_Arena")) Travel((HerePairing + 1) % Pairings.Num());
		else
		{
			UE_LOG(LogTemp, Log, TEXT("SlapHUD: pair test: in %s, %s vs %s, brains %s / %s"), *Pairings[HerePairing].AssetName.ToString(), *H.Card[0], *H.Card[1], *H.Brain[0], *H.Brain[1]);
			UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
		}
	}
	if (bShotOnResult && ShotAt < 0.f && !H.Result.IsEmpty()) ShotAt = GetWorld()->GetTimeSeconds() + 0.5f;
	if (ShotAt >= 0.f && GetWorld()->GetTimeSeconds() >= ShotAt)
	{
		const FString ShotFile = FPaths::ProjectSavedDir() / TEXT("Screenshots/slapshot.png");
		if (!bShotTaken)
		{
			bShotTaken = true;
			ShotRequested = FDateTime::UtcNow();
			IFileManager::Get().Delete(*ShotFile);	// a stale file must not count as this run's shot
			FScreenshotRequest::RequestScreenshot(ShotFile, true, false);	// with the Slate layer
		}
		else if (IFileManager::Get().FileSize(*ShotFile) > 0)	// quit once the file is on disk (a fixed delay lost the race)
		{
			UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
		}
		else if ((FDateTime::UtcNow() - ShotRequested).GetTotalSeconds() > 5.0)	// a viewport that skipped drawing drops the request: ask again
		{
			if (++ShotRetries > 2)
			{
				UE_LOG(LogTemp, Warning, TEXT("SlapHUD: -SlapShot: no screenshot after 3 requests (window hidden?), quitting"));
				UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
			}
			ShotRequested = FDateTime::UtcNow();
			FScreenshotRequest::RequestScreenshot(ShotFile, true, false);
		}
	}
}

void ASlapHUD::EndPlay(const EEndPlayReason::Type Reason)
{
	UGameViewportClient* VC = GetWorld()->GetGameViewport();
	if (VC && Ui.IsValid()) VC->RemoveViewportWidgetContent(Ui.ToSharedRef());
	Ui.Reset();
	if (Socket) Socket->Close();
	Socket.Reset();
	Super::EndPlay(Reason);
}

void ASlapHUD::BuildUi()
{
	// Corners (inside the device safe zone): title, FPS, menu | debug, build stamp. The menu is a bottom sheet over a scrim
	// with three tabs; tapping the scrim or X closes it. Sizes are Slate units (1080-wide reference, DPI-scaled).
	Targets.Reset();
	UGameViewportClient* VC = GetWorld()->GetGameViewport();
	if (!VC) return;	// no viewport (commandlet, server)
	if (APlayerController* PC = GetOwningPlayerController())	// taps and clicks reach the buttons
	{
		PC->SetShowMouseCursor(true);
		PC->SetInputMode(FInputModeGameAndUI().SetHideCursorDuringCapture(false));
	}
	IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	auto Find = [&AR](const TCHAR* Path, UClass* Class, bool bRecursive, TArray<FAssetData>& Out)
	{
		AR.ScanPathsSynchronous({ Path });
		FARFilter F;
		F.PackagePaths.Add(Path);
		F.bRecursivePaths = bRecursive;
		F.ClassPaths.Add(Class->GetClassPathName());
		AR.GetAssets(F, Out);
	};
	Find(TEXT("/Game/Brains"), UNNEModelData::StaticClass(), true, Brains);
	Brains.Sort([](const FAssetData& A, const FAssetData& B) { return A.AssetName.LexicalLess(B.AssetName); });
	// only the roster's brains are offered (training/roster.json); a build without training/ (Android) keeps every brain
	FString RosterRaw;
	TArray<TSharedPtr<FJsonValue>> Roster;
	if (FFileHelper::LoadFileToString(RosterRaw, *(FPaths::ProjectDir() / TEXT("training/roster.json"))) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(RosterRaw), Roster) && Roster.Num())
	{
		TSet<FString> Keep;
		for (const TSharedPtr<FJsonValue>& R : Roster) Keep.Add(FPaths::GetBaseFilename(R->AsObject()->GetStringField(TEXT("brain"))));
		Brains.RemoveAll([&Keep](const FAssetData& B) { return !Keep.Contains(B.AssetName.ToString()); });
	}
	Find(TEXT("/Game/Levels"), UWorld::StaticClass(), false, Pairings);
	Pairings.RemoveAll([](const FAssetData& A) { return !A.AssetName.ToString().StartsWith(TEXT("L_Arena")); });
	Pairings.Sort([](const FAssetData& A, const FAssetData& B) { return A.AssetName.LexicalLess(B.AssetName); });
	const FName Here(*UGameplayStatics::GetCurrentLevelName(this));	// PIE prefix stripped
	Pairing = HerePairing = Pairings.IndexOfByPredicate([&](const FAssetData& A) { return A.AssetName == Here; });

	LoadRoster();

	TWeakObjectPtr<ASlapHUD> Self(this);
	TSharedPtr<SWidget> Spare;	// SAssignNew needs somewhere to put the widgets the test doesn't click
	static const FSlateRoundedBoxBrush SheetBrush(FLinearColor(0.02f, 0.02f, 0.03f, 0.95f), FVector4(18.f, 18.f, 0.f, 0.f));
	static const FSlateColorBrush ScrimBrush(FLinearColor(0.f, 0.f, 0.f, 0.5f));
	const FLinearColor White = FLinearColor::White, Dim(Grey), Hi(Gold);
	auto Label = [](const TAttribute<FText>& T, int32 Size, const TAttribute<FSlateColor>& C, bool bWrap = false)
	{
		return SNew(STextBlock).Text(T).Font(FCoreStyle::GetDefaultFontStyle("Bold", Size)).ColorAndOpacity(C).ShadowOffset(FVector2D(1.0, 1.0)).AutoWrapText(bWrap);
	};
	auto Menu = [Self](bool bOpen)
	{
		if (Self.IsValid() && Self->bMenu != bOpen) UE_LOG(LogTemp, Log, TEXT("SlapHUD: menu %s"), bOpen ? TEXT("open") : TEXT("closed"));
		if (Self.IsValid()) Self->bMenu = bOpen;
		return FReply::Handled();
	};
	auto Close = [Menu] { return Menu(false); };
	static constexpr float Touch = 44.f;	// minimum touch target (Slate units, ~7 mm on a phone)
	auto TabButton = [&](int32 i, const TCHAR* Name)
	{
		TSharedRef<SBox> Box = SNew(SBox).MinDesiredHeight(Touch)
			[SAssignNew(i == 0 ? Hit[HitTabMatch] : i == 1 ? Hit[HitTabRoster] : Spare, SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda([Self, i] { if (Self.IsValid()) Self->Tab = i; return FReply::Handled(); })
			[Label(FText::FromString(Name), 18, TAttribute<FSlateColor>::CreateLambda([Self, i, Hi, Dim] { return FSlateColor(Self.IsValid() && Self->Tab == i ? Hi : Dim); }))]];
		if (Self.IsValid()) Self->Targets.Add(Box);
		return Box;
	};
	// view badge: a short code in a pill, gold when on; the ABOUT tab spells each one out
	static const FSlateRoundedBoxBrush BadgeOn(FLinearColor(0.55f, 0.42f, 0.1f, 0.9f), 10.f), BadgeOff(FLinearColor(1.f, 1.f, 1.f, 0.08f), 10.f);
	auto Badge = [&](const TCHAR* Code, bool ASlapHUD::* Flag)
	{
		TSharedPtr<SBox> BadgeBox;
		TSharedRef<SCheckBox> Box = SNew(SCheckBox).Style(&FCoreStyle::Get().GetWidgetStyle<FCheckBoxStyle>("ToggleButtonCheckbox"))
			.IsChecked_Lambda([Self, Flag] { return Self.IsValid() && (*Self).*Flag ? ECheckBoxState::Checked : ECheckBoxState::Unchecked; })
			.OnCheckStateChanged_Lambda([Self, Flag](ECheckBoxState St) { if (Self.IsValid()) (*Self).*Flag = St == ECheckBoxState::Checked; })
			[
				SNew(SBorder).Padding(0.f).VAlign(VAlign_Center)
				.BorderImage_Lambda([Self, Flag] { return Self.IsValid() && (*Self).*Flag ? &BadgeOn : &BadgeOff; })
				[SAssignNew(BadgeBox, SBox).MinDesiredHeight(Touch).HAlign(HAlign_Center).VAlign(VAlign_Center)
					[Label(FText::FromString(Code), 16, TAttribute<FSlateColor>::CreateLambda([Self, Flag, Hi, Dim] { return FSlateColor(Self.IsValid() && (*Self).*Flag ? FLinearColor::White : Dim); }))]]
			];
		if (Self.IsValid()) Self->Targets.Add(Box);	// the check box is the target: the audit checks every one is at least 44 units
		if (Flag == &ASlapHUD::bShowTelemetry) Hit[HitTelemetry] = Box;
		return Box;
	};
	static const FSlateColorBrush Bar(FLinearColor::White);
	auto Burger = []	// the menu icon: three bars (no icon font needed)
	{
		TSharedRef<SVerticalBox> V = SNew(SVerticalBox);
		for (int32 k = 0; k < 3; ++k) V->AddSlot().AutoHeight().Padding(0.f, 2.5f)[SNew(SBox).WidthOverride(22.f).HeightOverride(3.f)[SNew(SImage).Image(&Bar)]];
		return V;
	};
	auto DebugVis = [Self] { return Self.IsValid() && Self->bShowDebug ? EVisibility::HitTestInvisible : EVisibility::Collapsed; };
	auto MatchText = [Self]	// one dense line: the tape and the brains live on the corner rows and the ROSTER tab
	{
		if (!Self.IsValid() || Self->Last.Match == 0) return FText::FromString(TEXT("Waiting for the first match."));
		if (Self->bTravelling) return FText::FromString(TEXT("Loading the pairing..."));
		const FSlapHud& H = Self->Last;
		FString T = FString::Printf(TEXT("M%d  R%d/%d  %s >  SERIES %d-%d"), H.Match, H.Round, Rounds, *H.Striker.ToUpper(), H.Wins[0], H.Wins[1]);
		if (H.Draws) T += FString::Printf(TEXT(" (%d D)"), H.Draws);
		if (!H.Result.IsEmpty()) T += TEXT("\n") + H.Result;
		return FText::FromString(T);
	};
	auto Fits = [Self](int32 a)	// brains made for corner a's character
	{
		TArray<int32> Out;
		if (!Self.IsValid() || !Self->Controller.IsValid()) return Out;
		const FString Card = Self->Last.Card[a] + TEXT("_");
		for (int32 i = 0; i < Self->Brains.Num(); ++i)
		{
			if (Self->Brains[i].AssetName.ToString().StartsWith(Card)) Out.Add(i);
		}
		return Out;
	};
	auto Step = [Self, Fits](int32 a, int32 Dir)
	{
		const TArray<int32> F = Fits(a);	// empty when the HUD or the controller is gone: nothing below runs
		if (F.Num() == 0 || Self->bTravelling || Self->Pairing != Self->HerePairing) return FReply::Handled();	// another pairing: its own brains, picked there
		int32& P = Self->Pick[a];
		if (P == INDEX_NONE)	// from the brain the corner plays now
		{
			const FName Now(*Self->Last.Brain[a]);
			P = Self->Brains.IndexOfByPredicate([&](const FAssetData& B) { return B.AssetName == Now; });
		}
		const int32 k = F.Find(P);
		P = F[k == INDEX_NONE ? 0 : (k + Dir + F.Num()) % F.Num()];
		return FReply::Handled();
	};
	auto Arrow = [&](const TCHAR* Glyph, TFunction<FReply()> Click, TSharedPtr<SWidget>& Out)	// a thumb-sized stepper button
	{
		TSharedRef<SBox> Box = SNew(SBox).WidthOverride(Touch).HeightOverride(Touch)
			[SAssignNew(Out, SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda(MoveTemp(Click))[Label(FText::FromString(Glyph), 18, White)]];
		if (Self.IsValid()) Self->Targets.Add(Box);	// the audit checks every target is at least 44 units
		return Box;
	};
	auto Corner = [&](int32 a)
	{
		return SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
			[SNew(SBox).WidthOverride(64.f)[Label(FText::FromString(a ? TEXT("BLUE") : TEXT("RED")), 18, FLinearColor(TeamColor[a]))]]
			+ SHorizontalBox::Slot().AutoWidth()[Arrow(TEXT("<"), [Step, a] { return Step(a, -1); }, Spare)]
			+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center).HAlign(HAlign_Center)
			[Label(TAttribute<FText>::CreateLambda([Self, a]
			{
				if (!Self.IsValid()) return FText();
				if (Self->Pairing != Self->HerePairing) return FText::FromString(TEXT("level default"));
				const int32 P = Self->Pick[a];
				return FText::FromString(Self->Brains.IsValidIndex(P) ? Self->Brains[P].AssetName.ToString() : Self->Last.Brain[a]);
			}), 17, Hi)]
			+ SHorizontalBox::Slot().AutoWidth()[Arrow(TEXT(">"), [Step, a] { return Step(a, 1); }, a == 0 ? Hit[HitRedNext] : Spare)];
	};
	auto PairStep = [Self](int32 Dir)
	{
		if (Self.IsValid() && !Self->bTravelling && Self->Pairings.Num() > 0) Self->Pairing = (FMath::Max(Self->Pairing, 0) + Dir + Self->Pairings.Num()) % Self->Pairings.Num();
		return FReply::Handled();
	};
	TSharedRef<SWidget> PairRow = SNew(SHorizontalBox)
		+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)[SNew(SBox).WidthOverride(64.f)[Label(FText::FromString(TEXT("VS")), 18, Dim)]]
		+ SHorizontalBox::Slot().AutoWidth()[Arrow(TEXT("<"), [PairStep] { return PairStep(-1); }, Spare)]
		+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center).HAlign(HAlign_Center)
		[Label(TAttribute<FText>::CreateLambda([Self]
		{
			if (!Self.IsValid() || !Self->Pairings.IsValidIndex(Self->Pairing)) return FText::FromString(TEXT("this level"));
			// "L_Arena_kim_vs_grandpa" -> "KIM vs GRANDPA"; plain "L_Arena" is build_scene.py's default pairing
			const FString N = Self->Pairings[Self->Pairing].AssetName.ToString();
			const FString P = N == TEXT("L_Arena") ? TEXT("GRANDPA vs GRANDPA") : N.RightChop(8).ToUpper().Replace(TEXT("_VS_"), TEXT(" vs "));
			return FText::FromString(Self->Pairing == Self->HerePairing ? P : P + TEXT("  (loads)"));
		}), 17, Hi)]
		+ SHorizontalBox::Slot().AutoWidth()[Arrow(TEXT(">"), [PairStep] { return PairStep(1); }, Spare)];
	TSharedRef<SUniformGridPanel> Grid = SNew(SUniformGridPanel).SlotPadding(FMargin(2.f)).MinDesiredSlotHeight(Touch).MinDesiredSlotWidth(Touch);	// the roster: 4 x 4 slots, each a thumb target
	for (int32 i = 0; i < RosterSize; ++i)
	{
		const bool bOpen = Slots[i].Key.IsEmpty();
		Grid->AddSlot(i % 4, i / 4)
		[
			SNew(SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda([Self, i] { if (Self.IsValid()) Self->RosterSel = i; return FReply::Handled(); })
			[Label(FText::FromString(FString::Printf(TEXT("%d\n%s"), i + 1, bOpen ? TEXT("open") : *Slots[i].Key)), 14,
				TAttribute<FSlateColor>::CreateLambda([Self, i, bOpen, Hi, White, Dim] { return FSlateColor(Self.IsValid() && Self->RosterSel == i ? Hi : bOpen ? Dim * 0.7f : White); }))]
		];
	}
	auto Start = [Self, Menu]
	{
		if (!Self.IsValid() || Self->bTravelling) return FReply::Handled();	// a second tap while the level opens does nothing
		if (Self->Pairing != Self->HerePairing) { Self->Travel(Self->Pairing); return Menu(false); }
		if (!Self->Controller.IsValid()) return FReply::Handled();
		UNNEModelData* B[2] = {};
		for (int32 a = 0; a < 2; ++a)
		{
			if (Self->Brains.IsValidIndex(Self->Pick[a])) B[a] = Cast<UNNEModelData>(Self->Brains[Self->Pick[a]].GetAsset());
		}
		Self->Controller->RequestMatch(B[0], B[1]);
		Self->Pick[0] = Self->Pick[1] = INDEX_NONE;	// the corners play them now
		return Menu(false);
	};
	auto ReplayClick = [Self, Menu]	// replay over the live picture: the sheet closes so the replay is seen
	{
		if (!Self.IsValid() || Self->bTravelling) return FReply::Handled();
		Self->InstantReplay();
		return Menu(false);
	};
	auto ReplayLabel = [Self]
	{
		const bool bOn = Self.IsValid() && Self->GetWorld() && Self->GetWorld()->GetTimeSeconds() < Self->ReplayEnd;
		return FText::FromString(bOn ? TEXT("LIVE") : Self.IsValid() && Self->bQuarter ? TEXT("REPLAY 1/4") : TEXT("REPLAY 1/2"));
	};
	auto Rematch = [Self]	// the result card: the same two brains again, now (the hold would start the next match anyway)
	{
		if (!Self.IsValid() || Self->bTravelling || !Self->Controller.IsValid()) return FReply::Handled();
		Self->Controller->RequestMatch(nullptr, nullptr);
		Self->bRematched = true;
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: rematch"));
		return FReply::Handled();
	};
	auto CardButton = [&](const TCHAR* Name, FLinearColor Colour, TFunction<FReply()> Click, TSharedPtr<SWidget>& Out)
	{
		TSharedRef<SBox> Box = SNew(SBox).MinDesiredHeight(Touch)
			[SAssignNew(Out, SButton).ButtonColorAndOpacity(Colour).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda(MoveTemp(Click))[Label(FText::FromString(Name), 18, White)]];
		if (Self.IsValid()) Self->Targets.Add(Box);
		return Box;
	};
	static const FSlateRoundedBoxBrush CardBrush(FLinearColor(0.f, 0.f, 0.f, 0.8f), 14.f);
	static const FSlateRoundedBoxBrush GripBrush(FLinearColor(0.f, 0.f, 0.f, 0.45f), 12.f);
	auto Resulted = [Self] { return Self.IsValid() && !Self->bMenu && !Self->Last.Result.IsEmpty() ? EVisibility::Visible : EVisibility::Collapsed; };
	TSharedPtr<SBox> GripBox;

	Ui = SNew(SOverlay)
		+ SOverlay::Slot()	// the play area, under everything: a sideways swipe steps the view presets, a double tap replays
		[
			SAssignNew(Hit[HitPad], SBorder).BorderImage(FCoreStyle::Get().GetBrush("NoBorder")).Visibility(EVisibility::Visible)
			.OnMouseButtonDown_Lambda([Self](const FGeometry&, const FPointerEvent& E) { if (Self.IsValid()) Self->PadDown = E.GetScreenSpacePosition(); return FReply::Handled(); })
			.OnMouseButtonUp_Lambda([Self](const FGeometry& G, const FPointerEvent& E)
			{
				if (!Self.IsValid()) return FReply::Handled();
				const FVector2D D = (E.GetScreenSpacePosition() - Self->PadDown) / FMath::Max(G.GetAccumulatedLayoutTransform().GetScale(), 0.01f);	// Slate units
				if (FMath::Abs(D.X) > 80.f && FMath::Abs(D.X) > 2.f * FMath::Abs(D.Y)) Self->CyclePreset(D.X < 0.f ? 1 : -1);
				return FReply::Handled();
			})
			.OnMouseDoubleClick_Lambda([Self](const FGeometry&, const FPointerEvent&)
			{
				if (Self.IsValid() && !Self->bTravelling && Self->Last.Match > 0) Self->InstantReplay();
				return FReply::Handled();
			})
		]
		+ SOverlay::Slot()
		[
			// the safe insets DrawHUD reads each frame (pixels -> Slate units): the corners and the canvas follow one source.
			// (SSafeZone in the editor binary keeps a stale margin after the insets change.)
			SNew(SBox).Padding(TAttribute<FMargin>::CreateLambda([Self] { return Self.IsValid() ? Self->Safe * (1.f / FMath::Max(Self->DpiScale, 0.01f)) : FMargin(0.f); }))
			[
				SNew(SOverlay)
				+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Top)
				[
					SAssignNew(Anchor[0], SBox).HeightOverride(TopBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
					[Label(FText::FromString(TEXT("POSLAP")), 22, White)]
				]
				+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Top)	// TC: frame rate and the MuJoCo step counter, always on
				[
					SAssignNew(Anchor[1], SBox).HeightOverride(TopBar).VAlign(VAlign_Center)
					[
						SNew(SBorder).BorderImage(&ScrimBrush).Padding(FMargin(8.f, 2.f))
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
							[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(FString::Printf(TEXT("%d FPS"), Self.IsValid() ? FMath::RoundToInt(Self->Fps) : 0)); }), 15,
								TAttribute<FSlateColor>::CreateLambda([Self] { const float F = Self.IsValid() ? Self->Fps : 0.f; return FSlateColor(F >= 55.f ? FLinearColor(0.35f, 0.9f, 0.45f) : F >= 30.f ? FLinearColor(1.f, 0.8f, 0.2f) : FLinearColor(1.f, 0.3f, 0.25f)); }))]
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(FMargin(10.f, 0.f, 0.f, 0.f))
							[Label(TAttribute<FText>::CreateLambda([Self]
							{
								const int64 N = Self.IsValid() ? Self->Last.Steps : 0;	// k above 10 000 so the badge never widens past the corners
								return FText::FromString(N >= 10000000 ? FString::Printf(TEXT("STEP %.1fM"), N / 1e6) : N >= 10000 ? FString::Printf(TEXT("STEP %lldk"), N / 1000) : FString::Printf(TEXT("STEP %lld"), N));
							}), 15, Dim)]
						]
					]
				]
				+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Top)	// TR: the menu, an icon at thumb size
				[
					SAssignNew(Anchor[2], SBox).HeightOverride(TopBar).VAlign(VAlign_Center).Padding(FMargin(8.f, 0.f))
					[
						SNew(SBox).WidthOverride(Touch).HeightOverride(Touch)
						[SAssignNew(Hit[HitMenu], SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).ToolTipText(FText::FromString(TEXT("Menu"))).OnClicked_Lambda([Self, Menu] { return Menu(Self.IsValid() && !Self->bMenu); })[Burger()]]
					]
				]
				+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Bottom)	// BL: physics debug (DBG badge)
				[
					SAssignNew(Anchor[3], SBox).HeightOverride(BottomBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
					.Visibility_Lambda(DebugVis)
					[SNew(SBorder).BorderImage(&ScrimBrush).Padding(FMargin(6.f, 2.f))[Label(TAttribute<FText>::CreateLambda([Self]
					{
						if (!Self.IsValid()) return FText();
						const FSlapHud& H = Self->Last;
						return FText::FromString(FString::Printf(TEXT("SIM %.1fs  dt %.0fms  x%.2f  CAM %s  %.0fms"), H.Steps * H.Timestep, H.Timestep * 1000.0, H.SlowMo, ShotName[(int32)Self->Shot], Self->WorstMs));
					}), 13, Dim)]]
				]
				+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Bottom)	// BR: build stamp
				[
					SAssignNew(Anchor[4], SBox).HeightOverride(BottomBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
					[SNew(SBorder).BorderImage(&ScrimBrush).Padding(FMargin(6.f, 2.f))[Label(FText::FromString(TEXT(POSLAP_BUILD_VERSION)), 14, FLinearColor(1.f, 1.f, 1.f, 0.8f))]]
				]
				+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Bottom)	// BC: a grip that opens the menu under the thumb (TR is a stretch one-handed)
				[
					SAssignNew(GripBox, SBox).WidthOverride(120.f).HeightOverride(Touch)
					[
						SAssignNew(Hit[HitGrip], SButton).ButtonStyle(&FCoreStyle::Get().GetWidgetStyle<FButtonStyle>("NoBorder")).HAlign(HAlign_Center).VAlign(VAlign_Center)
						.ToolTipText(FText::FromString(TEXT("Menu"))).OnClicked_Lambda([Self, Menu] { return Menu(Self.IsValid() && !Self->bMenu); })
						[SNew(SBorder).BorderImage(&GripBrush).Padding(FMargin(18.f, 8.f))[SNew(SBox).WidthOverride(44.f).HeightOverride(4.f)[SNew(SImage).Image(&Bar)]]]
					]
				]
				+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Bottom).Padding(FMargin(24.f, 0.f, 24.f, BottomBar + 120.f))	// Win/Loss: the lower third, above the telemetry strips
				[
					SNew(SBorder).BorderImage(&CardBrush).Padding(FMargin(22.f, 14.f)).Visibility_Lambda(Resulted)
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center)
						[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(Self.IsValid() && Self->Last.Result.Contains(TEXT("KO")) ? TEXT("KNOCKOUT") : TEXT("DECISION")); }), 30,
							TAttribute<FSlateColor>::CreateLambda([Self, Hi] { return FSlateColor(Self.IsValid() && Self->Last.Result.Contains(TEXT("KO")) ? FLinearColor(FColor(250, 80, 60)) : Hi); }))]
						+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.f, 2.f)
						[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(Self.IsValid() ? Self->Last.Result : FString()); }), 18, White)]
						+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.f, 4.f, 0.f, 10.f)
						[Label(TAttribute<FText>::CreateLambda([Self]
						{
							if (!Self.IsValid()) return FText();
							const FSlapHud& H = Self->Last;
							return FText::FromString(FString::Printf(TEXT("JUDGES %d:%d  %d:%d  %d:%d     SERIES %d-%d%s"), H.Cards[0][0], H.Cards[0][1], H.Cards[1][0], H.Cards[1][1], H.Cards[2][0], H.Cards[2][1],
								H.Wins[0], H.Wins[1], H.Draws ? *FString::Printf(TEXT(" (%d D)"), H.Draws) : TEXT("")));
						}), 15, Dim)]
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(FMargin(0.f, 0.f, 4.f, 0.f))[CardButton(TEXT("REMATCH"), FLinearColor(0.75f, 0.2f, 0.15f), Rematch, Hit[HitRematch])]
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(FMargin(4.f, 0.f, 0.f, 0.f))[CardButton(TEXT("MENU"), FLinearColor::White, [Menu] { return Menu(true); }, Spare)]
						]
					]
				]
			]
		]
		+ SOverlay::Slot()
		[
			SNew(SOverlay)
			.Visibility_Lambda([Self] { return Self.IsValid() && Self->bMenu ? EVisibility::Visible : EVisibility::Collapsed; })
			+ SOverlay::Slot()
			[SAssignNew(Hit[HitScrim], SBorder).BorderImage(&ScrimBrush).OnMouseButtonDown_Lambda([Close](const FGeometry&, const FPointerEvent&) { return Close(); })]
			+ SOverlay::Slot().VAlign(VAlign_Bottom)
			// the sheet sits above the corner anchors' row (and the platform's bottom inset), so the build stamp and the
			// debug line stay visible while the menu is open
			.Padding(TAttribute<FMargin>::CreateLambda([Self] { return FMargin(0.f, 0.f, 0.f, BottomBar + (Self.IsValid() ? Self->SafeBottomSlate : 0.f)); }))
			[
				SAssignNew(SheetRoot, SBorder).BorderImage(&SheetBrush).Padding(FMargin(12.f, 12.f, 12.f, 4.f))
				[
					SNew(SBox).Padding(TAttribute<FMargin>::CreateLambda([Self]	// the side insets; the sheet already sits above the bottom one
					{
						const float K = Self.IsValid() ? 1.f / FMath::Max(Self->DpiScale, 0.01f) : 0.f;
						return Self.IsValid() ? FMargin(Self->Safe.Left * K, 0.f, Self->Safe.Right * K, 0.f) : FMargin(0.f);
					}))
					[
						// The sheet's height budget: the box is capped to it and clips whatever does not fit, so no tab can
						// run off the screen and nothing scrolls (the zero-scroll rule). The budget is measured from the
						// layer, so it follows the safe zone and any resize.
						SAssignNew(SheetBody, SBox)
						.MaxDesiredHeight_Lambda([Self] { return FOptionalSize(Self.IsValid() ? FMath::Max(120.f, Self->SheetBudget) : 600.f); })
						[
							SNew(SVerticalBox)
							+ SVerticalBox::Slot().FillHeight(1.f).Padding(FMargin(4.f, 0.f, 4.f, 8.f))
							[
								SAssignNew(SheetContent, SWidgetSwitcher).WidgetIndex_Lambda([Self] { return Self.IsValid() ? Self->Tab : 0; })
								+ SWidgetSwitcher::Slot()	// MATCH: status, pairing, brains, START + replay, view badges: one screen
								[
									SNew(SVerticalBox)
									+ SVerticalBox::Slot().AutoHeight()[Label(TAttribute<FText>::CreateLambda(MatchText), 15, White)]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 2.f)[PairRow]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 2.f)[Corner(0)]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 2.f)[Corner(1)]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 0.f)
									[
										SNew(SHorizontalBox)
										+ SHorizontalBox::Slot().FillWidth(2.f).Padding(FMargin(0.f, 0.f, 4.f, 0.f))
										[
											SNew(SBox).MinDesiredHeight(Touch)
											[SAssignNew(Hit[HitStart], SButton).ButtonColorAndOpacity(FLinearColor(0.75f, 0.2f, 0.15f)).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda(Start)[Label(FText::FromString(TEXT("START")), 18, White)]]
										]
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(FMargin(4.f, 0.f, 4.f, 0.f))
										[
											SNew(SBox).MinDesiredHeight(Touch)
											[SAssignNew(Hit[HitReplay], SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).IsEnabled_Lambda([Self] { return Self.IsValid() && Self->Last.Match > 0; }).OnClicked_Lambda(ReplayClick)[Label(TAttribute<FText>::CreateLambda(ReplayLabel), 16, White)]]
										]
										+ SHorizontalBox::Slot().FillWidth(0.6f)[Badge(TEXT("x1/4"), &ASlapHUD::bQuarter)]	// the replay's pace, beside it
									]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f, 0.f, 0.f)
									[
										// the view badges on one row: seven codes, no words (the ABOUT tab spells them out).
										// They share the row, so each one is a wide thumb target.
										SNew(SHorizontalBox)
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(0.f, 0.f, 3.f, 0.f)[Badge(TEXT("TEL"), &ASlapHUD::bShowTelemetry)]
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(0.f, 0.f, 3.f, 0.f)[Badge(TEXT("SCI"), &ASlapHUD::bShowScience)]
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(0.f, 0.f, 3.f, 0.f)[Badge(TEXT("XRAY"), &ASlapHUD::bShowXRay)]
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(0.f, 0.f, 3.f, 0.f)[Badge(TEXT("SLOW"), &ASlapHUD::bSlowMo)]
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(0.f, 0.f, 3.f, 0.f)[Badge(TEXT("LIVE"), &ASlapHUD::bLive)]
										+ SHorizontalBox::Slot().FillWidth(1.f).Padding(0.f, 0.f, 3.f, 0.f)[Badge(TEXT("COM"), &ASlapHUD::bShowCommentary)]
										+ SHorizontalBox::Slot().FillWidth(1.f)[Badge(TEXT("DBG"), &ASlapHUD::bShowDebug)]
									]
								]
								+ SWidgetSwitcher::Slot()	// ROSTER: the 4 x 4 grid, then the card of the selected slot
								[
									SNew(SVerticalBox)
									+ SVerticalBox::Slot().AutoHeight()[Grid]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 0.f)
									[Label(TAttribute<FText>::CreateLambda([Self] { return Self.IsValid() && Self->Slots.IsValidIndex(Self->RosterSel) ? FText::FromString(Self->Slots[Self->RosterSel].Value) : FText(); }), 14, White)]
								]
								+ SWidgetSwitcher::Slot()	// ABOUT: the legend as a two-column grid, one line per code
								[
									SNew(SVerticalBox)
									+ SVerticalBox::Slot().AutoHeight()
									[Label(FText::FromString(TEXT("Two AI athletes trade slaps across a podium. MuJoCo runs the physics; Unreal only draws it.\nOn the arena: swipe sideways for views (none, TEL, TEL+SCI, XRAY), double tap to replay.")), 13, Dim)]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f, 0.f, 0.f)
									[
										SNew(SUniformGridPanel).SlotPadding(FMargin(6.f, 1.f))
										+ SUniformGridPanel::Slot(0, 0)[Label(FText::FromString(TEXT("TEL")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 0)[Label(FText::FromString(TEXT("momentum, balance rings, palm speed, head g")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 1)[Label(FText::FromString(TEXT("SCI")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 1)[Label(FText::FromString(TEXT("KO danger, HIC / BrIC, kinetic chain")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 2)[Label(FText::FromString(TEXT("XRAY")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 2)[Label(FText::FromString(TEXT("each brain's target pose and what it reacts to")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 3)[Label(FText::FromString(TEXT("SLOW")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 3)[Label(FText::FromString(TEXT("live slow motion on the big moments")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 4)[Label(FText::FromString(TEXT("LIVE")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 4)[Label(FText::FromString(TEXT("second screen (Web viewer)")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 5)[Label(FText::FromString(TEXT("COM")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 5)[Label(FText::FromString(TEXT("the commentary line")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 6)[Label(FText::FromString(TEXT("DBG")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 6)[Label(FText::FromString(TEXT("sim clock, timestep, pace, camera, worst frame")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 7)[Label(FText::FromString(TEXT("REPLAY")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 7)[Label(FText::FromString(TEXT("the last 2.5 s at 1/2 speed (x1/4: quarter); tap again to return")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 8)[Label(FText::FromString(TEXT("RINGS")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 8)[Label(FText::FromString(TEXT("green under 5 cm off the soles, red over 10")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 9)[Label(FText::FromString(TEXT("GAUGE")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 9)[Label(FText::FromString(TEXT("right edge, palm speed; the line is a clean 8 m/s")), 13, Dim)]
										+ SUniformGridPanel::Slot(0, 10)[Label(FText::FromString(TEXT("KO DANGER")), 13, Hi)]
										+ SUniformGridPanel::Slot(1, 10)[Label(FText::FromString(TEXT("the cross is where to step; red 15 cm out")), 13, Dim)]
									]
									+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f, 0.f, 0.f)
									[Label(FText::FromString(FString(TEXT("Build ")) + TEXT(POSLAP_BUILD_VERSION)), 13, Dim)]
								]
							]
							+ SVerticalBox::Slot().AutoHeight()	// tabs at the foot of the sheet: under the thumb
							[
								SAssignNew(SheetTabs, SHorizontalBox)
								+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(0, TEXT("MATCH"))]
								+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(1, TEXT("ROSTER"))]
								+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(2, TEXT("ABOUT"))]
								+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)
								[SNew(SBox).WidthOverride(Touch).HeightOverride(Touch)[SAssignNew(Hit[HitClose], SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda(Close)[Label(FText::FromString(TEXT("X")), 18, White)]]]
							]
						]
					]
				]
			]
		];
	Targets.Add(GripBox);
	VC->AddViewportWidgetContent(Ui.ToSharedRef(), 10);
	if (SheetBody.IsValid()) SheetBody->SetClipping(EWidgetClipping::ClipToBounds);	// a tab too tall for the budget is cut, never spills over the tab row
}

void ASlapHUD::Travel(int32 P)
{
	if (!Pairings.IsValidIndex(P) || bTravelling) return;
	const FString Package = Pairings[P].PackageName.ToString();
	if (!FPackageName::DoesPackageExist(Package))	// the pairing level is not in this build (a cooked package that never shipped): stay in the menu
	{
		UE_LOG(LogTemp, Warning, TEXT("SlapHUD: pairing %s is not in this build: the menu stays"), *Package);
		return;
	}
	bTravelling = true;	// this HUD dies with the level; until then a second START or a picker tap is ignored
	TravelledAt = GetWorld()->GetTimeSeconds();
	UE_LOG(LogTemp, Log, TEXT("SlapHUD: opening pairing %s"), *Package);
	UGameplayStatics::OpenLevel(this, FName(*Package));	// the match here is abandoned unscored, as START does
}

void ASlapHUD::InstantReplay()
{
	// the same machinery as the KO replay: each athlete plays back its last poses (3 s kept) at half speed while MuJoCo
	// carries on unseen; the director cuts to the replay shot. Tapped again during a replay: back to live at once
	const double Now = GetWorld()->GetTimeSeconds();
	const bool bStop = Now < ReplayEnd;
	constexpr double Window = 2.5;
	const double Rate = bQuarter ? 0.25 : 0.5;
	for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
	{
		if (V.IsValid()) bStop ? V->Replay(0.0, 0.0, 0.0) : V->Replay(Now - Window, Now, Rate);
	}
	ReplayAt = -1.0;	// a pending KO replay would cut this one short
	ReplayEnd = bStop ? Now : Now + Window / Rate;
	UE_LOG(LogTemp, Log, TEXT("SlapHUD: instant replay %s at %.2f s"), bStop ? TEXT("stopped") : TEXT("started"), Now);
}

void ASlapHUD::CyclePreset(int32 Dir)
{
	Preset = (Preset + Dir + 4) % 4;	// a swipe on the arena: none, TEL, TEL+SCI, XRAY (the badges still set any mix)
	bShowTelemetry = Preset == 1 || Preset == 2;
	bShowScience = Preset == 2;
	bShowXRay = Preset == 3;
	UE_LOG(LogTemp, Log, TEXT("SlapHUD: view preset %d"), Preset);
}

void ASlapHUD::LoadRoster()
{
	// every character card in its slot ("slot": 1-16, else the first open one): the tape, its style, the brains made for it
	// (a new one shows "in training" until it graduates); the other slots are placeholders for athletes still to come
	// ponytail: read from training/ beside the project; a packaged (Android) build needs the cards staged with the content
	const FString Dir = FPaths::ProjectDir() / TEXT("training/characters");
	TArray<FString> Files;
	IFileManager::Get().FindFiles(Files, *(Dir / TEXT("*.json")), true, false);
	Files.Sort();
	Slots.Init({}, RosterSize);
	// the card of a slot is four lines, always: a long summary or brain list is cut, never wrapped (the sheet does not scroll)
	auto Cut = [](FString S, int32 N)
	{
		S.ReplaceInline(TEXT("\n"), TEXT(" "));
		if (S.Len() > N) S = S.Left(N - 1) + TEXT("...");
		return S;
	};
	for (const FString& F : Files)
	{
		const TSharedPtr<FJsonObject> C = F.EndsWith(TEXT("_skeleton.json")) ? nullptr : ReadJson(Dir / F);
		if (!C) continue;
		const FString Name = C->GetStringField(TEXT("name"));
		FString Mine;
		for (const FAssetData& B : Brains)
		{
			if (B.AssetName.ToString().StartsWith(Name + TEXT("_"))) Mine += (Mine.IsEmpty() ? TEXT("") : TEXT(", ")) + B.AssetName.ToString().RightChop(Name.Len() + 1);
		}
		const TSharedPtr<FJsonObject>* Style = nullptr;
		const FString Summary = Cut(C->TryGetObjectField(TEXT("style"), Style) ? (*Style)->GetStringField(TEXT("summary")) : FString(), 74);
		int32 Slot = INDEX_NONE;
		if (C->TryGetNumberField(TEXT("slot"), Slot)) Slot -= 1;
		if (!Slots.IsValidIndex(Slot) || !Slots[Slot].Key.IsEmpty()) Slot = Slots.IndexOfByPredicate([](const TPair<FString, FString>& X) { return X.Key.IsEmpty(); });
		if (Slot == INDEX_NONE) break;	// more cards than slots
		Slots[Slot].Key = C->GetStringField(TEXT("display"));
		const FString Made = Cut(Mine.IsEmpty() ? FString(TEXT("none yet (in training)")) : Mine, 54);
		Slots[Slot].Value = FString::Printf(TEXT("%s   %.2f m  %.0f kg\nSTR %.2f  SPD %.2f  FLX %.2f  TGH %.2f\n%s\nBrains: %s"),
			*Slots[Slot].Key, C->GetNumberField(TEXT("height_m")), C->GetNumberField(TEXT("mass_kg")), C->GetNumberField(TEXT("strength")),
			C->GetNumberField(TEXT("speed")), C->GetNumberField(TEXT("flexibility")), C->GetNumberField(TEXT("toughness")), *Summary,
			*Made);
	}
	for (int32 i = 0; i < RosterSize; ++i)
	{
		if (!Slots[i].Key.IsEmpty()) continue;
		Slots[i].Value = FString::Printf(TEXT("Slot %d is open.\nA new athlete: a rigged FBX -> Scripts/rig_mixamo.py -> a card\ntraining/characters/<name>.json with \"slot\": %d -> build_body.py\n-> its own ladder (overnight.py --char <name> --queue)"), i + 1, i + 1);
	}
}

void ASlapHUD::LayoutAudit()
{
	// -SlapLayoutTest: the sheet as it is actually laid out, measured in Slate units. One line per tab, then a summary.
	// The checks: the sheet stays inside the layer; the tab content fits the box (nothing is clipped inside the sheet);
	// the tab row is on screen at the foot; the sheet never covers a corner anchor; every target is at least 44 units.
	if (!bMenu)	// open the sheet and start on the first tab
	{
		bMenu = true;
		Tab = 0;
		LayoutStep = 0;
		LayoutWait = 8;	// let the sheet open and lay out before the first measurement
		return;
	}
	if (!SheetRoot.IsValid() || !SheetBody.IsValid() || !SheetContent.IsValid() || !SheetTabs.IsValid() || !Ui.IsValid()) return;
	if (--LayoutWait > 0) return;	// let the new tab lay out before it is measured

	// Two spaces: Slate units (dp, what the layout is made of) and the absolute rect (where things are on screen).
	auto Local = [](const TSharedPtr<SWidget>& W) { return W.IsValid() ? W->GetCachedGeometry().GetLocalSize() : FVector2D::ZeroVector; };
	auto Rect = [](const TSharedPtr<SWidget>& W, FVector2D& Pos, FVector2D& Size)
	{
		if (!W.IsValid()) { Pos = FVector2D::ZeroVector; Size = FVector2D::ZeroVector; return; }
		const FGeometry& G = W->GetCachedGeometry();
		Pos = G.GetAbsolutePosition();
		Size = G.GetAbsoluteSize();
	};
	FVector2D LPos, LSize, SPos, SSize;
	Rect(Ui, LPos, LSize);
	Rect(SheetRoot, SPos, SSize);
	const FVector2D BoxLocal = Local(SheetBody);
	const FVector2D ContentLocal = Local(SheetContent);
	const FVector2D TabsLocal = Local(SheetTabs);
	const float Wanted = SheetContent.IsValid() ? SheetContent->GetDesiredSize().Y : 0.f;

	int32 Fails = 0;
	const float Eps = 0.5f;
	if (SPos.Y + SSize.Y > LPos.Y + LSize.Y + Eps) ++Fails;	// the sheet runs off the bottom of the screen
	if (SPos.Y < LPos.Y - Eps) ++Fails;						// ... or off the top
	if (Wanted > ContentLocal.Y + Eps) ++Fails;				// the tab wants more room than it has: clipped inside
	if (BoxLocal.Y > SheetBudget + Eps) ++Fails;				// the box is taller than the budget
	if (TabsLocal.Y < 1.f) ++Fails;							// the tab row is not laid out
	int32 Covered = 0;
	for (int32 i = 0; i < 5; ++i)	// the sheet must never cover a corner anchor
	{
		FVector2D APos, ASize;
		Rect(Anchor[i], APos, ASize);
		if (ASize.X <= 0.f || ASize.Y <= 0.f) continue;	// not laid out (a hidden anchor)
		const float OverlapX = FMath::Min(APos.X + ASize.X, SPos.X + SSize.X) - FMath::Max(APos.X, SPos.X);
		const float OverlapY = FMath::Min(APos.Y + ASize.Y, SPos.Y + SSize.Y) - FMath::Max(APos.Y, SPos.Y);
		if (OverlapX > 2.f && OverlapY > 2.f) ++Covered;	// a shared edge is a touch, not a cover
	}
	Fails += Covered;
	int32 Unsafe = 0;	// every corner anchor and the grip inside the platform's safe insets (a notch, a nav bar; -SlapSafeZone fakes them)
	for (const TSharedPtr<SWidget>& W : { Anchor[0], Anchor[1], Anchor[2], Anchor[3], Anchor[4], Hit[HitGrip] })
	{
		FVector2D APos, ASize;
		Rect(W, APos, ASize);
		if (ASize.X <= 0.f || ASize.Y <= 0.f) continue;
		if (APos.Y < LPos.Y + Safe.Top - Eps || APos.Y + ASize.Y > LPos.Y + LSize.Y - Safe.Bottom + Eps || APos.X < LPos.X + Safe.Left - Eps || APos.X + ASize.X > LPos.X + LSize.X - Safe.Right + Eps)
		{
			if (!Unsafe++) UE_LOG(LogTemp, Log, TEXT("SlapHUD layout: outside the safe zone: %s + %s in layer %s + %s"), *APos.ToString(), *ASize.ToString(), *LPos.ToString(), *LSize.ToString());
		}
	}
	Fails += Unsafe;
	float MinTouch = 1e9f;
	for (const TSharedPtr<SWidget>& W : Targets)	// the 44-unit targets
	{
		const FVector2D Sz = Local(W);
		if (Sz.X <= 0.f || Sz.Y <= 0.f) continue;
		MinTouch = FMath::Min(MinTouch, FMath::Min(Sz.X, Sz.Y));
	}
	for (int32 i = 0; i < HitCount; ++i)	// and the buttons themselves
	{
		const FVector2D Sz = Local(Hit[i]);
		if (Sz.X <= 0.f || Sz.Y <= 0.f) continue;
		MinTouch = FMath::Min(MinTouch, FMath::Min(Sz.X, Sz.Y));
	}
	if (MinTouch < 43.5f) ++Fails;
	LayoutFails += Fails;
	UE_LOG(LogTemp, Log, TEXT("SlapHUD layout tab %d: sheet %.0fx%.0f dp at (%.0f,%.0f), layer %.0fx%.0f dp, budget %.0f, box %.0f, content %.0f of %.0f wanted, tabs %.0f, anchors covered %d, outside the safe zone %d (insets T%.0f B%.0f px, fake %.3f/%.3f, custom %d %.3f/%.3f), smallest target %.0f dp, dpi %.3f, fails %d"),
		Tab, Local(SheetRoot).X, Local(SheetRoot).Y, SPos.X, SPos.Y, Local(Ui).X, Local(Ui).Y, SheetBudget, BoxLocal.Y, ContentLocal.Y, Wanted, TabsLocal.Y, Covered, Unsafe, Safe.Top, Safe.Bottom, FakeSafe.Top, FakeSafe.Bottom, FSlateApplication::Get().IsCustomSafeZoneSet(), FSlateApplication::Get().GetCustomSafeZone().Top, FSlateApplication::Get().GetCustomSafeZone().Bottom, MinTouch, DpiScale, Fails);
	LayoutWait = 3;	// a few frames before the next tab
	if (++LayoutStep >= 3)
	{
		UE_LOG(LogTemp, Log, TEXT("SlapHUD layout: 3 tabs measured in a %.0f x %.0f layer, %d fails"), LSize.X, LSize.Y, LayoutFails);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	}
	else Tab = LayoutStep;
}

void ASlapHUD::UiTest(const FSlapHud& H)
{
	// -SlapUiTest: synthetic Slate clicks at the real widgets (hit-testing included, the OS cursor untouched), one every
	// 1.5 s from 3 s: grip, TEL badge, scrim, menu, ROSTER, MATCH, replay (closes the sheet, replay runs), menu, red's next
	// brain, START (a new match with that brain), menu, X, a double tap on the arena (replay runs); each followed by its
	// expected state. Every click target must be bound (a null one fails at once). Then waits for a match to end, taps
	// REMATCH on the result card and waits for the next to start (Gameplay -> Win/Loss -> Loop Reset). Logs PASS or FAIL.
	struct FStep { int32 Target; bool bMenuAfter; int32 TabAfter; bool bTelemetryAfter; };
	static const FStep Steps[] = { { HitGrip, true, 0, false }, { HitTelemetry, true, 0, true }, { HitScrim, false, 0, true },
		{ HitMenu, true, 0, true }, { HitTabRoster, true, 1, true }, { HitTabMatch, true, 0, true }, { HitReplay, false, 0, true },
		{ HitMenu, true, 0, true }, { HitRedNext, true, 0, true }, { HitStart, false, 0, true }, { HitMenu, true, 0, true }, { HitClose, false, 0, true }, { HitPad, false, 0, true } };
	const double Now = GetWorld()->GetTimeSeconds();
	if (UiStep == 0 && !bUiChecked)	// null bindings: every target the test (and a player) needs exists
	{
		bUiChecked = true;
		for (int32 k = 0; k < HitCount; ++k)
		{
			if (Hit[k].IsValid()) continue;
			++UiFails;
			UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test: click target %d is not bound - WRONG"), k);
		}
	}
	if (UiMatch0 == 0 && H.Match > 0) UiMatch0 = H.Wins[0] + H.Wins[1] + H.Draws + 1;	// series tally + 1: START abandons, only a real end counts
	if (UiStep > 0 && UiStep <= UE_ARRAY_COUNT(Steps))	// check the last click, a frame or more later
	{
		const FStep& Was = Steps[UiStep - 1];
		if (Now >= UiAt + 0.5 && !bUiChecked)
		{
			bUiChecked = true;
			bool bOk = bMenu == Was.bMenuAfter && Tab == Was.TabAfter && bShowTelemetry == Was.bTelemetryAfter;
			FString Extra;
			if (Was.Target == HitStart)	// a fresh match, red playing the brain it picked
			{
				bOk &= H.MatchSeq > UiSeq && H.Brain[0] == UiBrain;
				Extra = FString::Printf(TEXT(", match seq %d -> %d, red brain %s (picked %s)"), UiSeq, H.MatchSeq, *H.Brain[0], *UiBrain);
			}
			if (Was.Target == HitReplay || Was.Target == HitPad)	// the replay is running over the live match
			{
				bOk &= Now < ReplayEnd;
				Extra = FString::Printf(TEXT(", replay until %.2f s"), ReplayEnd);
			}
			UiFails += !bOk;
			UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test click %d (target %d): menu %d tab %d telemetry %d%s - %s"), UiStep, Was.Target, bMenu, Tab, bShowTelemetry, *Extra, bOk ? TEXT("ok") : TEXT("WRONG"));
		}
	}
	auto Click = [&](int32 Target)
	{
		const TSharedPtr<SWidget> W = Hit[Target];
		FSlateApplication& App = FSlateApplication::Get();
		const FVector2D P = W.IsValid() ? FVector2D(W->GetTickSpaceGeometry().GetAbsolutePositionAtCoordinates(FVector2f(0.5f, Target == HitScrim ? 0.2f : Target == HitPad ? 0.4f : 0.5f))) : FVector2D::ZeroVector;
		// hit-tested in the game window itself (the Process*Event path asks the OS which window is under the real cursor)
		const int32 User = App.GetUserIndexForMouse();
		const TSharedPtr<SWindow> Win = GetWorld()->GetGameViewport() ? GetWorld()->GetGameViewport()->GetWindow() : nullptr;
		const FWidgetPath Path = Win ? FWidgetPath(Win->GetHittestGrid().GetBubblePath(P, App.GetCursorRadius(), false, User)) : FWidgetPath();
		auto Ev = [&](const TSet<FKey>& Down, const FKey& Button) { return FPointerEvent(User, FSlateApplication::CursorPointerIndex, P, P, Down, Button, 0.f, FModifierKeysState()); };
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test click at %s, window %s + %s, hit %s"), *P.ToString(), Win ? *Win->GetPositionInScreen().ToString() : TEXT("-"),
			Win ? *Win->GetSizeInScreen().ToString() : TEXT("-"), Path.IsValid() ? *Path.GetLastWidget()->GetTypeAsString() : TEXT("nothing"));
		App.RoutePointerMoveEvent(Path, Ev({}, EKeys::Invalid), false);	// hover: SButton clicks only when hovered
		App.RoutePointerDownEvent(Path, Ev({ EKeys::LeftMouseButton }, EKeys::LeftMouseButton));
		App.RoutePointerUpEvent(Path, Ev({}, EKeys::LeftMouseButton));
		if (Target == HitPad)	// the second tap of a double tap
		{
			App.RoutePointerDoubleClickEvent(Path, Ev({ EKeys::LeftMouseButton }, EKeys::LeftMouseButton));
			App.RoutePointerUpEvent(Path, Ev({}, EKeys::LeftMouseButton));
		}
	};
	if (UiStep < UE_ARRAY_COUNT(Steps) && Now >= 3.0 + 1.5 * UiStep)
	{
		if (Steps[UiStep].Target == HitStart)
		{
			UiSeq = H.MatchSeq;
			UiBrain = Pick[0] != INDEX_NONE ? Brains[Pick[0]].AssetName.ToString() : H.Brain[0];
		}
		Click(Steps[UiStep].Target);
		++UiStep;
		UiAt = Now;
		bUiChecked = false;
	}
	const bool bEnded = UiMatch0 > 0 && H.Wins[0] + H.Wins[1] + H.Draws >= UiMatch0;
	if (UiStep == UE_ARRAY_COUNT(Steps) && bUiChecked && bEnded && !H.Result.IsEmpty() && !bRematched && Now >= UiAt + 0.5)	// the result card is up: REMATCH
	{
		Click(HitRematch);
		UiAt = Now;
	}
	if (UiStep == UE_ARRAY_COUNT(Steps) && bUiChecked && !bUiDone && bEnded && bRematched && H.Match > 0 && H.Result.IsEmpty())	// a match ended, REMATCH, the next began
	{
		bUiDone = true;
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test %s: %d of %d clicks wrong; a match ended (series %d-%d, %d drawn), REMATCH, and match %d began"), UiFails ? TEXT("FAIL") : TEXT("PASS"),
			UiFails, (int32)UE_ARRAY_COUNT(Steps), H.Wins[0], H.Wins[1], H.Draws, H.Match);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);	// short: training shares this machine
	}
	else if (!bUiDone && Now > 120.0)
	{
		bUiDone = true;
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test FAIL: %d of %d clicks wrong, within 120 s: match ended %d, REMATCH tapped %d"), UiFails, (int32)UE_ARRAY_COUNT(Steps), bEnded, bRematched);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	}
}

void ASlapHUD::Text(const FString& Str, float X, float Y, FColor C, float Scale, bool bCentre, UFont* Font)
{
	Font = Font ? Font : GEngine->GetSmallFont();
	float W = 0, Ht = 0;
	if (bCentre) Canvas->StrLen(Font, Str, W, Ht);	// only centring needs the width
	Canvas->SetDrawColor(C);
	Canvas->DrawText(Font, Str, bCentre ? X - W * Scale * 0.5f : X, Y, Scale, Scale);
}

void ASlapHUD::CacheAthletes()
{
	if (Body[0].IsValid() && Body[1].IsValid()) return;	// until both are found (they may spawn after the first frame)
	Skin.SetNum(2);	// one material instance per athlete: M_Grandpa's ripple (RipplePoint / RippleTime / RippleAmp), wear (FlushPoint / Flush / Tired)
	for (TActorIterator<ASlapAthleteVisual> It(GetWorld()); It; ++It)
	{
		const int32 a = It->BodyPrefix == TEXT("blue_") ? 1 : 0;
		if (Body[a].IsValid()) continue;
		Skin[a] = It->Mesh->CreateDynamicMaterialInstance(0);
		Body[a] = It->Mesh;
		Visual[a] = *It;
	}
}

void ASlapHUD::Cues(const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	HeadG.Add(H.HeadG);
	if (HeadG.Num() > 120) HeadG.RemoveAt(0);

	if (H.StrikeSeq != SeenStrike)	// the touch: spray along the palm's path, sound, ripple on the struck cheek
	{
		SeenStrike = H.StrikeSeq;
		StrikeAt = Now;
		StrikePos = H.StrikePos;
		StrikeSpeed = H.Speed;
		Struck = H.StrikeDefender;
		StrikeK = FMath::Clamp(H.Speed / CleanSpeed, 0.25, 1.5);
		const float K = StrikeK;
		if (Spray) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, H.StrikePos, FRotationMatrix::MakeFromZ(H.StrikeDir).Rotator(), FVector(0.3f * K));
		if (SlapTakes.Num())	// one of four takes, deeper behind a heavier arm, duller for a slow palm
		{
			const float Pitch = FMath::Clamp(FMath::Sqrt(3.0 / FMath::Max(H.StrikeMass[1 - Struck], 0.5)), 0.8, 1.25) * FMath::FRandRange(0.94f, 1.06f);
			if (UAudioComponent* A = UGameplayStatics::SpawnSoundAtLocation(this, SlapTakes[FMath::RandHelper(SlapTakes.Num())], H.StrikePos, FRotator::ZeroRotator, K, Pitch, 0.f, Ringside))
			{
				A->SetLowPassFilterEnabled(true);
				A->SetLowPassFilterFrequency(2500.f + 17500.f * FMath::Min(K, 1.f));
			}
		}
		if (Body[Struck].IsValid())
		{
			const FTransform HeadT = Body[Struck]->GetBoneTransformByName(BoneHead, EBoneSpaces::WorldSpace);
			FlushLocal[Struck] = HeadT.InverseTransformPosition(H.StrikePos);
			const FVector N = (H.StrikePos - HeadT.GetLocation()).GetSafeNormal();	// the print: fingers along the palm's travel, flat on the cheek
			const FVector U = (H.StrikeDir - N * (H.StrikeDir | N)).GetSafeNormal();
			PrintU[Struck] = HeadT.InverseTransformVectorNoScale(U);
			PrintV[Struck] = HeadT.InverseTransformVectorNoScale(N ^ U);
			Print[Struck] = FMath::Min(1.f, Print[Struck] + 0.4f * K);
			SweatHead0 = HeadT.GetLocation();
			SweatAt = Now + 0.08;
		}
		RoarAt = Now + 0.2;	// the crowd catches its breath (Crowd ducks the murmur), then roars
		RoarVolume = 0.3f + 0.5f * K;
		if (Skin.IsValidIndex(Struck) && Skin[Struck])
		{
			Skin[Struck]->SetVectorParameterValue(TEXT("RipplePoint"), FLinearColor(H.StrikePos));
			Skin[Struck]->SetScalarParameterValue(TEXT("RippleAmp"), 0.2f * H.Speed);	// cm: 1.6 at a clean 8 m/s
		}
	}
	if (SweatAt >= 0.0 && Now >= SweatAt && Body[Struck].IsValid())	// sweat off the far side, as hard as the head snapped
	{
		SweatAt = -1.0;
		const FVector Head = Body[Struck]->GetBoneLocation(BoneHead);
		const double Snap = FVector::Dist(Head, SweatHead0) / 100.0 / FMath::Max(Now - StrikeAt, 0.01);	// m/s (clean slaps: 2-7)
		const FVector Off = (Head - StrikePos).GetSafeNormal();
		// ponytail: the spray system again, smaller; a droplet emitter of its own if it reads as spray
		if (Spray) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, Head + Off * 8.f, FRotationMatrix::MakeFromZ(Off + H.StrikeDir).Rotator(), FVector(0.15f * FMath::Clamp(Snap / 5.0, 0.2, 1.5)));
	}
	if (RoarAt >= 0.0 && Now >= RoarAt)
	{
		RoarAt = -1.0;
		if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, RoarVolume);
	}
	const float Dt = GetWorld()->GetDeltaSeconds();
	for (int32 a = 0; a < Skin.Num(); ++a)	// wear: the flush and the print follow the head; 1 at the neck-strength and stamina floors
	{
		if (!Skin[a]) continue;
		Skin[a]->SetScalarParameterValue(PRippleTime, a == Struck ? Now - StrikeAt : 100.f);
		if (Body[a].IsValid())
		{
			const FTransform HeadT = Body[a]->GetBoneTransformByName(BoneHead, EBoneSpaces::WorldSpace);
			Skin[a]->SetVectorParameterValue(PFlushPoint, FLinearColor(HeadT.TransformPosition(FlushLocal[a])));
			Skin[a]->SetVectorParameterValue(PPrintU, FLinearColor(HeadT.TransformVectorNoScale(PrintU[a])));
			Skin[a]->SetVectorParameterValue(PPrintV, FLinearColor(HeadT.TransformVectorNoScale(PrintV[a])));
		}
		Print[a] *= FMath::Exp(-Dt / 25.f);
		Skin[a]->SetScalarParameterValue(PPrint, Print[a]);
		const double Sting = a == Struck ? 0.5 * FMath::Clamp(StrikeSpeed / CleanSpeed, 0.0, 1.5) * FMath::Exp((StrikeAt - Now) / 2.0) : 0.0;
		Skin[a]->SetScalarParameterValue(PFlush, FMath::Clamp((1.0 - H.Neck[a]) / (1.0 - MinNeck) + Sting, 0.0, 1.0));
		Skin[a]->SetScalarParameterValue(PTired, FMath::Clamp((1.0 - H.Stamina[a]) / (1.0 - MinStamina), 0.0, 1.0));
	}
	if (H.KOSeq != SeenKO)
	{
		SeenKO = H.KOSeq;
		KOAt = Now;
		if (Bell) UGameplayStatics::PlaySound2D(this, Bell);
		if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, 1.4f, 0.9f);
		if (Spray) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, StrikePos, FRotator(90, 0, 0), FVector(0.6f));
		if (Thud && Body[Struck].IsValid()) UGameplayStatics::PlaySoundAtLocation(this, Thud, Body[Struck]->GetBoneLocation(BonePelvis), 1.2f, 0.9f, 0.f, Ringside);
		ReplayAt = Now + 0.8;
	}
	if (ReplayAt >= 0.0 && Now >= ReplayAt)	// the KO again at half speed: its last 1.5 s, inside the post-match hold
	{
		ReplayAt = -1.0;
		const double Dur = FMath::Max(1.0, KOShot - 1.0), Window = FMath::Min(1.5, 0.5 * Dur);
		for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
		{
			if (V.IsValid()) V->Replay(KOAt - Window, KOAt, Window / Dur);
		}
		ReplayEnd = Now + Dur;
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
		LoadLeague();	// training/scouting.py may have run since
		Print[0] = Print[1] = 0.f;	// a fresh match: clean cheeks, a clean floor
		for (const TWeakObjectPtr<UDecalComponent>& M : Marks)
		{
			if (M.IsValid()) M->DestroyComponent();
		}
		Marks.Reset();
	}
	if (H.ScoredRound != SeenRound)
	{
		SeenRound = H.ScoredRound;
		if (H.ScoredRound > 0) CardAt = Now;
	}
	if (Now - CommentaryPoll > 0.5)	// Scripts/commentator.py writes one line per call
	{
		CommentaryPoll = Now;
		const FString Path = CommentaryFile();
		const FDateTime Stamp = IFileManager::Get().GetTimeStamp(*Path);
		FString Raw;
		if (Stamp != CommentaryStamp && FFileHelper::LoadFileToString(Raw, *Path))
		{
			CommentaryStamp = Stamp;
			CommentaryAt = Now;
			Commentary.Reset();	// wrapped once here, drawn every frame
			FString Cur;
			TArray<FString> Words;
			Raw.TrimStartAndEnd().ParseIntoArrayWS(Words);
			for (const FString& Wd : Words)
			{
				if (Cur.Len() + Wd.Len() > 44) { Commentary.Add(Cur); Cur.Reset(); }
				Cur += (Cur.IsEmpty() ? TEXT("") : TEXT(" ")) + Wd;
			}
			if (!Cur.IsEmpty()) Commentary.Add(Cur);
		}
		// chat hype: fresh for 15 s after Scripts/chat.py last wrote it
		const FDateTime HypeStamp = IFileManager::Get().GetTimeStamp(*HypeFile());
		Hype = 0.f;
		const TSharedPtr<FJsonObject> J = (FDateTime::UtcNow() - HypeStamp).GetTotalSeconds() < 15.0 ? ReadJson(HypeFile()) : nullptr;
		if (J)
		{
			Hype = FMath::Clamp((float)J->GetNumberField(TEXT("hype")), 0.f, 1.f);
			ChatLine = J->GetStringField(TEXT("line"));
			ChatAt = Now;
		}
	}
	Stage(Now);
}

void ASlapHUD::Stage(double Now)
{
	// a strike flares the key and the striker's rim, scaled by the palm's speed; a KO runs a chase round the truss
	auto Set = [](FStageLight& Lit, double Gain) { if (ULightComponent* L = Lit.Light.Get()) L->SetIntensity(Lit.Base * Gain); };
	const double Ts = Now - StrikeAt, Tk = Now - KOAt;
	const double K = StrikeK;
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
	for (int32 a = 0; a < 2; ++a)
	{
		if (!Body[a].IsValid()) continue;
		Head[a] = Body[a]->GetBoneLocation(BoneHead);
		Pelvis[a] = Body[a]->GetBoneLocation(BonePelvis);
		for (const FVector& P : { Head[a], Pelvis[a], Body[a]->GetBoneLocation(BoneFootL), Body[a]->GetBoneLocation(BoneFootR) })
		{
			if (!P.IsZero()) Box += P;
		}
	}
	if (!Box.IsValid) return;

	EShot Want = H.bWalkIn ? EShot::WalkIn : EShot::Wide;
	if (Now < ReplayEnd) Want = EShot::Replay;
	else if (Now - KOAt < KOShot) Want = EShot::KO;
	else if (Now - StrikeAt < 1.6) Want = EShot::Strike;
	else if (!H.bWalkIn && H.Match > 0 && !H.bStruck && H.HandDist < 0.55 && H.HandSpeed > 1.5) Want = EShot::Tension;
	bool bCut = false;
	if (Want != Shot && (Want > Shot || Want == EShot::WalkIn || Now - ShotStart > 1.0))
	{
		bCut = Want == EShot::WalkIn || Want == EShot::Tension || Want == EShot::KO || Want == EShot::Replay;
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
	case EShot::WalkIn:		// ring entrance dolly: angled low-hero sweep following both athletes walking to the podium
	{
		const float Wp = FMath::Clamp((float)H.WalkInProgress, 0.f, 1.f);
		const float YawSweep = 28.f * (1.f - Wp);
		Look = 0.5f * (Pelvis[0] + Pelvis[1]) + Up * 25.f;
		const float Apart = FVector::Dist2D(Pelvis[0], Pelvis[1]);	// brain walkers start 2.4 m down each aisle
		const float Back = FMath::Max(FMath::Lerp(340.f, 260.f, Wp), (0.5f * Apart + 70.f) / FMath::Tan(FMath::DegreesToRadians(CC->FieldOfView) * 0.5f));
		Pos = Look - Fwd.RotateAngleAxis(YawSweep, Up) * Back + Up * FMath::Lerp(-15.f, 20.f, Wp);
		Speed = 4.5f;
		break;
	}
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
	case EShot::Replay:		// close on the replayed defender's head, orbiting
	{
		const float T = Now - ShotStart;
		Look = Head[Struck];
		Pos = Look - Fwd.RotateAngleAxis((Struck == 1 ? -35.f : 35.f) + 20.f * T, Up) * 190.f + Up * 25.f;
		Speed = 6.f;
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
	const float K = StrikeK;
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
	const float X = 16.f * S;
	// The lines are collected first, so the panel behind them is sized to what is actually on it: no empty black box over
	// the arena. Compaction only: the same round, clock, speed, judges and series.
	struct FLine { UFont* Font; FString Str; FColor Color; float Scale; float H; float W; };
	TArray<FLine> Lines;
	auto Add = [&](UFont* F, const FString& Str, FColor C, float Scale = 1.f)
	{
		float W = 0.f, Ht = 0.f;
		Canvas->StrLen(F, Str, W, Ht);
		FLine L; L.Font = F; L.Str = Str; L.Color = C; L.Scale = Scale;
		L.H = Ht * S * Scale + 4.f * S;
		L.W = W * S * Scale;
		Lines.Add(L);
	};
	const FColor Tc = TeamColor[Team(H.Striker)];
	// three short lines: who strikes and the clock; the touch (speed, part, head snap, what it was); judges and series
	if (H.bWalkIn)
	{
		Add(Big, FString::Printf(TEXT("WALK-IN  %d%%"), FMath::RoundToInt(100.0 * H.WalkInProgress)), FColor::White, 1.2f);
		Add(Small, H.WalkNote.IsEmpty() ? TEXT("Approaching the podium") : TEXT("Walking in on their own legs"), Grey, 1.1f);
	}
	else
	{
		Add(Big, FString::Printf(TEXT("R%d/%d   %s   %s %.1fs"), H.Round, Rounds, *H.Striker.ToUpper(), H.bStruck ? TEXT("REC") : TEXT("CLOCK"), FMath::Max(0.0, H.Clock)), Tc, 1.2f);
		const FString Touch = H.bStruck || H.Speed > 0 ? FString::Printf(TEXT("%.1f m/s (%s)   snap %.1f   "), H.Speed, *H.Part, H.HeadPeak) : FString();
		if (!(Touch + H.Event).IsEmpty()) Add(Small, Touch + H.Event, Gold, 1.15f);
	}
	Add(Small, FString::Printf(TEXT("J %d:%d  %d:%d  %d:%d     SERIES %d-%d%s"), H.Cards[0][0], H.Cards[0][1], H.Cards[1][0], H.Cards[1][1], H.Cards[2][0], H.Cards[2][1],
		H.Wins[0], H.Wins[1], H.Draws ? *FString::Printf(TEXT(" (%d D)"), H.Draws) : TEXT("")), Grey, 1.1f);
	float Total = 0.f, W = 0.f;
	for (const FLine& L : Lines) { Total += L.H; W = FMath::Max(W, L.W); }
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 8.f * S, Top, FMath::Max(120.f * S, W + 16.f * S), Total + 6.f * S);	// legible over the truss
	float Y = Top + 3.f * S;
	for (const FLine& L : Lines)
	{
		Text(L.Str, X, Y, L.Color, S * L.Scale, false, L.Font);
		Y += L.H;
	}
	PanelBottom = Top + Total + 6.f * S;
}

void ASlapHUD::DrawTelemetry(const FSlapHud& H)
{
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
	const float MX = 8.f * S, MY = PanelBottom + 6.f * S, MW = 524.f * S, MH = 16.f * S;	// right under the panel: no dead band over the crowd
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
	const float GX = 504.f * S, GY = Top + 372.f * S, GH = 230.f * S, GW = 16.f * S, Max = 12.f;
	const float V = H.bStruck ? H.Speed : H.HandSpeed;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), GX - 4.f * S, GY - 6.f * S, GW + 8.f * S, GH + 32.f * S);
	DrawRect(FLinearColor(TeamColor[Sk]), GX, GY + GH * (1.f - FMath::Clamp(V / Max, 0.f, 1.f)), GW, GH * FMath::Clamp(V / Max, 0.f, 1.f));
	const float Y8 = GY + GH * (1.f - CleanSpeed / Max);
	DrawLine(GX - 4.f * S, Y8, GX + GW + 4.f * S, Y8, FLinearColor::White, 2.f * S);
	Text(FString::Printf(TEXT("%.1f"), V), GX + GW * 0.5f, GY + GH + 6.f * S, H.bStruck && H.Speed >= CleanSpeed ? Gold : FColor::White, S, true);

	// the defender's head acceleration, last 2 s
	const float BX = 16.f * S, BY = Bot - 34.f * S, BW = 230.f * S, BH = 26.f * S;	// a strip: the feet (where knockdowns show) stay in view
	const float Peak = FMath::Max(HeadG);	// 0 when empty
	const float Scale = FMath::Max(20.f, Peak);
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), BX - 6.f * S, BY - 18.f * S, BW + 12.f * S, BH + 22.f * S);
	Text(FString::Printf(TEXT("HEAD G  %s  peak %.0f g"), *H.Defender.ToUpper(), Peak), BX, BY - 17.f * S, Grey, 0.85f * S);
	for (int32 i = 1; i < HeadG.Num(); ++i)
	{
		const float X0 = BX + BW * (i - 1) / 119.f, X1 = BX + BW * i / 119.f;
		DrawLine(X0, BY + BH * (1.f - HeadG[i - 1] / Scale), X1, BY + BH * (1.f - HeadG[i] / Scale), FLinearColor(Gold), 2.f * S);
	}

	// fatigue: stamina (joint speed) and neck strength, both from 1 fresh down to their floors
	const float FX = 290.f * S, FY = Bot - 34.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), FX - 6.f * S, FY - 18.f * S, 240.f * S, 48.f * S);
	Text(TEXT("STAMINA"), FX + 50.f * S, FY - 17.f * S, Grey, 0.85f * S);
	Text(TEXT("NECK"), FX + 140.f * S, FY - 17.f * S, Grey, 0.85f * S);
	for (int32 a = 0; a < 2; ++a)
	{
		const float Y = FY + a * 14.f * S;
		Text(a ? TEXT("BLUE") : TEXT("RED"), FX, Y - 2.f * S, TeamColor[a], 0.85f * S);
		const double Bars[2] = { (H.Stamina[a] - MinStamina) / (1.0 - MinStamina), (H.Neck[a] - MinNeck) / (1.0 - MinNeck) };
		for (int32 b = 0; b < 2; ++b)
		{
			const float X = FX + (50.f + 90.f * b) * S;
			DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.15f), X, Y, 80.f * S, 10.f * S);
			DrawRect(FLinearColor(TeamColor[a]), X, Y, 80.f * S * FMath::Clamp((float)Bars[b], 0.f, 1.f), 10.f * S);
		}
	}
}

void ASlapHUD::DrawOverlays(const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	const float Cx = Canvas->ClipX * 0.5f;

	// impact: a white flash and a shockwave ring from the contact point
	const double Ts = Now - StrikeAt;
	if (Ts < 0.12) DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.3f * (1.f - Ts / 0.12f)), 0, 0, Canvas->ClipX, Canvas->ClipY);
	if (Ts < 0.4)
	{
		const FVector C = Canvas->Project(StrikePos);
		const float R = (40.f + 140.f * Ts / 0.4f) * S * FMath::Clamp(StrikeSpeed / CleanSpeed, 0.5, 1.5);
		const FLinearColor Col(1.f, 0.95f, 0.8f, 1.f - Ts / 0.4f);
		if (C.Z > 0) Ring(this, C, R, 40, Col, 3.f * S);
	}
	const double Tk = Now - KOAt;
	if (Tk < 0.3) DrawRect(FLinearColor(0.9f, 0.1f, 0.05f, 0.35f * (1.f - Tk / 0.3f)), 0, 0, Canvas->ClipX, Canvas->ClipY);

	// tale of the tape, at every match start: under the panel and the momentum bar, clear of the athletes' heads
	if (Now - TapeAt < 4.0 && H.Result.IsEmpty())	// a match over in under 4 s: the result wins
	{
		const float Y = PanelBottom + (bShowTelemetry ? 28.f : 6.f) * S;	// under the panel (and the momentum bar)
		const bool bScout = Scout.Contains(H.Brain[0]) || Scout.Contains(H.Brain[1]);
		const float Row = bScout ? 48.f : 34.f;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.7f), 8.f * S, Y, 524.f * S, (24.f + 2.f * Row) * S);
		Text(TEXT("TALE OF THE TAPE"), Cx, Y + 4.f * S, Grey, 0.9f * S, true);
		for (int32 a = 0; a < 2; ++a)
		{
			const float Ya = Y + (22.f + Row * a) * S;
			Text(H.Name[a], 16.f * S, Ya, TeamColor[a], 1.2f * S);
			Text(H.Tape[a] + TEXT("  ") + H.Brain[a], 16.f * S, Ya + 17.f * S, Grey, 0.85f * S);
			if (const FString* L = Scout.Find(H.Brain[a])) Text(*L, 16.f * S, Ya + 31.f * S, Gold, 0.85f * S);	// training/scouting.py
		}
	}

	// judges' cards for the round just scored: slide in from the right, hold, slide out
	const double Tc = Now - CardAt;
	if (Tc < 5.0)
	{
		const float In = FMath::InterpEaseOut(0.f, 1.f, FMath::Clamp((float)Tc / 0.4f, 0.f, 1.f), 3.f);
		const float Out = FMath::InterpEaseIn(0.f, 1.f, FMath::Clamp(((float)Tc - 4.5f) / 0.5f, 0.f, 1.f), 3.f);
		const float W = 200.f * S, X = Canvas->ClipX - W * (In - Out) - 8.f * S, Y = PanelBottom + (bShowTelemetry ? 28.f : 6.f) * S;	// right, under the panel and the momentum bar
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

	// the result: the Slate result card (BuildUi), with REMATCH

	// live slow motion: say so, so nobody thinks the stream stalled
	if (H.SlowMo < 1.f) Text(TEXT("SLOW MOTION"), Cx, Top + 4.f * S, Gold, 1.1f * S, true);

	// chat hype (Scripts/chat.py): top right, under the menu button
	if (Now - ChatAt < 15.0)
	{
		const float W = 192.f * S, X = Canvas->ClipX - W - 8.f * S, Y = Top + 4.f * S;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), X, Y, W, 42.f * S);
		Text(TEXT("CHAT HYPE"), X + 8.f * S, Y + 3.f * S, Grey, 0.85f * S);
		DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.12f), X + 80.f * S, Y + 6.f * S, W - 88.f * S, 8.f * S);
		DrawRect(FLinearColor(0.6f, 0.35f, 1.f), X + 80.f * S, Y + 6.f * S, (W - 88.f * S) * Hype, 8.f * S);
		Text(ChatLine.Left(34), X + 8.f * S, Y + 21.f * S, FColor::White, 0.85f * S);
	}

	// commentary, bottom: the latest line for 8 s, wrapped at ~44 characters
	if (bShowCommentary && Now - CommentaryAt < 8.0 && CommentaryAt >= TurnAt && Commentary.Num())
	{
		const TArray<FString>& Lines = Commentary;
		const float Y = Bot - 108.f * S - Lines.Num() * 20.f * S;	// above the X-ray lines and the telemetry strips
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), 16.f * S, Y - 6.f * S, 508.f * S, (Lines.Num() * 20.f + 10.f) * S);
		for (int32 i = 0; i < Lines.Num(); ++i) Text(Lines[i], Cx, Y + i * 20.f * S, FColor::White, 1.1f * S, true);
	}
}

void ASlapHUD::DrawScience(const FSlapHud& H)
{
	if (H.bWalkIn) return;
	const double Now = GetWorld()->GetTimeSeconds();
	const int32 Sk = Team(H.Striker), Def = 1 - Sk;
	auto Heat = [](double K) { return FLinearColor::LerpUsingHSV(FLinearColor(0.35f, 0.9f, 0.45f), FLinearColor(1.f, 0.25f, 0.2f), FMath::Clamp((float)K, 0.f, 1.f)); };

	// KO danger: from each athlete's centre of mass (over the floor) to his capture point; the cross is where he would have
	// to step to stop. Green inside the soles, red 15 cm outside
	for (int32 a = 0; a < 2; ++a)
	{
		const FVector C = Canvas->Project(FVector(H.Com[a].X, H.Com[a].Y, 1.0)), P = Canvas->Project(FVector(H.Capture[a].X, H.Capture[a].Y, 1.0));
		if (C.Z <= 0 || P.Z <= 0) continue;
		const FLinearColor Col = Heat(H.Danger[a]);
		DrawLine(C.X, C.Y, P.X, P.Y, Col, 2.f * S);
		const float R = 6.f * S;
		DrawLine(P.X - R, P.Y - R, P.X + R, P.Y + R, Col, 3.f * S);
		DrawLine(P.X - R, P.Y + R, P.X + R, P.Y - R, Col, 3.f * S);
		if (H.Danger[a] > 0.5 && Body[a].IsValid())
		{
			const FVector Hd = Canvas->Project(Body[a]->GetBoneLocation(BoneHead) + FVector(0.f, 0.f, 30.f));
			if (Hd.Z > 0) Text(FString::Printf(TEXT("KO DANGER %d%%"), FMath::RoundToInt(100.0 * H.Danger[a])), Hd.X, Hd.Y, Col.ToFColor(true), 1.2f * S, true);
		}
	}

	// the striker's palm, last 0.35 s: brighter where it was faster
	if (TrailOwner != Sk) { Trail.Reset(); TrailOwner = Sk; }
	if (Body[Sk].IsValid()) Trail.Emplace(Now, Body[Sk]->GetBoneLocation(BoneHandR));
	Trail.RemoveAll([Now](const TPair<double, FVector>& P) { return Now - P.Key > 0.35; });
	for (int32 i = 1; i < Trail.Num(); ++i)
	{
		const double Dt = FMath::Max(Trail[i].Key - Trail[i - 1].Key, 1e-4);
		const float V = (Trail[i].Value - Trail[i - 1].Value).Size() / 100.0 / Dt;	// m/s (game time: slow motion slows it too)
		const FVector A = Canvas->Project(Trail[i - 1].Value), B = Canvas->Project(Trail[i].Value);
		if (A.Z <= 0 || B.Z <= 0) continue;
		const float Age = 1.f - (Now - Trail[i].Key) / 0.35f;
		const float K = FMath::Clamp(V / (float)CleanSpeed, 0.f, 1.f);
		DrawLine(A.X, A.Y, B.X, B.Y, FLinearColor(1.f, 0.85f + 0.15f * K, 0.4f + 0.6f * K, Age), (2.f + 5.f * K) * S);
	}

	// head impact and kinetic chain: right column, under the menu button and the chat box
	const float W = 192.f * S, X = Canvas->ClipX - W - 8.f * S, Y = Top + 52.f * S, BarW = W - 24.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), X, Y, W, 184.f * S);
	Text(FString::Printf(TEXT("HEAD IMPACT  %s"), *H.Defender.ToUpper()), X + 12.f * S, Y + 6.f * S, Grey, S);
	auto Bar = [&](float By, double V, double Max, double Line, const FString& Label, const TCHAR* LineLabel)
	{
		DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.12f), X + 12.f * S, By, BarW, 10.f * S);
		DrawRect(Heat(V / Line), X + 12.f * S, By, BarW * FMath::Clamp((float)(V / Max), 0.f, 1.f), 10.f * S);
		const float Lx = X + 12.f * S + BarW * (Line / Max);
		DrawLine(Lx, By - 3.f * S, Lx, By + 13.f * S, FLinearColor::White, 2.f * S);
		Text(Label, X + 12.f * S, By + 13.f * S, FColor::White, 1.1f * S);
		Text(LineLabel, Lx, By + 15.f * S, Grey, 0.75f * S, true);
	};
	Bar(Y + 26.f * S, H.Hic, 1000.0, 700.0, FString::Printf(TEXT("HIC %.0f"), H.Hic), TEXT("crash-test limit"));
	Bar(Y + 64.f * S, H.Bric, 1.5, 1.0, FString::Printf(TEXT("BrIC %.2f"), H.Bric), TEXT("critical"));
	const float Cy = Y + 112.f * S;
	if (H.ChainOrder < 0)
	{
		Text(TEXT("KINETIC CHAIN"), X + 12.f * S, Cy, Grey, S);
		Text(TEXT("waiting for the touch"), X + 12.f * S, Cy + 20.f * S, Grey, 0.9f * S);
		return;
	}
	Text(FString::Printf(TEXT("KINETIC CHAIN  %d / 4 in order"), H.ChainOrder), X + 12.f * S, Cy, H.ChainOrder >= 3 ? Gold : Grey, S);
	// each link at the time it peaked, joined hips to hand; the axis spans the earliest peak (left) to the touch (right)
	float Span = 0.05f;
	for (const double C : H.Chain) Span = FMath::Max(Span, -(float)C);
	Text(FString::Printf(TEXT("peaks over the last %.0f ms"), 1000.f * Span), X + 12.f * S, Cy + 15.f * S, Grey, 0.75f * S);
	static const TCHAR* Links[5] = { TEXT("H"), TEXT("T"), TEXT("S"), TEXT("E"), TEXT("P") };
	FVector2D Prev = FVector2D::ZeroVector;
	for (int32 k = 0; k < 5; ++k)
	{
		const FVector2D Pt(X + 12.f * S + BarW * (1.f + (float)H.Chain[k] / Span), Cy + 30.f * S + 9.f * S * k);
		if (k > 0) DrawLine(Prev.X, Prev.Y, Pt.X, Pt.Y, H.Chain[k - 1] <= H.Chain[k] ? FLinearColor(0.35f, 0.9f, 0.45f) : FLinearColor(1.f, 0.3f, 0.25f), 2.f * S);
		DrawRect(FLinearColor(Gold), Pt.X - 3.f * S, Pt.Y - 3.f * S, 6.f * S, 6.f * S);
		Text(Links[k], Pt.X + 5.f * S, Pt.Y - 7.f * S, FColor::White, 0.75f * S);
		Prev = Pt;
	}
}

void ASlapHUD::DrawXRay(const FSlapHud& H)
{
	// each brain's target pose (where it is steering the body) as a stick figure, and a ring on what it just reacted to:
	// its own body parts, or the opponent's head and hand. Sizes are each group's share of the reaction (sums to 1)
	static const TCHAR* GroupName[10] = { TEXT("own legs"), TEXT("own arms"), TEXT("trunk"), TEXT("balance"), TEXT("rhythm"),
		TEXT("opp head"), TEXT("opp hand"), TEXT("memory"), TEXT("fatigue"), TEXT("own palm") };
	static const TArray<FName> GroupBones[10] = { { "thigh_l", "thigh_r", "shin_l", "shin_r", "foot_l", "foot_r" },
		{ "upperarm_l", "upperarm_r", "forearm_l", "forearm_r", "hand_l" }, { "belly", "chest", "neck" }, { "pelvis" }, {},
		{ "head" }, { "hand_r" }, { "head" }, { "chest" }, { "hand_r" } };
	for (int32 a = 0; a < 2; ++a)
	{
		const FLinearColor Team(TeamColor[a]);
		const TArray<FVector>& G = H.Ghost[a];
		for (int32 i = 0; i < G.Num() && i < H.GhostParent[a].Num(); ++i)
		{
			const int32 P = H.GhostParent[a][i];
			if (P < 0) continue;
			const FVector A = Canvas->Project(G[i]), B = Canvas->Project(G[P]);
			if (A.Z > 0 && B.Z > 0) DrawLine(A.X, A.Y, B.X, B.Y, Team.CopyWithNewOpacity(0.7f), 3.f * S);
		}
		for (int32 g = 0; g < 10; ++g)
		{
			const float F = H.Focus[a][g];
			if (F < 0.05f) continue;
			const int32 On = g == 5 || g == 6 ? 1 - a : a;	// the opponent's head and hand are on his body
			if (!Body[On].IsValid()) continue;
			for (const FName& Bone : GroupBones[g])
			{
				const FVector C = Canvas->Project(Body[On]->GetBoneLocation(Bone));
				if (C.Z <= 0) continue;
				const float R = (6.f + 36.f * F) * S;
				Ring(this, C, R, 24, Team.CopyWithNewOpacity(0.35f + 0.65f * F), 2.f * S);
			}
		}
		// the top three, in words, above the bottom panels
		int32 Order[10] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 };
		Algo::Sort(Order, [&](int32 L, int32 R) { return H.Focus[a][L] > H.Focus[a][R]; });
		FString Row = FString::Printf(TEXT("%s REACTS TO "), a ? TEXT("BLUE") : TEXT("RED"));
		for (int32 k = 0; k < 3 && H.Focus[a][Order[k]] > 0.f; ++k) Row += FString::Printf(TEXT(" %s %d%%"), GroupName[Order[k]], FMath::RoundToInt(100.f * H.Focus[a][Order[k]]));
		const float Ry = Bot - (bShowTelemetry ? 100.f : 60.f) * S + 22.f * S * a;	// above the telemetry strips
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 8.f * S, Ry - 2.f * S, 380.f * S, 20.f * S);
		Text(Row, 16.f * S, Ry, TeamColor[a], S);
	}
}

void ASlapHUD::Crowd(const FSlapHud& H, double Now)
{
	// the crowd follows the drama: the walk-in, a defender's KO danger, the last strike, a KO, the tension close-up, chat
	const int32 Def = Team(H.Defender);
	float Want = H.bWalkIn ? 0.5f : 0.f;
	if (H.Match > 0 && !H.bWalkIn) Want = FMath::Max(Want, 0.9f * (float)H.Danger[Def]);
	if (Now - StrikeAt < 3.0) Want = FMath::Max(Want, 0.6f * (float)StrikeK * FMath::Exp((StrikeAt - Now) / 1.2));
	if (Now - KOAt < KOShot) Want = 1.f;
	if (Shot == EShot::Tension) Want = FMath::Max(Want, 0.35f);
	Want = FMath::Max(Want, 0.6f * Hype);
	const float Dt = GetWorld()->GetDeltaSeconds();
	Tension = FMath::FInterpTo(Tension, Want, Dt, Want > Tension ? 6.f : 1.5f);	// up fast, down slowly
	for (UMaterialInstanceDynamic* M : CrowdSkin)
	{
		if (!M) continue;
		M->SetScalarParameterValue(PRise, Tension);
		M->SetScalarParameterValue(PCheer, Tension);
	}
	if (UAudioComponent* A = CrowdAudio.Get()) A->SetVolumeMultiplier(0.35f * (1.f + 1.6f * Tension) * (Now - StrikeAt < 0.2 ? 0.3f : 1.f));	// ducked for the slap
	// a gasp the moment a struck defender's capture point leaves his soles
	if (H.Danger[Def] > 0.7 && H.bStruck && !bGasped && CrowdReact)
	{
		bGasped = true;
		UGameplayStatics::PlaySound2D(this, CrowdReact, 0.5f, 1.25f);
		if (Thud && Body[Def].IsValid()) UGameplayStatics::PlaySoundAtLocation(this, Thud, Body[Def]->GetBoneLocation(BonePelvis), 0.6f, 1.1f, 0.f, Ringside);	// he stumbles
	}
	else if (H.Danger[Def] < 0.3) bGasped = false;
}

void ASlapHUD::Spotlight(const FSlapHud& H)
{
	// a narrow spot from above: on the striker through the wind-up, snapping onto the struck head at the touch and a KO
	USpotLightComponent* L = Spot.Get();
	if (!L || H.Match == 0) return;
	const double Now = GetWorld()->GetTimeSeconds();
	const bool bOnStruck = Now - StrikeAt < 1.6 || Now - KOAt < KOShot;
	const int32 Who = bOnStruck ? Struck : Team(H.Striker);
	if (!Body[Who].IsValid()) return;
	const FVector Head = Body[Who]->GetBoneLocation(BoneHead);
	const FVector Want = bOnStruck ? Head : 0.5f * (Head + Body[Who]->GetBoneLocation(BonePelvis));
	SpotAim = SpotAim.IsZero() ? Want : FMath::VInterpTo(SpotAim, Want, GetWorld()->GetDeltaSeconds(), bOnStruck ? 14.f : 4.f);
	const FVector Pos = SpotAim + FVector::UpVector * 520.f - BaseRot.Vector() * 160.f;	// above, a little toward the camera
	L->SetWorldLocationAndRotation(Pos, (SpotAim - Pos).Rotation());
	L->SetIntensity(SpotBase * (Now - StrikeAt < 1.0 ? 1.f + 1.5f * StrikeK * FMath::Exp(-6.0 * (Now - StrikeAt)) : 1.f));
}

void ASlapHUD::Scuffs(const FSlapHud& H)
{
	// from the drawn bones (display only): a sole skidding along the floor squeaks and leaves a streak; a palm landing on the
	// podium leaves a mark where it braced
	const double Now = GetWorld()->GetTimeSeconds();
	const float Dt = GetWorld()->GetDeltaSeconds();
	if (!Podium.IsValid)
	{
		AAMjManager* Manager = AAMjManager::GetManager();
		const mjModel* m = Manager && Manager->PhysicsEngine ? Manager->PhysicsEngine->GetModel() : nullptr;
		const int32 g = m ? Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("podium")) : -1;
		if (g >= 0) Podium = FBox::BuildAABB(MjUtils::MjToUEPosition(m->geom_pos + 3 * g), 100.0 * FVector(m->geom_size[3 * g], m->geom_size[3 * g + 1], m->geom_size[3 * g + 2]));
	}
	const bool bMark = H.Match > 0 && !H.bWalkIn && Now >= ReplayEnd && Dt > 0.f;
	for (int32 a = 0; a < 2; ++a)
	{
		if (!Body[a].IsValid()) continue;
		for (int32 f = 0; f < 2; ++f)
		{
			const FVector P = Body[a]->GetBoneLocation(f ? BoneFootR : BoneFootL);
			const FVector V = (P - FootPrev[a][f]) / FMath::Max(Dt, 1e-4f);
			FootPrev[a][f] = P;
			FootLow[a][f] = FMath::Min(FootLow[a][f], (float)P.Z);	// the ankle's planted height
			const float Sp = V.Size2D();	// cm/s
			Slide[a][f] = bMark && P.Z < FootLow[a][f] + 3.f && Sp > 50.f && Sp < 400.f ? Slide[a][f] + 1 : 0;	// a reset's jump breaks the run
			if (Slide[a][f] < 4 || Now < FootNext[a][f]) continue;	// a skid, not a twitch
			FootNext[a][f] = Now + 0.5;
			Scuff(FVector(P.X, P.Y, 0.0), V.Rotation().Yaw, FVector(10.f, 5.f, 14.f));
			USoundBase* Snd = Sp > 90.f && Squeaks.Num() ? Squeaks[FMath::RandHelper(Squeaks.Num())].Get() : Scrape.Get();
			if (Snd) UGameplayStatics::PlaySoundAtLocation(this, Snd, P, FMath::Clamp(Sp / 150.f, 0.3f, 1.2f), FMath::FRandRange(0.9f, 1.1f), 0.f, Ringside);
		}
		for (int32 h = 0; h < 2 && Podium.IsValid; ++h)
		{
			const FVector P = Body[a]->GetBoneLocation(h ? BoneHandR : BoneHandL);	// the wrist: the palm lies within ~12 cm
			const bool bOn = FMath::Abs(P.Z - Podium.Max.Z) < 12.f && P.X > Podium.Min.X - 6.f && P.X < Podium.Max.X + 6.f && P.Y > Podium.Min.Y - 4.f && P.Y < Podium.Max.Y + 4.f;
			if (bOn && !bPalmDown[a][h] && bMark) Scuff(FVector(P.X, P.Y, Podium.Max.Z), 0.f, FVector(10.f, 7.f, 9.f));
			bPalmDown[a][h] = bOn;
		}
	}
}

void ASlapHUD::Scuff(const FVector& At, float Yaw, const FVector& Size)
{
	// a deferred decal pointing down (its U runs along Yaw), the match's last 8 kept: a phone pays per decal
	if (!ScuffMat) return;
	if (UDecalComponent* D = UGameplayStatics::SpawnDecalAtLocation(this, ScuffMat, Size, At, FRotator(-90.f, Yaw, 0.f)))
	{
		D->SetFadeScreenSize(0.f);
		Marks.Add(D);
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: scuff at %s"), *At.ToCompactString());
	}
	while (Marks.Num() > 8)
	{
		if (Marks[0].IsValid()) Marks[0]->DestroyComponent();
		Marks.RemoveAt(0);
	}
}

void ASlapHUD::Pant(const FSlapHud& H)
{
	// each athlete breathes at his head: louder and quicker as stamina drains; the striker blows for a moment after a swing
	const double Now = GetWorld()->GetTimeSeconds();
	for (int32 a = 0; a < 2; ++a)
	{
		UAudioComponent* A = Breath[a].Get();
		if (!A || !Body[a].IsValid()) continue;
		const double Tired = H.Match > 0 ? FMath::Clamp((1.0 - H.Stamina[a]) / (1.0 - MinStamina), 0.0, 1.0) : 0.0;
		const double Effort = a != Struck && Now - StrikeAt < 3.0 ? 0.4 * FMath::Exp(StrikeAt - Now) : 0.0;
		const float Need = FMath::Min(1.0, Tired + Effort);
		A->SetWorldLocation(Body[a]->GetBoneLocation(BoneHead));
		A->SetVolumeMultiplier(0.01f + 0.6f * Need * Need);
		A->SetPitchMultiplier(1.f + 0.45f * Need);
	}
}

void ASlapHUD::LiveSend(const FString& Msg)
{
	// Azure Web PubSub's JSON subprotocol wraps every message for the "live" group; the local relay takes it bare
	Socket->Send(bAzure ? TEXT("{\"type\":\"sendToGroup\",\"group\":\"live\",\"dataType\":\"json\",\"noEcho\":true,\"data\":") + Msg + TEXT("}") : Msg);
}

void ASlapHUD::Live(const FSlapHud& H)
{
	// Second screen (Web/index.html?live=...): both athletes' joints at 30 Hz and every match event, so a phone draws the
	// match with the browser's own renderer instead of a Pixel Streaming video. Off unless switched on (VIEW, -SlapLive=)
	USlapController* C = Controller.Get();
	const double Now = GetWorld()->GetRealTimeSeconds();
	if (!bLive || !C)
	{
		if (Socket) { Socket->Close(); Socket.Reset(); }
		if (C) C->bLiveFeed = false;
		return;
	}
	if (!Socket && Now >= LiveRetry)
	{
		bAzure = LiveUrl.Contains(TEXT("webpubsub.azure.com"));
		Socket = FModuleManager::LoadModuleChecked<FWebSocketsModule>(TEXT("WebSockets")).CreateWebSocket(LiveUrl, bAzure ? TEXT("json.webpubsub.azure.v1") : TEXT(""));
		Socket->OnConnected().AddLambda([Url = LiveUrl] { UE_LOG(LogTemp, Log, TEXT("SlapHUD: second screen connected to %s"), *Url); });
		Socket->OnConnectionError().AddLambda([Url = LiveUrl](const FString& Why) { UE_LOG(LogTemp, Warning, TEXT("SlapHUD: second screen: no connection to %s (%s)"), *Url, *Why); });
		Socket->Connect();
		LiveRetry = Now + 5.0;
	}
	if (!Socket) return;
	if (!Socket->IsConnected())
	{
		if (Now >= LiveRetry) Socket.Reset();	// try again
		C->bLiveFeed = false;
		return;
	}
	LiveRetry = Now + 5.0;
	C->bLiveFeed = true;
	TArray<FString> Events;
	C->TakeEvents(Events);
	for (const FString& E : Events) LiveSend(TEXT("{\"t\":\"ev\",\"e\":") + E + TEXT("}"));
	if (Now - LiveSent < 1.0 / 30.0 || H.Pose.IsEmpty()) return;
	LiveSent = Now;
	TStringBuilder<2048> Msg;
	Msg.Appendf(TEXT("{\"t\":\"pose\",\"c\":[\"%s\",\"%s\"],\"m\":%d,\"r\":%d,\"s\":\"%s\",\"w\":%s,\"slow\":%.2f,\"q\":["),
		*H.Card[0], *H.Card[1], H.Match, H.Round, *H.Striker, H.bWalkIn ? TEXT("true") : TEXT("false"), H.SlowMo);
	for (int32 i = 0; i < H.Pose.Num(); ++i) Msg.Appendf(TEXT("%s%.4f"), i ? TEXT(",") : TEXT(""), H.Pose[i]);
	Msg << TEXT("]}");
	LiveSend(FString(Msg));
}

void ASlapHUD::LoadLeague()
{
	// training/scouting.py: Elo and tendencies per brain from every logged match; a missing file = no scouting line
	const TSharedPtr<FJsonObject> J = ReadJson(FPaths::ProjectDir() / TEXT("training/models/league.json"));
	if (!J) return;
	const TSharedPtr<FJsonObject>* League = nullptr;
	if (!J->TryGetObjectField(TEXT("brains"), League)) return;
	Scout.Reset();
	for (const TPair<FString, TSharedPtr<FJsonValue>>& B : (*League)->Values)
	{
		const TSharedPtr<FJsonObject> O = B.Value->AsObject();
		if (!O) continue;
		Scout.Add(B.Key, FString::Printf(TEXT("ELO %.0f   %d-%d-%d   KO %.0f%%   clean %.0f%%   %.1f m/s"), O->GetNumberField(TEXT("elo")),
			(int32)O->GetNumberField(TEXT("wins")), (int32)O->GetNumberField(TEXT("losses")), (int32)O->GetNumberField(TEXT("draws")),
			100.0 * O->GetNumberField(TEXT("ko_rate")), 100.0 * O->GetNumberField(TEXT("clean_rate")), O->GetNumberField(TEXT("speed"))));
	}
}
