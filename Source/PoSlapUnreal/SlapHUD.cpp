#include "SlapHUD.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
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
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "NNEModelData.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "MuJoCo/Components/Geometry/MjGeom.h"
#include "NiagaraFunctionLibrary.h"
#include "NiagaraSystem.h"
#include "SlapAthleteVisual.h"
#include "SlapController.h"
#include "SceneView.h"
#include "Sound/SoundAttenuation.h"
#include "Sound/SoundBase.h"
#include "Styling/CoreStyle.h"
#include "UObject/UObjectIterator.h"
#include "UnrealClient.h"
#include "Brushes/SlateColorBrush.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SSafeZone.h"
#include "Widgets/Layout/SUniformGridPanel.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

namespace
{
	constexpr double CleanSpeed = 8.0;	// m/s: a clean strike (Blueprint L2)
	constexpr double KOShot = 4.0;		// s of crane after a KO (USlapController::PostMatchHold)
	const FColor TeamColor[2] = { FColor(235, 70, 60), FColor(70, 130, 240) };
	const FColor Gold(255, 220, 120), Grey(200, 200, 200);
	constexpr float TopBar = 56.f, BottomBar = 40.f;	// Slate units: the corner anchors' rows (BuildUi)
	const TCHAR* ShotName[] = { TEXT("WIDE"), TEXT("TENSION"), TEXT("STRIKE"), TEXT("KO") };
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
	bMenu = FParse::Value(FCommandLine::Get(), TEXT("SlapMenu="), Tab);
	bUiTest = FParse::Param(FCommandLine::Get(), TEXT("SlapUiTest"));
	Tab = FMath::Clamp(Tab, 0, 3);
	BuildUi();
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
	FMargin Safe(0.f);
	if (FSlateApplication::IsInitialized()) FSlateApplication::Get().GetSafeZoneSize(Safe, View);	// pixels
	const float Dpi = GetDefault<UUserInterfaceSettings>()->GetDPIScaleBasedOnSize(FIntPoint(View.X, View.Y));
	const FIntRect R = Canvas->SceneView ? Canvas->SceneView->UnscaledViewRect : FIntRect(0, 0, View.X, View.Y);
	Top = FMath::Max(0.f, Safe.Top + TopBar * Dpi - FMath::Max<float>(R.Min.Y, Safe.Top));
	Bot = Canvas->ClipY - FMath::Max(0.f, Safe.Bottom + BottomBar * Dpi - FMath::Max<float>(View.Y - R.Max.Y, Safe.Bottom));

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
		if (bShowTelemetry) DrawTelemetry(H);
		DrawMatch(H);
		DrawOverlays(H);
	}

	if (bUiTest) UiTest(H);
	if (ShotAt >= 0.f && GetWorld()->GetTimeSeconds() >= ShotAt)
	{
		if (!bShotTaken)
		{
			bShotTaken = true;
			FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir() / TEXT("Screenshots/slapshot.png"), true, false);	// with the Slate layer
		}
		else if (GetWorld()->GetTimeSeconds() >= ShotAt + 1.f)	// a frame later the file is written
		{
			UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
		}
	}
}

void ASlapHUD::EndPlay(const EEndPlayReason::Type Reason)
{
	UGameViewportClient* VC = GetWorld()->GetGameViewport();
	if (VC && Ui.IsValid()) VC->RemoveViewportWidgetContent(Ui.ToSharedRef());
	Ui.Reset();
	Super::EndPlay(Reason);
}

void ASlapHUD::BuildUi()
{
	// Corners (inside the device safe zone): title, FPS, menu | debug, build stamp. The menu is a bottom sheet over a scrim
	// with three tabs; tapping the scrim or X closes it. Sizes are Slate units (1080-wide reference, DPI-scaled).
	UGameViewportClient* VC = GetWorld()->GetGameViewport();
	if (!VC) return;	// no viewport (commandlet, server)
	if (APlayerController* PC = GetOwningPlayerController())	// taps and clicks reach the buttons
	{
		PC->SetShowMouseCursor(true);
		PC->SetInputMode(FInputModeGameAndUI().SetHideCursorDuringCapture(false));
	}
	IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	AR.ScanPathsSynchronous({ TEXT("/Game/Brains") });
	AR.GetAssetsByPath(FName(TEXT("/Game/Brains")), Brains, true);
	Brains.RemoveAll([](const FAssetData& A) { return A.AssetClassPath != UNNEModelData::StaticClass()->GetClassPathName(); });
	Brains.Sort([](const FAssetData& A, const FAssetData& B) { return A.AssetName.LexicalLess(B.AssetName); });

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
	auto TabButton = [&](int32 i, const TCHAR* Name)
	{
		return SAssignNew(i == 0 ? Hit[HitTabMatch] : i == 2 ? Hit[HitTabView] : Spare, SButton).HAlign(HAlign_Center).OnClicked_Lambda([Self, i] { if (Self.IsValid()) Self->Tab = i; return FReply::Handled(); })
			[Label(FText::FromString(Name), 18, TAttribute<FSlateColor>::CreateLambda([Self, i, Hi, Dim] { return FSlateColor(Self.IsValid() && Self->Tab == i ? Hi : Dim); }))];
	};
	auto Toggle = [&](const TCHAR* Name, bool ASlapHUD::* Flag)
	{
		TSharedRef<SCheckBox> Box = SNew(SCheckBox)
			.IsChecked_Lambda([Self, Flag] { return Self.IsValid() && (*Self).*Flag ? ECheckBoxState::Checked : ECheckBoxState::Unchecked; })
			.OnCheckStateChanged_Lambda([Self, Flag](ECheckBoxState St) { if (Self.IsValid()) (*Self).*Flag = St == ECheckBoxState::Checked; })
			[Label(FText::FromString(Name), 18, White, true)];
		if (Flag == &ASlapHUD::bShowTelemetry) Hit[HitTelemetry] = Box;
		return Box;
	};
	auto MatchText = [Self]
	{
		const FSlapHud H = Self.IsValid() && Self->Controller.IsValid() ? Self->Controller->GetHud() : FSlapHud();
		if (H.Match == 0) return FText::FromString(TEXT("Waiting for the first match."));
		FString T = FString::Printf(TEXT("MATCH %d   ROUND %d / 3   %s strikes\n"), H.Match, H.Round, *H.Striker.ToUpper());
		for (int32 a = 0; a < 2; ++a) T += FString::Printf(TEXT("\n%s  %s\n%s  %s\n"), a ? TEXT("BLUE") : TEXT("RED"), *H.Name[a], *H.Tape[a], *H.Brain[a]);
		T += FString::Printf(TEXT("\nSERIES   RED %d - %d BLUE   (%d drawn)"), H.Wins[0], H.Wins[1], H.Draws);
		if (!H.Result.IsEmpty()) T += TEXT("\nLast result: ") + H.Result;
		return FText::FromString(T);
	};
	auto Fits = [Self](int32 a)	// brains made for corner a's character
	{
		TArray<int32> Out;
		if (!Self.IsValid() || !Self->Controller.IsValid()) return Out;
		const FString Card = Self->Controller->GetHud().Card[a] + TEXT("_");
		for (int32 i = 0; i < Self->Brains.Num(); ++i)
		{
			if (Self->Brains[i].AssetName.ToString().StartsWith(Card)) Out.Add(i);
		}
		return Out;
	};
	auto Step = [Self, Fits](int32 a, int32 Dir)
	{
		const TArray<int32> F = Fits(a);
		if (F.Num() == 0) return FReply::Handled();
		int32& P = Self->Pick[a];
		if (P == INDEX_NONE)	// from the brain the corner plays now
		{
			const FName Now(*Self->Controller->GetHud().Brain[a]);
			P = Self->Brains.IndexOfByPredicate([&](const FAssetData& B) { return B.AssetName == Now; });
		}
		const int32 k = F.Find(P);
		P = F[k == INDEX_NONE ? 0 : (k + Dir + F.Num()) % F.Num()];
		return FReply::Handled();
	};
	auto Corner = [&](int32 a)
	{
		return SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
			[SNew(SBox).WidthOverride(80.f)[Label(FText::FromString(a ? TEXT("BLUE") : TEXT("RED")), 18, FLinearColor(TeamColor[a]))]]
			+ SHorizontalBox::Slot().AutoWidth()[SNew(SButton).OnClicked_Lambda([Step, a] { return Step(a, -1); })[Label(FText::FromString(TEXT(" < ")), 18, White)]]
			+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center).HAlign(HAlign_Center)
			[Label(TAttribute<FText>::CreateLambda([Self, a]
			{
				if (!Self.IsValid()) return FText();
				const int32 P = Self->Pick[a];
				return FText::FromString(P != INDEX_NONE ? Self->Brains[P].AssetName.ToString() : Self->Controller.IsValid() ? Self->Controller->GetHud().Brain[a] : FString());
			}), 18, Hi)]
			+ SHorizontalBox::Slot().AutoWidth()
			[SAssignNew(a == 0 ? Hit[HitRedNext] : Spare, SButton).OnClicked_Lambda([Step, a] { return Step(a, 1); })[Label(FText::FromString(TEXT(" > ")), 18, White)]];
	};
	TSharedRef<SUniformGridPanel> Grid = SNew(SUniformGridPanel).SlotPadding(FMargin(2.f));	// the roster: 4 x 4 slots
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
		if (!Self.IsValid() || !Self->Controller.IsValid()) return FReply::Handled();
		UNNEModelData* B[2] = {};
		for (int32 a = 0; a < 2; ++a)
		{
			if (Self->Pick[a] != INDEX_NONE) B[a] = Cast<UNNEModelData>(Self->Brains[Self->Pick[a]].GetAsset());
		}
		Self->Controller->RequestMatch(B[0], B[1]);
		Self->Pick[0] = Self->Pick[1] = INDEX_NONE;	// the corners play them now
		return Menu(false);
	};

	Ui = SNew(SOverlay)
		+ SOverlay::Slot()
		[
			SNew(SSafeZone).IsTitleSafe(true)	// the same inset the engine gives the canvas
			[
				SNew(SOverlay)
				+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Top)
				[
					SNew(SBox).HeightOverride(TopBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
					[Label(FText::FromString(TEXT("POSLAP")), 22, White)]
				]
				+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Top)
				[
					SNew(SBox).HeightOverride(TopBar).VAlign(VAlign_Center)
					[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(FString::Printf(TEXT("%d FPS"), Self.IsValid() ? FMath::RoundToInt(Self->Fps) : 0)); }), 18,
						TAttribute<FSlateColor>::CreateLambda([Self] { const float F = Self.IsValid() ? Self->Fps : 0.f; return FSlateColor(F >= 55.f ? FLinearColor(0.35f, 0.9f, 0.45f) : F >= 30.f ? FLinearColor(1.f, 0.8f, 0.2f) : FLinearColor(1.f, 0.3f, 0.25f)); }))]
				]
				+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Top)
				[
					SNew(SBox).HeightOverride(TopBar).VAlign(VAlign_Center).Padding(FMargin(8.f, 4.f))
					[SAssignNew(Hit[HitMenu], SButton).OnClicked_Lambda([Self, Menu] { return Menu(Self.IsValid() && !Self->bMenu); })[Label(FText::FromString(TEXT("MENU")), 18, White)]]
				]
				+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Bottom)
				[
					SNew(SBox).HeightOverride(BottomBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
					.Visibility_Lambda([Self] { return Self.IsValid() && Self->bShowDebug ? EVisibility::HitTestInvisible : EVisibility::Collapsed; })
					[SNew(SBorder).BorderImage(&ScrimBrush).Padding(FMargin(6.f, 2.f))[Label(TAttribute<FText>::CreateLambda([Self]
					{
						if (!Self.IsValid()) return FText();
						const FSlapHud H = Self->Controller.IsValid() ? Self->Controller->GetHud() : FSlapHud();
						return FText::FromString(FString::Printf(TEXT("M%d R%d  CAM %s  worst %.0f ms"), H.Match, H.Round, ShotName[(int32)Self->Shot], Self->WorstMs));
					}), 14, Dim)]]
				]
				+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Bottom)
				[
					SNew(SBox).HeightOverride(BottomBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
					[SNew(SBorder).BorderImage(&ScrimBrush).Padding(FMargin(6.f, 2.f))[Label(FText::FromString(TEXT(POSLAP_BUILD_VERSION)), 14, FLinearColor(1.f, 1.f, 1.f, 0.8f))]]
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
			[
				SNew(SBorder).BorderImage(&SheetBrush).Padding(FMargin(16.f, 16.f, 16.f, 8.f))
				[
					SNew(SSafeZone).IsTitleSafe(true).PadTop(false)	// the sheet's own background runs to the screen edge
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(0, TEXT("MATCH"))]
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(1, TEXT("ROSTER"))]
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(2, TEXT("VIEW"))]
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f)[TabButton(3, TEXT("ABOUT"))]
							+ SHorizontalBox::Slot().AutoWidth().Padding(2.f)[SAssignNew(Hit[HitClose], SButton).OnClicked_Lambda(Close)[Label(FText::FromString(TEXT(" X ")), 18, White)]]
						]
						+ SVerticalBox::Slot().AutoHeight().Padding(FMargin(8.f, 20.f, 8.f, 12.f))
						[
							SNew(SWidgetSwitcher).WidgetIndex_Lambda([Self] { return Self.IsValid() ? Self->Tab : 0; })
							+ SWidgetSwitcher::Slot()
							[
								SNew(SVerticalBox)
								+ SVerticalBox::Slot().AutoHeight()[Label(TAttribute<FText>::CreateLambda(MatchText), 16, White, true)]
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 14.f, 0.f, 4.f)[Corner(0)]
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f)[Corner(1)]
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 10.f, 0.f, 0.f)
								[SAssignNew(Hit[HitStart], SButton).HAlign(HAlign_Center).OnClicked_Lambda(Start)[Label(FText::FromString(TEXT("START NEW MATCH")), 18, White)]]
							]
							+ SWidgetSwitcher::Slot()
							[
								SNew(SVerticalBox)
								+ SVerticalBox::Slot().AutoHeight()[Grid]
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 12.f, 0.f, 0.f)
								[Label(TAttribute<FText>::CreateLambda([Self] { return Self.IsValid() ? FText::FromString(Self->Slots[Self->RosterSel].Value) : FText(); }), 15, White, true)]
							]
							+ SWidgetSwitcher::Slot()
							[
								SNew(SVerticalBox)
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f)[Toggle(TEXT("Telemetry (momentum, balance, palm, head g, condition)"), &ASlapHUD::bShowTelemetry)]
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f)[Toggle(TEXT("Commentary"), &ASlapHUD::bShowCommentary)]
								+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f)[Toggle(TEXT("Debug line"), &ASlapHUD::bShowDebug)]
							]
							+ SWidgetSwitcher::Slot()
							[Label(FText::FromString(FString(TEXT("Two AI athletes trade open-palm slaps across a podium. MuJoCo runs the physics; Unreal only draws it.\n\n"
								"MOMENTUM  who is beating their own brain's expectation right now\n"
								"RINGS  centre of mass over the soles: green < 5 cm off, red > 10\n"
								"PALM  hand speed; the white line is a clean 8 m/s strike\n"
								"HEAD G  the defender's head acceleration, last 2 s\n\n"
								"Build ")) + TEXT(POSLAP_BUILD_VERSION)), 16, Dim, true)]
						]
					]
				]
			]
		];
	VC->AddViewportWidgetContent(Ui.ToSharedRef(), 10);
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
	for (const FString& F : Files)
	{
		FString Raw;
		TSharedPtr<FJsonObject> C;
		if (F.EndsWith(TEXT("_skeleton.json")) || !FFileHelper::LoadFileToString(Raw, *(Dir / F)) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Raw), C) || !C) continue;
		const FString Name = C->GetStringField(TEXT("name"));
		FString Mine;
		for (const FAssetData& B : Brains)
		{
			if (B.AssetName.ToString().StartsWith(Name + TEXT("_"))) Mine += (Mine.IsEmpty() ? TEXT("") : TEXT(", ")) + B.AssetName.ToString().RightChop(Name.Len() + 1);
		}
		const TSharedPtr<FJsonObject>* Style = nullptr;
		const FString Summary = C->TryGetObjectField(TEXT("style"), Style) ? (*Style)->GetStringField(TEXT("summary")) : FString();
		int32 Slot = INDEX_NONE;
		if (C->TryGetNumberField(TEXT("slot"), Slot)) Slot -= 1;
		if (!Slots.IsValidIndex(Slot) || !Slots[Slot].Key.IsEmpty()) Slot = Slots.IndexOfByPredicate([](const TPair<FString, FString>& X) { return X.Key.IsEmpty(); });
		if (Slot == INDEX_NONE) break;	// more cards than slots
		Slots[Slot].Key = C->GetStringField(TEXT("display"));
		Slots[Slot].Value = FString::Printf(TEXT("%s   %.2f m  %.0f kg\nSTR %.2f  SPD %.2f  FLX %.2f  TGH %.2f\n%s\nBrains: %s"),
			*Slots[Slot].Key, C->GetNumberField(TEXT("height_m")), C->GetNumberField(TEXT("mass_kg")), C->GetNumberField(TEXT("strength")),
			C->GetNumberField(TEXT("speed")), C->GetNumberField(TEXT("flexibility")), C->GetNumberField(TEXT("toughness")), *Summary,
			Mine.IsEmpty() ? TEXT("none yet (in training)") : *Mine);
	}
	for (int32 i = 0; i < RosterSize; ++i)
	{
		if (!Slots[i].Key.IsEmpty()) continue;
		Slots[i].Value = FString::Printf(TEXT("Slot %d is open. A new athlete: a rigged FBX -> Scripts/rig_mixamo.py -> card training/characters/<name>.json with \"slot\": %d and its own stats and style -> build_body.py -> its own ladder (overnight.py --char <name> --queue) -> a pairing level (build_scene.py SLAP_RED / SLAP_BLUE)."), i + 1, i + 1);
	}
}

void ASlapHUD::UiTest(const FSlapHud& H)
{
	// -SlapUiTest: synthetic Slate clicks at the real widgets (hit-testing included, the OS cursor untouched), one every
	// 1.5 s from 3 s: menu, VIEW, telemetry toggle, scrim, menu, MATCH, red's next brain, START (a new match with that
	// brain), menu, X; each followed by its expected state. Then waits for a match to end and the next to start
	// (Gameplay -> Win/Loss -> Loop Reset). Logs PASS or FAIL.
	struct FStep { int32 Target; bool bMenuAfter; int32 TabAfter; bool bTelemetryAfter; };
	static const FStep Steps[] = { { HitMenu, true, 0, true }, { HitTabView, true, 2, true }, { HitTelemetry, true, 2, false },
		{ HitScrim, false, 2, false }, { HitMenu, true, 2, false }, { HitTabMatch, true, 0, false }, { HitRedNext, true, 0, false },
		{ HitStart, false, 0, false }, { HitMenu, true, 0, false }, { HitClose, false, 0, false } };
	const double Now = GetWorld()->GetTimeSeconds();
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
			UiFails += !bOk;
			UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test click %d (target %d): menu %d tab %d telemetry %d%s - %s"), UiStep, Was.Target, bMenu, Tab, bShowTelemetry, *Extra, bOk ? TEXT("ok") : TEXT("WRONG"));
		}
	}
	if (UiStep < UE_ARRAY_COUNT(Steps) && Now >= 3.0 + 1.5 * UiStep)
	{
		const TSharedPtr<SWidget> W = Hit[Steps[UiStep].Target];
		if (Steps[UiStep].Target == HitStart)
		{
			UiSeq = H.MatchSeq;
			UiBrain = Pick[0] != INDEX_NONE ? Brains[Pick[0]].AssetName.ToString() : H.Brain[0];
		}
		FSlateApplication& App = FSlateApplication::Get();
		const FVector2D P = W.IsValid() ? FVector2D(W->GetTickSpaceGeometry().GetAbsolutePositionAtCoordinates(FVector2f(0.5f, Steps[UiStep].Target == HitScrim ? 0.2f : 0.5f))) : FVector2D::ZeroVector;
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
		++UiStep;
		UiAt = Now;
		bUiChecked = false;
	}
	if (UiStep == UE_ARRAY_COUNT(Steps) && bUiChecked && !bUiDone && UiMatch0 > 0 && H.Wins[0] + H.Wins[1] + H.Draws >= UiMatch0 && H.Match > 0 && H.Result.IsEmpty())	// a match ended, the next began
	{
		bUiDone = true;
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test %s: %d of %d clicks wrong; a match ended (series %d-%d, %d drawn) and match %d began"), UiFails ? TEXT("FAIL") : TEXT("PASS"),
			UiFails, (int32)UE_ARRAY_COUNT(Steps), H.Wins[0], H.Wins[1], H.Draws, H.Match);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);	// short: training shares this machine
	}
	else if (!bUiDone && Now > 120.0)
	{
		bUiDone = true;
		UE_LOG(LogTemp, Log, TEXT("SlapHUD: UI test FAIL: %d of %d clicks wrong, no match ended within 120 s"), UiFails, (int32)UE_ARRAY_COUNT(Steps));
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	}
}

void ASlapHUD::Text(const FString& Str, float X, float Y, FColor C, float Scale, bool bCentre, UFont* Font)
{
	Font = Font ? Font : GEngine->GetSmallFont();
	float W = 0, Ht = 0;
	Canvas->StrLen(Font, Str, W, Ht);
	Canvas->SetDrawColor(C);
	Canvas->DrawText(Font, Str, bCentre ? X - W * Scale * 0.5f : X, Y, Scale, Scale);
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
	auto Set = [](FStageLight& Lit, double Gain) { if (ULightComponent* L = Lit.Light.Get()) L->SetIntensity(Lit.Base * Gain); };
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
	const float X = 16.f * S;
	float Y = Top + 8.f * S;
	auto Line = [&](UFont* F, const FString& Str, FColor C, float Scale = 1.f)
	{
		Text(Str, X, Y, C, S * Scale, false, F);
		float W, Ht;
		Canvas->StrLen(F, Str, W, Ht);
		Y += Ht * S * Scale + 4.f * S;
	};
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 8.f * S, Top, 300.f * S, 250.f * S);	// legible over the truss
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
	const float MX = 8.f * S, MY = Top + 256.f * S, MW = 524.f * S, MH = 16.f * S;	// under the panel
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
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), GX - 4.f * S, GY - 26.f * S, GW + 8.f * S, GH + 52.f * S);
	DrawRect(FLinearColor(TeamColor[Sk]), GX, GY + GH * (1.f - FMath::Clamp(V / Max, 0.f, 1.f)), GW, GH * FMath::Clamp(V / Max, 0.f, 1.f));
	const float Y8 = GY + GH * (1.f - CleanSpeed / Max);
	DrawLine(GX - 4.f * S, Y8, GX + GW + 4.f * S, Y8, FLinearColor::White, 2.f * S);
	Text(TEXT("PALM"), GX + GW * 0.5f, GY - 22.f * S, Grey, S, true);
	Text(FString::Printf(TEXT("%.1f"), V), GX + GW * 0.5f, GY + GH + 6.f * S, H.bStruck && H.Speed >= CleanSpeed ? Gold : FColor::White, S, true);

	// the defender's head acceleration, last 2 s
	const float BX = 16.f * S, BY = Bot - 84.f * S, BW = 230.f * S, BH = 70.f * S;
	float Peak = 0.f;
	for (float G : HeadG) Peak = FMath::Max(Peak, G);
	const float Scale = FMath::Max(20.f, Peak);
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), BX - 6.f * S, BY - 22.f * S, BW + 12.f * S, BH + 28.f * S);
	Text(FString::Printf(TEXT("HEAD G  %s  peak %.0f g"), *H.Defender.ToUpper(), Peak), BX, BY - 20.f * S, Grey, S);
	for (int32 i = 1; i < HeadG.Num(); ++i)
	{
		const float X0 = BX + BW * (i - 1) / 119.f, X1 = BX + BW * i / 119.f;
		DrawLine(X0, BY + BH * (1.f - HeadG[i - 1] / Scale), X1, BY + BH * (1.f - HeadG[i] / Scale), FLinearColor(Gold), 2.f * S);
	}

	// fatigue: stamina (joint speed) and neck strength, both from 1 fresh down to their floors
	const float FX = 290.f * S, FY = Bot - 84.f * S;
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
	const float Cx = Canvas->ClipX * 0.5f;
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
		const float Y = Top + 278.f * S;
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
		const float W = 200.f * S, X = Canvas->ClipX - W * (In - Out) - 8.f * S, Y = Top;	// top right, beside the panel, under the menu button
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
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.7f), 0, Top + 278.f * S, Canvas->ClipX, 70.f * S);	// below the panel and the momentum bar
		Text(KO ? TEXT("KNOCKOUT") : TEXT("DECISION"), Cx, Top + 284.f * S, KO ? FColor(250, 80, 60) : Gold, 1.6f * S, true, Big);
		Text(H.Result, Cx, Top + 320.f * S, FColor::White, 1.2f * S, true);
	}

	// commentary, bottom: the latest line for 8 s, wrapped at ~44 characters
	if (bShowCommentary && Now - CommentaryAt < 8.0 && CommentaryAt >= TurnAt && !Commentary.IsEmpty())
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
		const float Y = Bot - 168.f * S - Lines.Num() * 20.f * S;	// above the head-g and condition panels
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), 16.f * S, Y - 6.f * S, 508.f * S, (Lines.Num() * 20.f + 10.f) * S);
		for (int32 i = 0; i < Lines.Num(); ++i) Text(Lines[i], Cx, Y + i * 20.f * S, FColor::White, 1.1f * S, true);
	}
}
