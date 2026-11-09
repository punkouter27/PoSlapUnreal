// ASlapHUD: the frame loop, the broadcast's assets, the loading curtain and the screenshots (SlapHUD.h lists the other files).

#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Components/AudioComponent.h"
#include "Components/LightComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/Light.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/Texture.h"
#include "Engine/UserInterfaceSettings.h"
#include "EngineUtils.h"
#include "Framework/Application/SlateApplication.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "InputCoreTypes.h"
#include "IWebSocket.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "MuJoCo/Components/Geometry/MjGeom.h"
#include "NiagaraSystem.h"
#include "RHI.h"
#include "Scalability.h"
#include "SceneView.h"
#include "ShaderCompiler.h"
#include "SlapAthleteVisual.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "Sound/ReverbEffect.h"
#include "Sound/SoundAttenuation.h"
#include "Sound/SoundBase.h"
#include "Sound/SoundSubmix.h"
#include "UnrealClient.h"
#include "UObject/UObjectIterator.h"

using namespace SlapUi;

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
	UE_LOG(LogSlap, Log, TEXT("Chaos audit: %d primitive components, %d had collision/physics and were turned off; %d athlete collision shapes hidden"), Total, Turned, Hidden);
}

void ASlapHUD::BeginPlay()
{
	Super::BeginPlay();
	const TCHAR* Cmd = FCommandLine::Get();
	FParse::Value(Cmd, TEXT("SlapShot="), ShotAt);
	bShotOnResult = FParse::Param(Cmd, TEXT("SlapShotResult"));
	FParse::Value(Cmd, TEXT("SlapShotStrike="), ShotAfterStrike);
	FParse::Value(Cmd, TEXT("SlapFxTest="), FxTestAt);
	bEventShots = FParse::Param(Cmd, TEXT("SlapShotEvents"));	// the report's screenshots: one per new widget as it first shows, then the result
	bMenu = FParse::Value(Cmd, TEXT("SlapMenu="), Tab);
	Tab = FMath::Clamp(Tab, 0, NumTabs - 1);
	bUiTest = FParse::Param(Cmd, TEXT("SlapUiTest"));
	bRoundTripTest = FParse::Param(Cmd, TEXT("SlapRoundTripTest"));
	bPairTest = FParse::Param(Cmd, TEXT("SlapPairTest"));
	bTourneyAuto = FParse::Param(Cmd, TEXT("SlapTourney"));
	bTour = FParse::Param(Cmd, TEXT("SlapTour"));
	bLayoutTest = FParse::Param(Cmd, TEXT("SlapLayoutTest"));
	int32 Cv = 0;	// the display pass's switches: -SlapDisplayPass=0 (the parity proof), -SlapHudLog=0|1|2
	if (FParse::Value(Cmd, TEXT("SlapDisplayPass="), Cv)) IConsoleManager::Get().FindConsoleVariable(TEXT("slap.DisplayPass"))->Set(Cv, ECVF_SetByCommandline);
	if (FParse::Value(Cmd, TEXT("SlapHudLog="), Cv)) IConsoleManager::Get().FindConsoleVariable(TEXT("slap.HudLog"))->Set(Cv, ECVF_SetByCommandline);
	bPass = DisplayPassOn();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: display pass %s, HUD log level %d"), bPass ? TEXT("on") : TEXT("OFF"), HudLogLevel());
	FString View;	// overlays on from the start (screenshots, checks; '+', not commas)
	if (FParse::Value(Cmd, TEXT("SlapView="), View))
	{
		bShowTelemetry = View.Contains(TEXT("telemetry"));
		bShowScience = View.Contains(TEXT("science"));
		bShowXRay = View.Contains(TEXT("xray"));
		bShowCommentary = View.Contains(TEXT("commentary")) || !View.Contains(TEXT("nocommentary"));
		bShowDebug = View.Contains(TEXT("debug"));
		bSlowMo = !View.Contains(TEXT("noslow"));
	}
	FString CamFlag;
	if (FParse::Value(Cmd, TEXT("SlapCam="), CamFlag))
	{
		for (int32 c = 0; c < UE_ARRAY_COUNT(CamName); ++c)
			if (CamFlag.Equals(CamName[c], ESearchCase::IgnoreCase)) CamMode = (ECam)c;
	}
	FParse::Value(Cmd, TEXT("SlapTier="), Tier);
	Tier = FMath::Clamp(Tier, -1, PLATFORM_ANDROID || PLATFORM_IOS ? 2 : 3);	// 3 CINEMA: desktop only (ApplyCinema)
	FParse::Value(Cmd, TEXT("SlapCine="), CineFeatures);
	bLive = FParse::Value(Cmd, TEXT("SlapLive="), LiveUrl);
	FString Zone;	// -SlapSafeZone=0.05+0.03: a fake notch (top) and nav bar (bottom), fractions of the screen height
	if (FParse::Value(Cmd, TEXT("SlapSafeZone="), Zone) && FSlateApplication::IsInitialized())
	{
		FString T, B;
		Zone.Split(TEXT("+"), &T, &B);
		FakeSafe = FMargin(0.f, 2.f * FCString::Atof(*T), 0.f, 2.f * FCString::Atof(*B));	// ratios of the half height; DrawHUD keeps it applied
	}
	if ((PLATFORM_ANDROID || PLATFORM_IOS) && GEngine) GEngine->bEnableOnScreenDebugMessages = false;	// URLab's ZMQ stubs print red bind errors over a phone's arena
	BuildUi();
	LoadAssets();
	CommentaryStamp = IFileManager::Get().GetTimeStamp(*(FPaths::ProjectSavedDir() / TEXT("Commentary/latest.txt")));	// only lines from now on
	bBurst = FParse::Param(Cmd, TEXT("SlapBurst")) && !PLATFORM_ANDROID && !PLATFORM_IOS;	// the 120 fps burst is desktop only
	TierCap = DetectTier();
	ApplyTier(Tier >= 0 ? Tier : TierCap);
}

void ASlapHUD::LoadAssets()
{
	// the broadcast's assets (Scripts/broadcast_fx.py, broadcast_audio.py); a missing one is skipped
	Spray = Load<UNiagaraSystem>(TEXT("/Game/FX/NS_SlapSpray.NS_SlapSpray"));
	DustPuff = Load<UNiagaraSystem>(TEXT("/Game/FX/NS_DustPuff.NS_DustPuff"));
	for (const TCHAR* N : { TEXT("S_Slap"), TEXT("S_Slap1"), TEXT("S_Slap2"), TEXT("S_Slap3") })
		if (USoundBase* Snd = LoadNamed<USoundBase>(TEXT("/Game/Audio"), N)) SlapTakes.Add(Snd);
	for (const TCHAR* N : { TEXT("S_Squeak0"), TEXT("S_Squeak1") })
		if (USoundBase* Snd = LoadNamed<USoundBase>(TEXT("/Game/Audio"), N)) Squeaks.Add(Snd);
	Thud = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_Thud"));
	Scrape = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_Scrape"));
	Step = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_Step"));
	BreathLoop = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_Breath"));
	CrowdReact = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_CrowdReact"));
	CrowdNear = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_CrowdNear"));
	CrowdLoop = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_CrowdLoop"));
	Bell = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_Bell"));
	StepDeck = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_StepDeck"));
	PAChime = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("S_PAChime"));
	// the impact synthesis (Scripts/broadcast_metasound.py: a MetaSound built headlessly) layered on the recorded takes, desktop only
	if (!PLATFORM_ANDROID && !PLATFORM_IOS) ImpactSynth = LoadNamed<USoundBase>(TEXT("/Game/Audio"), TEXT("MS_ImpactSynth"));
	// the arena's impulse response (broadcast_audio.py ArenaIR.wav -> broadcast_fx.py RV_ArenaIR, a convolution reverb
	// submix): every spatialised one-shot sends to it on desktop; a phone keeps the two algorithmic reverbs (CPU)
	if (!PLATFORM_ANDROID && !PLATFORM_IOS) ArenaIR = LoadNamed<USoundSubmixBase>(TEXT("/Game/Audio"), TEXT("RV_ArenaIR"));
	ScuffMat = Load<UMaterialInterface>(TEXT("/Game/FX/M_Scuff.M_Scuff"));
	DecalMat = Load<UMaterialInterface>(TEXT("/Game/FX/M_DecalTex.M_DecalTex"));
	for (const TCHAR* N : { TEXT("T_Decal_SoleSkid_01"), TEXT("T_Decal_SoleSkid_02"), TEXT("T_Decal_PalmSmudge_01") }) DecalTex.Add(LoadNamed<UTexture>(TEXT("/Game/FX/Decals"), N));
	// Two reverbs, blended by the camera's distance to the contact (ReverbMix): the hall rings out on a wide shot, a tight
	// close-up is dry. Both come from broadcast_fx.py; if the build predates the ringside one, it is made here.
	HallVerb = LoadNamed<UReverbEffect>(TEXT("/Game/Audio"), TEXT("RV_Arena"));
	CloseVerb = LoadNamed<UReverbEffect>(TEXT("/Game/Audio"), TEXT("RV_Ringside"));
	if (!CloseVerb)
	{
		CloseVerb = NewObject<UReverbEffect>(this, TEXT("RV_Ringside"));
		CloseVerb->DecayTime = 0.45f; CloseVerb->Density = 1.f; CloseVerb->Diffusion = 0.6f;
		CloseVerb->Gain = 0.25f; CloseVerb->GainHF = 0.4f; CloseVerb->ReflectionsGain = 0.15f;
		CloseVerb->ReflectionsDelay = 0.008f; CloseVerb->LateGain = 0.6f; CloseVerb->LateDelay = 0.02f;
	}
	if (HallVerb) UGameplayStatics::ActivateReverbEffect(this, HallVerb, TEXT("Arena"), 0.f, 1.f, 0.f);
	if (CloseVerb) UGameplayStatics::ActivateReverbEffect(this, CloseVerb, TEXT("Ringside"), 0.f, 0.f, 0.f);
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
	if (!PLATFORM_ANDROID && !PLATFORM_IOS)	// binaural on desktop: the Resonance Audio spatializer's HRTF (Config/DefaultEngine.ini)
		Ringside->Attenuation.SpatializationAlgorithm = ESoundSpatializationAlgorithm::SPATIALIZATION_HRTF;
	UScriptStruct* SendType = FindObject<UScriptStruct>(nullptr, TEXT("/Script/Engine.AttenuationSubmixSendSettings"));
	if (ArenaIR && SendType)	// the hall's own reflections (Web/audio.js "wet" sends ~0.15-0.3): the algorithmic hall steps back
	{
		Ringside->Attenuation.bEnableSubmixSends = true;
		// the struct's constructor is not exported from the engine module: construct it through its reflection data
		TArray<FAttenuationSubmixSendSettings>& Sends = Ringside->Attenuation.SubmixSendSettings;
		FAttenuationSubmixSendSettings& Send = Sends[Sends.AddUninitialized()];
		SendType->InitializeStruct(&Send);
		Send.SoundSubmix = ArenaIR;
		Send.SendLevelControlMethod = ESendLevelControlMethod::Manual;
		Send.SendLevel = 0.2f;
		Send.MaxSendLevel = 1.f;
	}
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
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: spray %s dust %s, sounds slap %d squeak %d thud %d scrape %d step %d breath %d crowd %d/%d/%d bell %d deck %d chime %d IR %d synth %d, scuff %d decals %d, reverbs %d/%d, lights key %d rims %d/%d truss %d, crowd materials %d, league %d brains"),
		Spray ? TEXT("yes") : TEXT("MISSING"), DustPuff ? TEXT("yes") : TEXT("MISSING"),
		SlapTakes.Num(), Squeaks.Num(), Thud != nullptr, Scrape != nullptr, Step != nullptr, BreathLoop != nullptr, CrowdReact != nullptr, CrowdNear != nullptr, CrowdLoop != nullptr, Bell != nullptr, StepDeck != nullptr, PAChime != nullptr, ArenaIR != nullptr, ImpactSynth != nullptr,
		ScuffMat != nullptr, DecalMat != nullptr, HallVerb != nullptr, CloseVerb != nullptr, Key.Light.IsValid(), Rim[0].Light.IsValid(), Rim[1].Light.IsValid(), Truss.Num(), CrowdSkin.Num(), Scout.Num());
}

void ASlapHUD::FindController()
{
	if (!Controller.IsValid())	// the controller playing the match (URLab may keep more than one instance around)
	{
		for (TObjectIterator<USlapController> It; It; ++It)
		{
			if (It->HasAnyFlags(RF_ClassDefaultObject | RF_ArchetypeObject)) continue;
			It->GetHud(Last);
			if (Last.Match > 0 || It->bWaitForStart) { Controller = *It; break; }
		}
	}
	if (Controller.IsValid())
	{
		Controller->GetHud(Last);
		KOShot = Controller->PostMatchHold;
	}
	else Last = FSlapHud();
}

void ASlapHUD::Layout()
{
	// layout rows: below the top bar and above the bottom bar (BuildAnchors). The engine already shrinks this canvas into
	// the title-safe area (UCanvas::ApplySafeZoneTransform) and the camera keeps it 9:16, so the canvas starts at the safe
	// edge or the letterbox edge, whichever is further in; the bars start at the safe edge
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
	TickerY = Bot - 18.f * S;	// the event ticker's row, right above the bottom anchors: what hangs low starts above it
	if (bPass) Bot = TickerY - 2.f * S;

	// The menu sheet's height budget, in Slate units. The layer's own height is measured, so the budget follows the safe
	// zone and any resize; the sheet's content is clipped inside it, so no tab can run off the screen and nothing scrolls.
	// The sheet sits above the corner anchors' row (and the platform's bottom inset), so the build stamp stays visible.
	const float LayerH = Ui.IsValid() ? Ui->GetCachedGeometry().GetLocalSize().Y : 0.f;
	const float ScreenSlate = LayerH > 1.f ? LayerH : View.Y / FMath::Max(Dpi, 0.01f);
	SafeBottomSlate = Safe.Bottom / FMath::Max(Dpi, 0.01f);
	SheetBudget = FMath::Max(160.f, FMath::Min(ScreenSlate * 0.78f, ScreenSlate - SafeBottomSlate - BottomBar - 24.f));
}

void ASlapHUD::Keys()
{
	APlayerController* PC = GetOwningPlayerController();
	if (!PC) return;
	const bool bTab = PC->IsInputKeyDown(EKeys::Tab);	// TAB: the detailed stats panel (the default HUD stays clean)
	if (bTab && !bTabDown)
	{
		bShowTelemetry = !bShowTelemetry;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: TAB: the stats panel is %s"), bShowTelemetry ? TEXT("on") : TEXT("off"));
	}
	bTabDown = bTab;
	const bool bCam = PC->IsInputKeyDown(EKeys::C);	// C: the camera mode (AUTO -> WIDE -> SIDE -> CLOSE)
	if (bCam && !bCamDown) SetCam((ECam)(((int32)CamMode + 1) % UE_ARRAY_COUNT(CamName)));
	bCamDown = bCam;
	const bool bSpace = PC->IsInputKeyDown(EKeys::SpaceBar);	// SPACE: skip a replay, back to live
	if (bSpace && !bSpaceDown) SkipReplay();
	bSpaceDown = bSpace;
}

void ASlapHUD::DrawHUD()
{
	Super::DrawHUD();
	const double Now = GetWorld()->GetTimeSeconds();

	APlayerController* PC = GetOwningPlayerController();	// view through the level's (portrait) camera
	if (!Camera.IsValid())
	{
		TActorIterator<ACameraActor> It(GetWorld());
		if (It) Camera = *It;
	}
	if (PC && Camera.IsValid() && PC->GetViewTarget() != Camera.Get())
	{
		PC->SetViewTarget(Camera.Get());
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: viewing through %s at %s"), *Camera->GetName(), *Camera->GetActorLocation().ToString());
	}

	const float Dt = GetWorld()->GetDeltaSeconds();	// the performance log: one line every 10 s, never per frame
	FpsSum += Dt;
	FpsWorst = FMath::Max(FpsWorst, (double)Dt);
	++FpsFrames;
	if (FpsFrames > 1 && GetWorld()->GetRealTimeSeconds() - FpsSince >= 10.0)
	{
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: %.1f fps average, worst frame %.1f ms (%d frames), tier %d, GPU %.1f ms, cinema %s"), FpsFrames / FpsSum, FpsWorst * 1000.0, FpsFrames, TierLive,
			FPlatformTime::ToMilliseconds(RHIGetGPUFrameCycles(0)), TierLive == 3 ? *CineFeatures : TEXT("off"));
		WorstMs = FpsWorst * 1000.0;
		FpsSum = FpsWorst = 0.0;
		FpsFrames = 0;
		FpsSince = GetWorld()->GetRealTimeSeconds();
	}
	Fps += 0.05f * (1.f / FMath::Max(Dt, 1e-4f) - Fps);

	Layout();
	FindController();
	if (bTravelling && TravelledAt >= 0.0 && Now - TravelledAt > 6.0)
	{
		// the level never opened: give the menu back instead of leaving every control inert for good
		bTravelling = false;
		TravelledAt = -1.0;
		UE_LOG(LogSlap, Warning, TEXT("SlapHUD: the pairing level did not open within 6 s: the menu is live again"));
	}
	RoundTrip();
	TourneyTick(Now);
	FSlapHud& H = Last;	// -SlapFxTest's fakes below show in the menu too: only that check sees them
	CacheAthletes();
	const int32 FxDef = Team(H.Defender);
	if (FxTestAt >= 0.f && H.Match > 0 && Body[FxDef].IsValid() && Now >= FxTestAt)	// -SlapFxTest
	{
		H.StrikeSeq = -1;
		H.StrikeDefender = FxDef;
		H.Speed = 9.0;
		H.StrikePos = Body[FxDef]->GetBoneTransformByName(BoneHead, EBoneSpaces::WorldSpace).GetLocation() + FVector(FxDef ? -9.f : 9.f, -5.f, -4.f);	// the cheek toward the striker
		H.StrikeDir = FVector(0.f, 1.f, 0.f);
		H.Neck[FxDef] = 0.6;
		H.Stamina[0] = H.Stamina[1] = 0.75;
		if (Now >= FxTestAt + 1.5f) H.KOSeq = -1;
	}
	bPass = DisplayPassOn();	// slap.DisplayPass can flip it live
	Keys();
	if (H.Match > 0) Sense(H);	// before the cues: the strike reads the flinch
	if (H.Match > 0) Cues(H);
	Spotlight(H);
	Scuffs(H);
	Pant(H);
	AnnounceTick();
	Steps(H);
	Dust(H);
	if (USlapController* C = Controller.Get())	// the VIEW switches the physics thread reads
	{
		C->bXRay = bShowXRay;
		C->bSlowMo = bSlowMo;
	}
	if (Dilation != H.SlowMo)	// live slow motion: the world clock (effects, camera, cues) runs at the physics pace
	{
		Dilation = H.SlowMo;
		UGameplayStatics::SetGlobalTimeDilation(this, Dilation);
		SLAP_HUD_LOG(1, TEXT("SlapHUD: slow motion %.2f at %.2f s"), Dilation, Now);
	}
	Crowd(H, Now);
	ReverbMix(Now);
	Burst(Now);
	AutoTier();
	Live(H);
	if (Camera.IsValid()) Direct(Camera.Get(), H);
	if (H.Match > 0 && !(bMenu && !H.Result.IsEmpty()))	// the menu over a finished match: the arena and the menu, nothing else
	{
		Boxes.Reset();
		TextOverflows = TextShrunk = 0;
		DrawMatch(H);		// the scoreboard
		DrawMomentum(H);	// who is winning
		DrawNamePlates(H);	// names, badges, stamina, neck, danger
		if (bShowTelemetry) DrawTelemetry(H);
		if (bShowTelemetry) DrawStrain(H);
		if (bShowScience) DrawScience(H);
		if (bShowXRay) DrawXRay(H);
		DrawOverlays(H);
		DrawFlinch(H);
		if (H.Result.IsEmpty()) DrawImpactCard(H);	// the result card owns the bottom once it is over
		DrawScouting(H);
		DrawTicker(H);
		DrawTell(H);
		DrawJudges(H);
		DrawWinner(H);
		DrawCaptions(H);	// ROUND / KNOCKDOWN / KNOCKOUT / WINS, the turn banner, the running score
		if (Now < ReplayEnd && bAutoReplay) DrawStrikeReplayCaption(H);
		else if (Now < ReplayEnd) Text(bQuarter ? TEXT("REPLAY   1/4 SPEED") : TEXT("REPLAY   1/2 SPEED"), Canvas->ClipX * 0.5f, PanelBottom + 6.f * S, Gold, 1.4f * S, true, GEngine->GetMediumFont());
	}

	if (bUiTest) UiTest(H);
	if (bRoundTripTest) RoundTripTest(H);
	if (bTour) Tour(H);
	if (bLayoutTest) LayoutAudit();
	if (bPairTest) PairTest(H);
	Screenshot();
	if (!Curtain() && Controller.IsValid()) Controller->MarkDrawn();
}

bool ASlapHUD::Curtain()
{
	// The loading curtain: until the shaders are compiled the athletes draw as grey T-posed placeholders on a checker floor
	// (uncooked -game, 2026-10-30). Black until then, and the walk-in waits behind it (MarkDrawn feeds the controller's frame
	// heartbeat). 30 s at most, so a stuck compile cannot hide the match for good.
	if (!bSceneReady)
	{
		const int32 Jobs = GShaderCompilingManager ? GShaderCompilingManager->GetNumRemainingJobs() : 0;
		const double Now = FPlatformTime::Seconds();
		if (Jobs > 0 || LoadStart < 0.0) QuietSince = -1.0;
		else if (QuietSince < 0.0) QuietSince = Now;
		if (LoadStart < 0.0) LoadStart = Now;
		bSceneReady = (QuietSince >= 0.0 && Now - QuietSince >= 0.3) || Now - LoadStart > 30.0;
		if (bSceneReady) UE_LOG(LogSlap, Log, TEXT("SlapHUD: scene ready after %.1f s (%d shader jobs left)"), Now - LoadStart, Jobs);
	}
	if (bSceneReady) return false;
	DrawRect(FLinearColor::Black, 0.f, 0.f, Canvas->ClipX, Canvas->ClipY);
	const FString Msg = TEXT("LOADING THE ARENA");
	float W = 0.f, TH = 0.f;
	Canvas->TextSize(GEngine->GetLargeFont(), Msg, W, TH);
	Canvas->SetDrawColor(FColor(200, 200, 200));
	Canvas->DrawText(GEngine->GetLargeFont(), Msg, (Canvas->ClipX - W) * 0.5f, Canvas->ClipY * 0.5f);
	return true;
}

void ASlapHUD::EventShots(const FSlapHud& H)
{
	// -SlapShotEvents: Saved/Screenshots/pass/<name>.png the first time each moment comes round (one request at a time),
	// then -SlapShotResult's shot and quit. Works with the display pass off too (the "before" set: plates, tab, result).
	if (!bEventShots || !bSceneReady || H.Match == 0 || bShotTaken) return;
	const double Now = GetWorld()->GetTimeSeconds(), Real = FPlatformTime::Seconds();
	if (Real - EventShotAt < 0.6) return;
	auto Shoot = [&](const TCHAR* Name)
	{
		if (EventShotsTaken.Contains(Name)) return false;
		EventShotsTaken.Add(Name);
		EventShotAt = Real;
		FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir() / TEXT("Screenshots/pass") / FString(Name) + TEXT(".png"), true, false);
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: event shot %s at %.2f s"), Name, Now);
		return true;
	};
	if (!H.bWalkIn && !H.bStruck && H.Result.IsEmpty() && H.Clock < StrikeClock - 3.0 && !bShowTelemetry && Shoot(TEXT("plates"))) return;
	if (bAutoReplay && Now < ReplayEnd && Now > ReplayEnd - 0.7 && Shoot(TEXT("replay"))) return;
	if (ScoutAt > 0.0 && Now - ScoutAt > 0.8 && Now - ScoutAt < 4.0 && Shoot(TEXT("scouting"))) return;
	if (FlinchAt > 0.0 && Now - FlinchAt > 0.1 && Now - FlinchAt < 1.0 && Shoot(TEXT("flinch"))) return;
	if (EventShotsTaken.Contains(TEXT("plates")) && Now - StrikeAt > 0.1 && Now - StrikeAt < 1.5 && StrikeAt > 0.0 && Shoot(TEXT("strike"))) return;
	if (EventShotsTaken.Contains(TEXT("strike")) && !bShowTelemetry && !EventShotsTaken.Contains(TEXT("tab")) && Now >= ReplayEnd) { bShowTelemetry = true; return; }	// TAB on for the strain heat
	if (bShowTelemetry && !H.bStruck && (Read.bReading || H.HandSpeed > 3.0) && Now >= ReplayEnd && Shoot(TEXT("tab"))) return;
}

void ASlapHUD::Screenshot()
{
	EventShots(Last);
	const double Now = GetWorld()->GetTimeSeconds();
	if (bShotOnResult && ShotAt < 0.f && !Last.Result.IsEmpty()) ShotAt = Now + 0.5f;
	if (ShotAt < 0.f || Now < ShotAt) return;
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
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: screenshot saved: %s"), *ShotFile);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	}
	else if ((FDateTime::UtcNow() - ShotRequested).GetTotalSeconds() > 5.0)	// a viewport that skipped drawing drops the request: ask again
	{
		if (++ShotRetries > 2)
		{
			UE_LOG(LogSlap, Warning, TEXT("SlapHUD: -SlapShot: no screenshot after 3 requests (window hidden?), quitting"));
			UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
		}
		ShotRequested = FDateTime::UtcNow();
		FScreenshotRequest::RequestScreenshot(ShotFile, true, false);
	}
}

void ASlapHUD::EndPlay(const EEndPlayReason::Type Reason)
{
	// a tier changes the engine's scalability for this session only: put the launch levels back, or the engine saves the
	// AUTO step-down (a slow shader warm-up) into GameUserSettings.ini and every later launch starts at LOW (2026-11-04)
	if (BaseQuality[0] >= 0)
	{
		Scalability::FQualityLevels Q = Scalability::GetQualityLevels();
		Q.ShadowQuality = BaseQuality[0]; Q.PostProcessQuality = BaseQuality[1]; Q.EffectsQuality = BaseQuality[2];
		Q.FoliageQuality = BaseQuality[3]; Q.ShadingQuality = BaseQuality[4];
		Scalability::SetQualityLevels(Q);
	}
	UGameViewportClient* VC = GetWorld()->GetGameViewport();
	if (VC && Ui.IsValid()) VC->RemoveViewportWidgetContent(Ui.ToSharedRef());
	Ui.Reset();
	if (Socket) Socket->Close();
	Socket.Reset();
	Super::EndPlay(Reason);
}

void ASlapHUD::CacheAthletes()
{
	if (Body[0].IsValid() && Body[1].IsValid()) return;	// until both are found (they may spawn after the first frame)
	Skin.SetNum(2);	// one material instance per athlete: the ripple (RipplePoint / RippleTime / RippleAmp), wear (FlushPoint / Flush / Tired)
	for (TActorIterator<ASlapAthleteVisual> It(GetWorld()); It; ++It)
	{
		const int32 a = It->BodyPrefix == TEXT("blue_") ? 1 : 0;
		if (Body[a].IsValid()) continue;
		Skin[a] = It->Mesh->CreateDynamicMaterialInstance(0);
		Body[a] = It->Mesh;
		Visual[a] = *It;
		USkeletalMesh* Mesh = Cast<USkeletalMesh>(It->Mesh->GetSkinnedAsset());
		bDentMorphs[a] = Mesh && Mesh->FindMorphTarget(DentMorph[0]) && Mesh->FindMorphTarget(DentMorph[1]);
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: %s athlete %s, cheek dent morphs %s"), a ? TEXT("blue") : TEXT("red"), Mesh ? *Mesh->GetName() : TEXT("-"), bDentMorphs[a] ? TEXT("yes") : TEXT("none (no-op)"));
	}
}
