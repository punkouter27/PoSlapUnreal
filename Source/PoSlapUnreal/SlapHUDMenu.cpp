// ASlapHUD's Slate layer: the corner anchors, the result card and the menu screen (MATCH / BRAINS / VIEW / TOURNEY,
// START at its bottom centre). The menu is its own screen over an opaque backdrop between the two anchor rows: the
// arena is never seen behind it (user 2026-11-09: the menu first, the scene apart from it). Sizes are Slate units
// (DPI-scaled); every touch target is at least 44 units; nothing scrolls.

#include "SlapHUD.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Brushes/SlateColorBrush.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Engine/GameViewportClient.h"
#include "Framework/Application/SlateApplication.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "NNEModelData.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SlapAthleteVisual.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScaleBox.h"
#include "Widgets/Layout/SUniformGridPanel.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

using namespace SlapUi;

namespace
{
	const FLinearColor White = FLinearColor::White, Dim(Grey), Hi(Gold);
	const FLinearColor RedTeam(0.85f, 0.22f, 0.18f), BlueTeam(0.2f, 0.45f, 0.9f), Panel(0.18f, 0.18f, 0.22f);
	const FSlateRoundedBoxBrush& SheetBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(0.05f, 0.05f, 0.07f, 1.f), 12.f); return B; }	// the menu screen's panel
	const FSlateColorBrush& BackdropBrush() { static const FSlateColorBrush B(FLinearColor(0.02f, 0.02f, 0.03f, 1.f)); return B; }	// under the menu: the arena is not seen
	const FSlateColorBrush& ScrimBrush() { static const FSlateColorBrush B(FLinearColor(0.f, 0.f, 0.f, 0.5f)); return B; }
	const FSlateRoundedBoxBrush& TabOnBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(0.16f, 0.16f, 0.2f, 1.f), FVector4(8.f, 8.f, 0.f, 0.f)); return B; }
	const FSlateRoundedBoxBrush& TabOffBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(0.09f, 0.09f, 0.11f, 1.f), FVector4(8.f, 8.f, 0.f, 0.f)); return B; }
	const FSlateRoundedBoxBrush& ByeBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(1.f, 1.f, 1.f, 0.04f), 10.f); return B; }
	const FSlateRoundedBoxBrush& OnBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(0.55f, 0.42f, 0.1f, 0.9f), 10.f); return B; }
	const FSlateRoundedBoxBrush& OffBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(1.f, 1.f, 1.f, 0.08f), 10.f); return B; }
	const FSlateRoundedBoxBrush& CardBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(0.f, 0.f, 0.f, 0.8f), 14.f); return B; }
	const FSlateRoundedBoxBrush& GripBrush() { static const FSlateRoundedBoxBrush B(FLinearColor(0.f, 0.f, 0.f, 0.45f), 12.f); return B; }
	const FSlateColorBrush& BarBrush() { static const FSlateColorBrush B(FLinearColor::White); return B; }

	/** A line of text, never smaller than MinSlateFont. */
	TSharedRef<STextBlock> Label(const TAttribute<FText>& T, int32 Size, const TAttribute<FSlateColor>& C, bool bWrap = false)
	{
		return SNew(STextBlock).Text(T).Font(FCoreStyle::GetDefaultFontStyle("Bold", FMath::Max(Size, MinSlateFont))).ColorAndOpacity(C).ShadowOffset(FVector2D(1.0, 1.0)).AutoWrapText(bWrap);
	}

	/** A label that shrinks (never grows) to fit its slot: a long name stays on one line. */
	TSharedRef<SWidget> FitLabel(const TAttribute<FText>& T, int32 Size, const TAttribute<FSlateColor>& C)
	{
		return SNew(SScaleBox).Stretch(EStretch::ScaleToFit).StretchDirection(EStretchDirection::DownOnly)[Label(T, Size, C)];
	}

	/** A thumb-sized button; Out (if any) receives it for the checks, Targets the box for the touch audit. */
	TSharedRef<SWidget> Button(const TAttribute<FText>& T, int32 Size, TFunction<FReply()> Click, TArray<TSharedPtr<SWidget>>& Targets,
		TSharedPtr<SWidget>* Out = nullptr, TAttribute<FSlateColor> TextColour = White, TAttribute<FLinearColor> Colour = FLinearColor::White, float Width = 0.f)
	{
		TSharedPtr<SButton> B;
		TSharedRef<SBox> Box = SNew(SBox).MinDesiredHeight(Touch).MinDesiredWidth(FMath::Max(Width, Touch))
			[SAssignNew(B, SButton).ClickMethod(EButtonClickMethod::MouseDown).TouchMethod(EButtonTouchMethod::Down).PressMethod(EButtonPressMethod::DownAndUp)
					.ButtonColorAndOpacity(TAttribute<FSlateColor>::CreateLambda([Colour] { return FSlateColor(Colour.Get()); })).HAlign(HAlign_Center).VAlign(VAlign_Center).OnClicked_Lambda(MoveTemp(Click))[FitLabel(T, Size, TextColour)]];
		if (Out) *Out = B;
		Targets.Add(Box);
		return Box;
	}

	/** A pill that is gold when on (a view toggle, a camera or quality choice). */
	TSharedRef<SWidget> Pill(const FString& Code, TFunction<bool()> IsOn, TFunction<void()> Click, TArray<TSharedPtr<SWidget>>& Targets, TSharedPtr<SWidget>* Out = nullptr)
	{
		TSharedPtr<SButton> B;
		TSharedRef<SBox> Box = SNew(SBox).MinDesiredHeight(Touch).MinDesiredWidth(Touch)
			[SAssignNew(B, SButton).ButtonStyle(&FCoreStyle::Get().GetWidgetStyle<FButtonStyle>("NoBorder")).ContentPadding(0.f).ClickMethod(EButtonClickMethod::MouseDown).TouchMethod(EButtonTouchMethod::Down)
				.OnClicked_Lambda([Click] { Click(); return FReply::Handled(); })
				[SNew(SBorder).Padding(FMargin(4.f, 0.f)).HAlign(HAlign_Center).VAlign(VAlign_Center).BorderImage_Lambda([IsOn] { return IsOn() ? &OnBrush() : &OffBrush(); })
					[FitLabel(FText::FromString(Code), 15, TAttribute<FSlateColor>::CreateLambda([IsOn] { return FSlateColor(IsOn() ? White : Dim); }))]]];
		if (Out) *Out = B;
		Targets.Add(Box);
		return Box;
	}

	/** A row of equal pills. */
	TSharedRef<SHorizontalBox> PillRow(TArray<TSharedRef<SWidget>> Pills)
	{
		TSharedRef<SHorizontalBox> Row = SNew(SHorizontalBox);
		for (int32 i = 0; i < Pills.Num(); ++i) Row->AddSlot().FillWidth(1.f).Padding(i ? 3.f : 0.f, 0.f, 0.f, 0.f)[Pills[i]];
		return Row;
	}

	TSharedRef<SWidget> Heading(const TCHAR* T) { return Label(FText::FromString(T), 13, Hi); }
}

void ASlapHUD::OpenMenu(bool bOpen)
{
	if (bMenu != bOpen) UE_LOG(LogSlap, Log, TEXT("SlapHUD: menu %s"), bOpen ? TEXT("open") : TEXT("closed"));
	if (bOpen && !bMenu && GetWorld())
	{
		MenuAt = GetWorld()->GetTimeSeconds();
		if (Last.Match == 0 || !Last.Result.IsEmpty())	// over a finished match: no replay or KO crane left running behind the sheet
		{
			for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
				if (V.IsValid()) V->Replay(0.0, 0.0, 0.0);
			ReplayAt = -1.0;
			ReplayEnd = FMath::Min(ReplayEnd, MenuAt);
			bAutoReplay = false;
		}
	}
	bMenu = bOpen;
}

void ASlapHUD::FindBrainsAndLevels()
{
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
	Brains.RemoveAll([](const FAssetData& B) { return B.AssetName.ToString().EndsWith(TEXT("_S2")); });	// walking brains: the walk-in's, not a match brain
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
	// The phone arena (Scripts/arena_mobile_level.py: Arena_Mobile.glb, one palette atlas, a lighter crowd) is the same
	// level under "<level>_Mobile": a phone lists only those, a desktop only the full ones. A phone build without them
	// keeps the full arena.
	// 2026-11-04 phone test: the Arena_Mobile twins drew wrong on the Pixel (dark props at the camera, card crowd), so a
	// phone plays the full arena too; -SlapMobileArena opts back into the twins while they are fixed.
	const bool bPhone = (PLATFORM_ANDROID || PLATFORM_IOS) && FParse::Param(FCommandLine::Get(), TEXT("SlapMobileArena"));
	const bool bAnyMobile = Pairings.ContainsByPredicate([](const FAssetData& A) { return IsMobileLevel(A.AssetName.ToString()); });
	Pairings.RemoveAll([&](const FAssetData& A) { return IsMobileLevel(A.AssetName.ToString()) != (bPhone && bAnyMobile); });
	Pairings.Sort([](const FAssetData& A, const FAssetData& B) { return A.AssetName.LexicalLess(B.AssetName); });
	const FName Here(*UGameplayStatics::GetCurrentLevelName(this));	// PIE prefix stripped
	Pairing = HerePairing = Pairings.IndexOfByPredicate([&](const FAssetData& A) { return A.AssetName == Here; });
}

void ASlapHUD::BuildUi()
{
	Targets.Reset();
	UGameViewportClient* VC = GetWorld()->GetGameViewport();
	if (!VC) return;	// no viewport (commandlet, server)
	if (APlayerController* PC = GetOwningPlayerController())	// taps and clicks reach the buttons
	{
		PC->SetShowMouseCursor(true);
		PC->SetInputMode(FInputModeGameAndUI().SetHideCursorDuringCapture(false));
	}
	FindBrainsAndLevels();
	LoadRoster();
	{	// the picks open on the pairing that is loaded ("L_Arena" is build_scene.py's grandpa vs grandpa)
		const FString N = DesktopLevel(UGameplayStatics::GetCurrentLevelName(this));
		FString R = TEXT("grandpa"), B = TEXT("grandpa");
		if (N.StartsWith(TEXT("L_Arena_"))) N.RightChop(8).Split(TEXT("_vs_"), &R, &B);
		for (int32 a = 0; a < 2; ++a)
		{
			const int32 Slot = SlotCards.Find((a ? B : R).ToLower());
			if (Slot != INDEX_NONE) SelectedFighter[a] = Slot;
			HereCard[a] = (a ? B : R).ToLower();
		}
	}
	LoadRatings();
	LoadTourney();	// after the roster and the ratings: a fresh bracket is seeded from both

	TWeakObjectPtr<ASlapHUD> Self(this);
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
				else if (Self->bAutoReplay) Self->SkipReplay();	// a tap during a strike replay: back to live
				return FReply::Handled();
			})
			.OnMouseDoubleClick_Lambda([Self](const FGeometry&, const FPointerEvent&)
			{
				if (Self.IsValid() && !Self->bTravelling && Self->Last.Match > 0) Self->InstantReplay();
				return FReply::Handled();
			})
		]
		+ SOverlay::Slot()	// the menu's backdrop, under the anchors: the whole viewport, opaque, so the scene stays its own thing
		[SAssignNew(Hit[HitScrim], SBorder).BorderImage(&BackdropBrush()).Visibility_Lambda([Self] { return Self.IsValid() && Self->bMenu ? EVisibility::Visible : EVisibility::Collapsed; })]
		+ SOverlay::Slot()[BuildAnchors()]
		+ SOverlay::Slot()[BuildSheet()];
	VC->AddViewportWidgetContent(Ui.ToSharedRef(), 10);
	if (SheetBody.IsValid()) SheetBody->SetClipping(EWidgetClipping::ClipToBounds);	// a tab too tall for the budget is cut, never spills over START
}

TSharedRef<SWidget> ASlapHUD::BuildAnchors()
{
	// Corners, inside the device safe zone: TL title, TC FPS + step counter, TR menu, BL live telemetry (+ debug), BR build
	// stamp; BC a grip that opens the menu under the thumb (TR is a stretch one-handed), and the result card above it.
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto Burger = []	// the menu icon: three bars (no icon font needed)
	{
		TSharedRef<SVerticalBox> V = SNew(SVerticalBox);
		for (int32 k = 0; k < 3; ++k) V->AddSlot().AutoHeight().Padding(0.f, 2.5f)[SNew(SBox).WidthOverride(22.f).HeightOverride(3.f)[SNew(SImage).Image(&BarBrush())]];
		return V;
	};
	TSharedPtr<SBox> GripBox;
	TSharedRef<SWidget> Out = SNew(SBox)
		// the safe insets DrawHUD reads each frame (pixels -> Slate units): the corners and the canvas follow one source
		.Padding(TAttribute<FMargin>::CreateLambda([Self] { return Self.IsValid() ? Self->Safe * (1.f / FMath::Max(Self->DpiScale, 0.01f)) : FMargin(0.f); }))
		[
			SNew(SOverlay)
			+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Top)
			[SAssignNew(Anchor[0], SBox).HeightOverride(TopBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))[Label(FText::FromString(TEXT("POSLAP")), 22, White)]]
			+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Top)	// TC: frame rate and the MuJoCo step counter, always on
			[
				SAssignNew(Anchor[1], SBox).HeightOverride(TopBar).VAlign(VAlign_Center)
				[
					SNew(SBorder).BorderImage(&ScrimBrush()).Padding(FMargin(8.f, 2.f))
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
			+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Top)	// TR: the settings (the menu), an icon at thumb size
			[
				SAssignNew(Anchor[2], SBox).HeightOverride(TopBar).VAlign(VAlign_Center).Padding(FMargin(8.f, 0.f))
				[
					SNew(SBox).WidthOverride(Touch).HeightOverride(Touch)
					[SAssignNew(Hit[HitMenu], SButton).ClickMethod(EButtonClickMethod::MouseDown).TouchMethod(EButtonTouchMethod::Down).HAlign(HAlign_Center).VAlign(VAlign_Center).ToolTipText(FText::FromString(TEXT("Menu")))
						.OnClicked_Lambda([Self] { if (Self.IsValid()) Self->OpenMenu(!Self->bMenu); return FReply::Handled(); })[Burger()]]
				]
			]
			+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Bottom)	// BL: the live telemetry (with DBG: the physics clock too)
			[
				SAssignNew(Anchor[3], SBox).HeightOverride(BottomBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
				.Visibility_Lambda([Self] { return Self.IsValid() && !Self->bMenu && (Self->Last.Match > 0 || Self->bShowDebug) ? EVisibility::HitTestInvisible : EVisibility::Collapsed; })	// live numbers: not on the menu screen
				[SNew(SBorder).BorderImage(&ScrimBrush()).Padding(FMargin(6.f, 2.f))[Label(TAttribute<FText>::CreateLambda([Self]
				{
					if (!Self.IsValid()) return FText();
					const FSlapHud& H = Self->Last;
					float G = (float)H.HeadG;	// the head's peak over the last ~2 s: the live value is back near 0 a moment after a slap
					for (const float Sample : Self->HeadG) G = FMath::Max(G, Sample);
					FString T = FString::Printf(TEXT("PALM %.1f m/s  HEAD %.0f g"), H.bStruck ? H.Speed : H.HandSpeed, G);
					if (Self->bShowDebug) T += FString::Printf(TEXT("\nSIM %.1fs dt %.0fms x%.2f %s %s T%d %.0fms"), H.Steps * H.Timestep, H.Timestep * 1000.0, H.SlowMo, ShotName[(int32)Self->Shot], CamName[(int32)Self->CamMode], Self->TierLive, Self->WorstMs);
					return FText::FromString(T);
				}), 13, Dim)]]
			]
			+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Bottom)	// BR: the build stamp (YYMMDD)
			[
				SAssignNew(Anchor[4], SBox).HeightOverride(BottomBar).VAlign(VAlign_Center).Padding(FMargin(16.f, 0.f))
				[SNew(SBorder).BorderImage(&ScrimBrush()).Padding(FMargin(6.f, 2.f))[Label(FText::FromString(TEXT(POSLAP_BUILD_VERSION)), 14, FLinearColor(1.f, 1.f, 1.f, 0.8f))]]
			]
			+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Bottom)	// BC: the grip
			[
				SAssignNew(GripBox, SBox).WidthOverride(120.f).HeightOverride(Touch)
				.Visibility_Lambda([Self] { return Self.IsValid() && Self->bMenu ? EVisibility::Hidden : EVisibility::Visible; })
				[
					SAssignNew(Hit[HitGrip], SButton).ButtonStyle(&FCoreStyle::Get().GetWidgetStyle<FButtonStyle>("NoBorder")).ClickMethod(EButtonClickMethod::MouseDown).TouchMethod(EButtonTouchMethod::Down).HAlign(HAlign_Center).VAlign(VAlign_Center)
					.ToolTipText(FText::FromString(TEXT("Menu"))).OnClicked_Lambda([Self] { if (Self.IsValid()) Self->OpenMenu(!Self->bMenu); return FReply::Handled(); })
					[SNew(SBorder).BorderImage(&GripBrush()).Padding(FMargin(18.f, 8.f))[SNew(SBox).WidthOverride(44.f).HeightOverride(4.f)[SNew(SImage).Image(&BarBrush())]]]
				]
			]
			+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Bottom).Padding(FMargin(24.f, 0.f, 24.f, BottomBar + 120.f))	// Win/Loss: the lower third
			[BuildResultCard()]
		];
	Targets.Add(GripBox);
	return Out;
}

TSharedRef<SWidget> ASlapHUD::BuildResultCard()
{
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto bKO = [Self] { return Self.IsValid() && Self->Last.KOd >= 0; };
	return SNew(SBorder).BorderImage(&CardBrush()).Padding(FMargin(22.f, 14.f))
		.Visibility_Lambda([Self] { return Self.IsValid() && !Self->bMenu && !Self->Last.Result.IsEmpty() ? EVisibility::Visible : EVisibility::Collapsed; })
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center)
			[Label(TAttribute<FText>::CreateLambda([bKO] { return FText::FromString(bKO() ? TEXT("KNOCKOUT") : TEXT("DECISION")); }), 30,
				TAttribute<FSlateColor>::CreateLambda([bKO] { return FSlateColor(bKO() ? FLinearColor(FColor(250, 80, 60)) : Hi); }))]
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.f, 2.f)
			[FitLabel(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(Self.IsValid() ? Self->ResultPlain(Self->Last) : FString()); }), 18, White)]
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.f, 4.f, 0.f, 6.f)
			[Label(TAttribute<FText>::CreateLambda([Self]
			{
				if (!Self.IsValid()) return FText();
				const FSlapHud& H = Self->Last;
				return FText::FromString(FString::Printf(TEXT("JUDGES %d:%d  %d:%d  %d:%d     SERIES %d-%d%s"), H.Cards[0][0], H.Cards[0][1], H.Cards[1][0], H.Cards[1][1], H.Cards[2][0], H.Cards[2][1],
					H.Wins[0], H.Wins[1], H.Draws ? *FString::Printf(TEXT(" (%d D)"), H.Draws) : TEXT("")));
			}), 15, Dim)]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 0.f, 0.f, 8.f)[BuildResultStats()]
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0.f, 0.f, 0.f, 6.f)	// the summary: turning point, fastest slap, landed
			[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(Self.IsValid() ? Self->SummaryText() : FString()); }), 13, White)]
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.f, 0.f, 0.f, 10.f)
			[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(Self.IsValid() ? Self->EloLine : FString()); }), 14, Hi)]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().FillWidth(1.f).Padding(FMargin(0.f, 0.f, 4.f, 0.f))
				[Button(FText::FromString(TEXT("REMATCH")), 18, [Self]	// the same two again, now
				{
					if (!Self.IsValid() || Self->bTravelling || !Self->Controller.IsValid()) return FReply::Handled();
					Self->Controller->RequestMatch(nullptr, nullptr);
					UE_LOG(LogSlap, Log, TEXT("SlapHUD: rematch from the result card"));
					return FReply::Handled();
				}, Targets, &Hit[HitRematch], White, RedTeam)]
				+ SHorizontalBox::Slot().FillWidth(1.f).Padding(FMargin(4.f, 0.f, 0.f, 0.f))
				[Button(FText::FromString(TEXT("OK")), 18, [Self]	// the result stands until OK: then the menu (user 2026-11-09)
				{
					if (!Self.IsValid()) return FReply::Handled();
					Self->OpenMenu(true);
					Self->Tab = Self->bTourney || Self->TourneyPend[0] >= 0 ? TabTourney : TabMatch;
					Self->ActiveCornerSelect = 0;
					Self->ResultAt = -1.0;
					if (Self->Controller.IsValid()) Self->Controller->bRestand = true;	// both back on their feet behind the menu
					return FReply::Handled();
}, Targets, &Hit[HitResultMenu], White, FLinearColor(0.15f, 0.55f, 0.25f))]
			]
		];
}

TSharedRef<SWidget> ASlapHUD::BuildResultStats()
{
	// the match's numbers on the result card: red | what | blue, one row each
	TWeakObjectPtr<ASlapHUD> Self(this);
	TSharedRef<SVerticalBox> Rows = SNew(SVerticalBox);
	auto Row = [&](const TCHAR* What, TFunction<FString(int32)> Value)
	{
		auto Side = [Self, Value](int32 a) { return TAttribute<FText>::CreateLambda([Self, Value, a] { return FText::FromString(Self.IsValid() ? Value(a) : FString()); }); };
		Rows->AddSlot().AutoHeight()
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(1.f).HAlign(HAlign_Right)[FitLabel(Side(0), 15, RedTeam)]
			+ SHorizontalBox::Slot().FillWidth(1.3f).HAlign(HAlign_Center)[Label(FText::FromString(What), 13, Dim)]
			+ SHorizontalBox::Slot().FillWidth(1.f).HAlign(HAlign_Left)[FitLabel(Side(1), 15, BlueTeam)]
		];
	};
	Row(TEXT(""), [Self](int32 a) { return Self->HereCard[a].IsEmpty() ? FString(a ? TEXT("BLUE") : TEXT("RED")) : Self->HereCard[a].ToUpper(); });
	Row(TEXT("STRIKES"), [Self](int32 a) { return FString::FromInt(Self->TotalStrikes[a]); });
	Row(TEXT("FASTEST m/s"), [Self](int32 a) { return FString::Printf(TEXT("%.1f"), Self->PeakSpeed[a]); });
	Row(TEXT("KNOCKDOWNS"), [Self](int32 a) { return FString::FromInt(Self->Last.Downs[a]); });
	Row(TEXT("FLINCHES"), [Self](int32 a) { return FString::FromInt(Self->Flinches[a]); });
	Row(TEXT("STAMINA"), [Self](int32 a) { return FString::Printf(TEXT("%.0f%%"), 100.0 * StaminaLeft(Self->Last.Stamina[a])); });
	Row(TEXT("ELO"), [Self](int32 a) { return FString::Printf(TEXT("%.0f"), Self->EloNum(Self->HereCard[a])); });
	return SNew(SBox).WidthOverride(330.f)[Rows];	// a fixed width: in the auto-sized card the labels were cut
}

TSharedRef<SWidget> ASlapHUD::BuildSheet()
{
	// The menu screen: a panel filling the space between the two anchor rows (the backdrop under it hides the arena). Top
	// to bottom: the league header, the tab row (the lit tab carries a gold bar), the tab's content in the measured budget,
	// START at the bottom centre under the thumb. X (or the menu icon) closes it; START closes it and plays.
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto TabButton = [this, Self](int32 i, const TCHAR* Name)
	{
		auto bOn = [Self, i] { return Self.IsValid() && Self->Tab == i; };
		return SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SBorder).Padding(0.f).BorderImage_Lambda([bOn] { return bOn() ? &TabOnBrush() : &TabOffBrush(); })
				[Button(FText::FromString(Name), 16, [Self, i] { if (Self.IsValid()) Self->Tab = i; return FReply::Handled(); }, Targets,
					&Hit[i == TabMatch ? HitTabMatch : i == TabBrains ? HitTabBrains : i == TabView ? HitTabView : HitTabTourney],
					TAttribute<FSlateColor>::CreateLambda([bOn] { return FSlateColor(bOn() ? Hi : Dim); }), FLinearColor(0.f, 0.f, 0.f, 0.f))]
			]
			+ SVerticalBox::Slot().AutoHeight()
			[SNew(SBox).HeightOverride(3.f)[SNew(SImage).Image(&BarBrush()).ColorAndOpacity_Lambda([bOn] { return bOn() ? FSlateColor(Hi) : FSlateColor(FLinearColor(1.f, 1.f, 1.f, 0.08f)); })]];
	};
	auto Header = [Self]	// the league line: what this screen is, and how the 16 places are filled today
	{
		return SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)[FitLabel(FText::FromString(TEXT("POWER SLAP LEAGUE")), 20, Hi)]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[Label(TAttribute<FText>::CreateLambda([Self]
				{
					if (!Self.IsValid()) return FText();
					int32 Fighters = 0;
					for (const FString& C : Self->SlotCards) Fighters += !C.IsEmpty();
					return FText::FromString(FString::Printf(TEXT("16 PLACES  ·  %d FIGHTERS  ·  %d BYES"), Fighters, RosterSize - Fighters));
				}), 13, Dim)]
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 0.f)
			[SNew(SBox).HeightOverride(1.f)[SNew(SImage).Image(&BarBrush()).ColorAndOpacity(FSlateColor(FLinearColor(1.f, 1.f, 1.f, 0.12f)))]];
	};
	return SNew(SOverlay)
		.Visibility_Lambda([Self] { return Self.IsValid() && Self->bMenu ? EVisibility::Visible : EVisibility::Collapsed; })
		+ SOverlay::Slot().VAlign(VAlign_Fill)
		// between the anchor rows (and the platform's insets): the title, FPS, menu icon and build stamp stay around it
		.Padding(TAttribute<FMargin>::CreateLambda([Self] { return FMargin(0.f, TopBar + (Self.IsValid() ? Self->SafeTopSlate : 0.f), 0.f, BottomBar + (Self.IsValid() ? Self->SafeBottomSlate : 0.f)); }))
		[
			SAssignNew(SheetRoot, SBorder).BorderImage(&SheetBrush()).Padding(FMargin(12.f, 10.f, 12.f, 8.f))
			[
				SNew(SBox).Padding(TAttribute<FMargin>::CreateLambda([Self]	// the side insets
				{
					const float K = Self.IsValid() ? 1.f / FMath::Max(Self->DpiScale, 0.01f) : 0.f;
					return Self.IsValid() ? FMargin(Self->Safe.Left * K, 0.f, Self->Safe.Right * K, 0.f) : FMargin(0.f);
				}))
				[
					// the height budget (the whole screen between the rows): the tab's content is clipped inside it, never the tabs or START
					SAssignNew(SheetBody, SBox).HeightOverride_Lambda([Self] { return FOptionalSize(Self.IsValid() ? FMath::Max(120.f, Self->SheetBudget) : 600.f); })
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight().Padding(FMargin(4.f, 2.f, 4.f, 8.f))[Header()]
						+ SVerticalBox::Slot().AutoHeight()
						[
							SAssignNew(SheetTabs, SHorizontalBox)
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f, 0.f)[TabButton(TabMatch, TEXT("MATCH"))]
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f, 0.f)[TabButton(TabBrains, TEXT("BRAINS"))]
							+ SHorizontalBox::Slot().FillWidth(1.f).Padding(2.f, 0.f)[TabButton(TabView, TEXT("VIEW"))]
							+ SHorizontalBox::Slot().FillWidth(1.2f).Padding(2.f, 0.f)[TabButton(TabTourney, TEXT("TOURNEY"))]
							+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f, 2.f, 0.f)
							[Button(FText::FromString(TEXT("X")), 18, [Self] { if (Self.IsValid()) Self->OpenMenu(false); return FReply::Handled(); }, Targets, &Hit[HitClose], Dim, FLinearColor(0.f, 0.f, 0.f, 0.f))]
						]
						+ SVerticalBox::Slot().FillHeight(1.f).Padding(FMargin(4.f, 10.f, 4.f, 8.f))
						[
							SAssignNew(SheetContent, SWidgetSwitcher).WidgetIndex_Lambda([Self] { return Self.IsValid() ? Self->Tab : 0; })
							+ SWidgetSwitcher::Slot()[BuildMatchTab()]
							+ SWidgetSwitcher::Slot()[BuildBrainsTab()]
							+ SWidgetSwitcher::Slot()[BuildViewTab()]
							+ SWidgetSwitcher::Slot()[BuildTourneyTab()]
						]
						+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center)	// START, bottom centre: one thumb
						[
							SNew(SBox).WidthOverride(340.f).MinDesiredHeight(58.f)
							[Button(TAttribute<FText>::CreateLambda([Self]
							{
								if (!Self.IsValid()) return FText();
								if (Self->bTravelling) return FText::FromString(TEXT("LOADING..."));
								if (Self->Tab == TabTourney) return FText::FromString(Self->bTourney ? TEXT("PAUSE TOURNAMENT") : TEXT("PLAY TOURNAMENT"));
								return FText::FromString(Self->IsRematch() ? TEXT("REMATCH") : TEXT("START MATCH"));
							}), 20, [Self]
							{
								if (!Self.IsValid() || Self->bTravelling) return FReply::Handled();
								if (Self->Tab == TabTourney) Self->PlayTourney(!Self->bTourney);	// the TOURNEY tab: START runs the bracket
								else Self->Start();
								return FReply::Handled();
							}, Targets, &Hit[HitStart], White, RedTeam, 340.f)]
						]
					]
				]
			]
		];
}

TSharedRef<SWidget> ASlapHUD::BuildMatchTab()
{
	// MATCH: the status line, the two picks (a corner stepper each, then the 16-slot grid a tap assigns), the replay
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto Corner = [&](int32 a)
	{
		return SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth()[Button(FText::FromString(TEXT("<")), 18, [Self, a] { if (Self.IsValid()) Self->StepFighter(a, -1); return FReply::Handled(); }, Targets)]
			+ SHorizontalBox::Slot().FillWidth(1.f).Padding(FMargin(2.f, 0.f))
			[Button(TAttribute<FText>::CreateLambda([Self, a]
			{
				if (!Self.IsValid()) return FText();
				const int32 Slot = Self->SelectedFighter[a];
				const FString Name = Self->Slots.IsValidIndex(Slot) && !Self->Slots[Slot].Key.IsEmpty() ? Self->Slots[Slot].Key : TEXT("OPEN");
				return FText::FromString(FString::Printf(TEXT("%s %s"), a ? TEXT("BLUE") : TEXT("RED"), *Name.ToUpper()));
			}), 14, [Self, a] { if (Self.IsValid()) Self->ActiveCornerSelect = a; return FReply::Handled(); }, Targets, nullptr, White,
				TAttribute<FLinearColor>::CreateLambda([Self, a] { return Self.IsValid() && Self->ActiveCornerSelect == a ? (a ? BlueTeam : RedTeam) : Panel; }))]
			+ SHorizontalBox::Slot().AutoWidth()[Button(FText::FromString(TEXT(">")), 18, [Self, a] { if (Self.IsValid()) Self->StepFighter(a, 1); return FReply::Handled(); }, Targets)];
	};
	// the 16 places of the league, 4 x 4: a fighter's tile (place, name, Elo and record) or a BYE (an open place: the
	// bracket gives it away as a bye, user 2026-11-09: show the 16 players / byes)
	TSharedRef<SUniformGridPanel> Grid = SNew(SUniformGridPanel).SlotPadding(FMargin(3.f)).MinDesiredSlotHeight(52.f).MinDesiredSlotWidth(Touch);
	for (int32 i = 0; i < RosterSize; ++i)
	{
		if (Playable(i))
		{
			Grid->AddSlot(i % 4, i / 4)
			[
				SNew(SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).ClickMethod(EButtonClickMethod::MouseDown).TouchMethod(EButtonTouchMethod::Down)
				.ButtonColorAndOpacity_Lambda([Self, i]
				{
					if (!Self.IsValid()) return FLinearColor::White;
					const bool bRed = Self->SelectedFighter[0] == i, bBlue = Self->SelectedFighter[1] == i;
					return bRed && bBlue ? FLinearColor(0.6f, 0.2f, 0.7f) : bRed ? FLinearColor(0.8f, 0.2f, 0.15f) : bBlue ? FLinearColor(0.15f, 0.35f, 0.85f) : Panel;
				})
				.OnClicked_Lambda([Self, i]
				{
					if (!Self.IsValid()) return FReply::Handled();
					const int32 a = Self->ActiveCornerSelect;
					if (Self->SelectedFighter[a] != i) Self->BrainPick[a] = INDEX_NONE;	// another character: its own brains
					Self->SelectedFighter[a] = i;
					Self->ActiveCornerSelect = 1 - a;
					Self->RosterSel = i;
					return FReply::Handled();
				})
				[FitLabel(TAttribute<FText>::CreateLambda([Self, i]
				{
					if (!Self.IsValid()) return FText();
					return FText::FromString(FString::Printf(TEXT("%d. %s\n%s"), i + 1, *Self->Slots[i].Key, *Self->EloOf(Self->SlotCards[i])));
				}), 12, White)]
			];
		}
		else	// an open place (a BYE in the bracket), or a character with no arena level in this build - not a pick
		{
			const bool bOpen = Slots[i].Key.IsEmpty();
			Grid->AddSlot(i % 4, i / 4)
			[
				SNew(SBorder).BorderImage(bOpen ? &ByeBrush() : &OffBrush()).Padding(0.f).HAlign(HAlign_Center).VAlign(VAlign_Center)
				[SNew(SBox).MinDesiredHeight(52.f).MinDesiredWidth(Touch).HAlign(HAlign_Center).VAlign(VAlign_Center)
					[FitLabel(FText::FromString(bOpen ? FString::Printf(TEXT("%d.\nBYE"), i + 1) : FString::Printf(TEXT("%d. %s\nno arena"), i + 1, *Slots[i].Key)), 12, Dim * (bOpen ? 0.55f : 0.95f))]]
			];
		}
	}
	return SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()[Label(TAttribute<FText>::CreateLambda([Self]	// one dense line: the tape and brains live in BRAINS
		{
			if (!Self.IsValid()) return FText();
			if (Self->bTravelling) return FText::FromString(TEXT("Loading the pairing..."));
			const FSlapHud& H = Self->Last;
			if (H.Match == 0) return FText::FromString(TEXT("Pick two fighters and START. Elo is saved after every match."));
			FString T = FString::Printf(TEXT("M%d  R%d/%d  %s >  SERIES %d-%d"), H.Match, H.Round, Rounds, *H.Striker.ToUpper(), H.Wins[0], H.Wins[1]);
			if (H.Draws) T += FString::Printf(TEXT(" (%d D)"), H.Draws);
			if (!H.Result.IsEmpty()) T += TEXT("\n") + Self->ResultPlain(H);
			if (!H.Result.IsEmpty() && !Self->EloLine.IsEmpty()) T += TEXT("\n") + Self->EloLine;
			return FText::FromString(T);
		}), 14, White, true)]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 2.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(1.f)[Corner(0)]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(FMargin(4.f, 0.f))[Label(FText::FromString(TEXT("VS")), 14, Dim)]
			+ SHorizontalBox::Slot().FillWidth(1.f)[Corner(1)]
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f, 0.f, 2.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)[Heading(TEXT("THE 16 PLACES"))]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
			[Label(TAttribute<FText>::CreateLambda([Self]
			{
				return FText::FromString(Self.IsValid() && Self->ActiveCornerSelect == 1 ? TEXT("a tap picks BLUE") : TEXT("a tap picks RED"));
			}), 13, TAttribute<FSlateColor>::CreateLambda([Self] { return FSlateColor(Self.IsValid() && Self->ActiveCornerSelect == 1 ? BlueTeam : RedTeam); }))]
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 2.f, 0.f, 8.f)[Grid]
		+ SVerticalBox::Slot().AutoHeight()
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(2.f).Padding(FMargin(0.f, 0.f, 4.f, 0.f))
			[Button(TAttribute<FText>::CreateLambda([Self]
			{
				const bool bOn = Self.IsValid() && Self->GetWorld() && Self->GetWorld()->GetTimeSeconds() < Self->ReplayEnd;
				return FText::FromString(bOn ? TEXT("BACK TO LIVE") : Self.IsValid() && Self->bQuarter ? TEXT("REPLAY 1/4") : TEXT("REPLAY 1/2"));
			}), 15, [Self]	// replay over the live picture: the sheet closes so the replay is seen
			{
				if (!Self.IsValid() || Self->bTravelling || Self->Last.Match == 0) return FReply::Handled();
				Self->InstantReplay();
				Self->OpenMenu(false);
				return FReply::Handled();
			}, Targets, &Hit[HitReplay], White, Panel)]
			+ SHorizontalBox::Slot().FillWidth(1.f)[Pill(TEXT("x1/4"), [Self] { return Self.IsValid() && Self->bQuarter; }, [Self] { if (Self.IsValid()) Self->bQuarter = !Self->bQuarter; }, Targets)]
		];
}

TSharedRef<SWidget> ASlapHUD::BuildBrainsTab()
{
	// BRAINS: each corner's brain (those made for its character; this level's pairing only), then the roster cards
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto BrainRow = [&](int32 a)
	{
		return SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(FMargin(0.f, 0.f, 6.f, 0.f))
			[SNew(SBox).WidthOverride(52.f)[Label(FText::FromString(a ? TEXT("BLUE") : TEXT("RED")), 14, a ? BlueTeam : RedTeam)]]
			+ SHorizontalBox::Slot().AutoWidth()[Button(FText::FromString(TEXT("<")), 18, [Self, a] { if (Self.IsValid()) Self->StepBrain(a, -1); return FReply::Handled(); }, Targets)]
			+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center).HAlign(HAlign_Center).Padding(FMargin(4.f, 0.f))
			[FitLabel(TAttribute<FText>::CreateLambda([Self, a]
			{
				if (!Self.IsValid()) return FText();
				if (Self->PairingFor(Self->SelectedFighter[0], Self->SelectedFighter[1]) != Self->HerePairing) return FText::FromString(TEXT("the level's default"));
				const int32 P = Self->BrainPick[a];
				const FString Name = Self->Brains.IsValidIndex(P) ? Self->Brains[P].AssetName.ToString() : Self->Last.Brain[a];
				const FString* Scouting = Self->Scout.Find(Name);
				return FText::FromString(Scouting ? Name + TEXT("\n") + *Scouting : Name);
			}), 14, Hi)]
			+ SHorizontalBox::Slot().AutoWidth()[Button(FText::FromString(TEXT(">")), 18, [Self, a] { if (Self.IsValid()) Self->StepBrain(a, 1); return FReply::Handled(); }, Targets, a == 0 ? &Hit[HitRedNext] : nullptr)];
	};
	TSharedRef<SUniformGridPanel> Grid = SNew(SUniformGridPanel).SlotPadding(FMargin(2.f)).MinDesiredSlotHeight(Touch).MinDesiredSlotWidth(Touch);	// the roster: 4 x 4
	for (int32 i = 0; i < RosterSize; ++i)
	{
		const bool bOpen = Slots[i].Key.IsEmpty();
		Grid->AddSlot(i % 4, i / 4)
		[
			SNew(SButton).HAlign(HAlign_Center).VAlign(VAlign_Center).ClickMethod(EButtonClickMethod::MouseDown).TouchMethod(EButtonTouchMethod::Down).OnClicked_Lambda([Self, i] { if (Self.IsValid()) Self->RosterSel = i; return FReply::Handled(); })
			[FitLabel(FText::FromString(FString::Printf(TEXT("%d %s"), i + 1, bOpen ? TEXT("open") : *Slots[i].Key)), 13,
				TAttribute<FSlateColor>::CreateLambda([Self, i, bOpen] { return FSlateColor(Self.IsValid() && Self->RosterSel == i ? Hi : bOpen ? Dim * 0.7f : White); }))]
		];
	}
	return SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()[Heading(TEXT("EACH CORNER'S BRAIN (START plays it)"))]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f, 0.f, 2.f)[BrainRow(0)]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 2.f, 0.f, 8.f)[BrainRow(1)]
		+ SVerticalBox::Slot().AutoHeight()[Heading(TEXT("ROSTER"))]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 2.f)[Grid]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 0.f)
		[Label(TAttribute<FText>::CreateLambda([Self] { return Self.IsValid() && Self->Slots.IsValidIndex(Self->RosterSel) ? FText::FromString(Self->Slots[Self->RosterSel].Value) : FText(); }), 13, White, true)];
}

TSharedRef<SWidget> ASlapHUD::BuildViewTab()
{
	// VIEW: the overlays, the camera (the director or a pinned shot), the quality tier, the burst capture, the legend
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto Flag = [&](const TCHAR* Code, bool ASlapHUD::* Member, TSharedPtr<SWidget>* Out = nullptr)
	{
		return Pill(Code, [Self, Member] { return Self.IsValid() && (*Self).*Member; }, [Self, Member] { if (Self.IsValid()) (*Self).*Member = !((*Self).*Member); }, Targets, Out);
	};
	auto Cam = [&](ECam Mode, TSharedPtr<SWidget>* Out = nullptr)
	{
		return Pill(CamName[(int32)Mode], [Self, Mode] { return Self.IsValid() && Self->CamMode == Mode; }, [Self, Mode] { if (Self.IsValid()) Self->SetCam(Mode); }, Targets, Out);
	};
	auto Quality = [&](const TCHAR* Name, int32 T)
	{
		return Pill(Name, [Self, T] { return Self.IsValid() && Self->Tier == T; }, [Self, T]
		{
			if (!Self.IsValid()) return;
			Self->Tier = T;
			Self->ApplyTier(T >= 0 ? T : Self->TierCap);
		}, Targets);
	};
	auto Legend = [](const TCHAR* Code, const TCHAR* Text)
	{
		return SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth()[SNew(SBox).WidthOverride(62.f)[Label(FText::FromString(Code), 12, Hi)]]
			+ SHorizontalBox::Slot().FillWidth(1.f)[Label(FText::FromString(Text), 12, Dim, true)];
	};
	return SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()[Heading(TEXT("OVERLAYS (TAB: the stats panel)"))]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 3.f, 0.f, 8.f)
		[PillRow({ Flag(TEXT("TEL"), &ASlapHUD::bShowTelemetry, &Hit[HitTelemetry]), Flag(TEXT("SCI"), &ASlapHUD::bShowScience), Flag(TEXT("XRAY"), &ASlapHUD::bShowXRay),
			Flag(TEXT("SLOW"), &ASlapHUD::bSlowMo), Flag(TEXT("RPL"), &ASlapHUD::bStrikeReplays), Flag(TEXT("LIVE"), &ASlapHUD::bLive), Flag(TEXT("COM"), &ASlapHUD::bShowCommentary), Flag(TEXT("DBG"), &ASlapHUD::bShowDebug) })]
		+ SVerticalBox::Slot().AutoHeight()[Heading(TEXT("CAMERA (C cycles; AUTO cuts on the wind-up and the impact)"))]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 3.f, 0.f, 8.f)
		[PillRow({ Cam(ECam::Auto, &Hit[HitCamAuto]), Cam(ECam::Wide), Cam(ECam::Side, &Hit[HitCamSide]), Cam(ECam::Close) })]
		+ SVerticalBox::Slot().AutoHeight()[Label(TAttribute<FText>::CreateLambda([Self]
		{
			if (!Self.IsValid()) return FText();
			static const TCHAR* Names[4] = { TEXT("LOW"), TEXT("MEDIUM"), TEXT("HIGH"), TEXT("CINEMA") };
			return FText::FromString(FString::Printf(TEXT("QUALITY  %s, in force %s (this device %s)"), Self->Tier < 0 ? TEXT("AUTO") : Names[Self->Tier], Names[Self->TierLive], Names[Self->DetectTier()]));
		}), 13, Hi)]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 3.f, 0.f, 8.f)
		[PillRow({ Quality(TEXT("AUTO"), -1), Quality(TEXT("LOW"), 0), Quality(TEXT("MED"), 1), Quality(TEXT("HIGH"), 2),
			Pill(TEXT("120 FPS"), [Self] { return Self.IsValid() && Self->bBurst; }, [Self] { if (Self.IsValid() && !PLATFORM_ANDROID && !PLATFORM_IOS) Self->bBurst = !Self->bBurst; }, Targets) })]
		+ SVerticalBox::Slot().AutoHeight()[Legend(TEXT("TEL"), TEXT("balance, palm gauge, head g, swings, joint strain heat"))]
		+ SVerticalBox::Slot().AutoHeight()[Legend(TEXT("SCI"), TEXT("KO danger cross, HIC / BrIC, kinetic chain, palm trail"))]
		+ SVerticalBox::Slot().AutoHeight()[Legend(TEXT("XRAY"), TEXT("each brain's target pose and what it reacts to"))]
		+ SVerticalBox::Slot().AutoHeight()[Legend(TEXT("PLATES"), TEXT("clock ring, STM / NECK chips, DNG = balance + neck damage"))]
		+ SVerticalBox::Slot().AutoHeight()[Legend(TEXT("RPL"), TEXT("each landed strike again, slowed (tap / SPACE skips)"))]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f, 0.f, 0.f)[Label(FText::FromString(TEXT("Swipe the arena for views, double tap to replay.")), 12, Dim, true)];	// the build stamp is the BR anchor's
}

TSharedRef<SWidget> ASlapHUD::BuildTourneyTab()
{
	// TOURNEY: the status line, the bracket (16 -> 8 -> 4 -> 2 -> the champion; winners gold, the match on now lit), the
	// Elo of the field, NEW BRACKET; START (PLAY / PAUSE TOURNAMENT) runs it. Every cell reads the live state.
	TWeakObjectPtr<ASlapHUD> Self(this);
	auto Cell = [Self](int32 c, int32 j)
	{
		auto NameOf = [Self, c, j]() -> FString
		{
			if (!Self.IsValid() || Self->TourneyWon.Num() != TourneyRounds) return FString();
			return c < TourneyRounds ? Self->TourneyEntrant(c, j / 2, j % 2) : Self->TourneyWon.Last()[0];
		};
		auto IsPending = [Self, c, j] { return Self.IsValid() && c < TourneyRounds && Self->TourneyPend[0] == c && Self->TourneyPend[1] == j / 2; };
		return SNew(SBorder).Padding(FMargin(3.f, 0.f)).VAlign(VAlign_Center)
			.BorderImage_Lambda([IsPending] { return IsPending() ? &OnBrush() : &OffBrush(); })
			[FitLabel(TAttribute<FText>::CreateLambda([Self, NameOf, c, j]
			{
				const FString N = NameOf();
				if (N.IsEmpty()) return FText::FromString(TEXT("-"));
				FString T = N.ToUpper();
				if (c > 0 && N != TEXT("BYE") && Self.IsValid() && Self->TourneyHow[c - 1].IsValidIndex(j) && Self->TourneyHow[c - 1][j] != TEXT("BYE"))
					T += TEXT(" ") + Self->TourneyHow[c - 1][j];	// how he got here: KO, DEC, W/O, SEED
				return FText::FromString(T);
			}), c >= TourneyRounds ? 20 : 15, TAttribute<FSlateColor>::CreateLambda([Self, NameOf, IsPending, c, j]
			{
				const FString N = NameOf();
				if (!Self.IsValid() || N.IsEmpty() || N == TEXT("BYE")) return FSlateColor(Dim * 0.5f);
				if (c >= TourneyRounds || IsPending()) return FSlateColor(c >= TourneyRounds ? Hi : White);
				const FString& W = Self->TourneyWon[c][j / 2];
				return FSlateColor(W.IsEmpty() ? White * 0.85f : W == N ? Hi : Dim * 0.6f);
			}))];
	};
	// Two-sided (user 2026-11-09): places 1-8 start on the left, 9-16 on the right, each half narrows toward the centre
	// and the champion sits in the middle. Column order: L16 QF SF FINAL | CHAMP | FINAL SF QF L16.
	static const TCHAR* const ColName[] = { TEXT("LAST 16"), TEXT("QF"), TEXT("SF"), TEXT("FINAL"), TEXT("CHAMPION") };
	TSharedRef<SHorizontalBox> Bracket = SNew(SHorizontalBox), Heads = SNew(SHorizontalBox);
	auto AddColumn = [&](int32 c, int32 Side)	// Side 0 left half, 1 right half, -1 the champion
	{
		TSharedRef<SVerticalBox> Col = SNew(SVerticalBox);
		const int32 Rows = c >= TourneyRounds ? 1 : (16 >> c) / 2, First = Side == 1 ? Rows : 0;
		for (int32 k = 0; k < Rows; ++k)	// equal shares of the half's height: each winner sits level with the pair that fed it
			Col->AddSlot().FillHeight(1.f).VAlign(VAlign_Center).Padding(0.f, 1.f, 0.f, k % 2 && k + 1 < Rows ? 3.f : 1.f)
				[SNew(SBox).HeightOverride(c >= TourneyRounds ? 44.f : c == 0 ? 30.f : 34.f)[Cell(c, First + k)]];
		const float W = c >= TourneyRounds ? 1.25f : c == 0 ? 1.1f : 1.f;
		const bool bFirst = Bracket->NumSlots() == 0;
		Bracket->AddSlot().FillWidth(W).Padding(bFirst ? 0.f : 3.f, 0.f, 0.f, 0.f)[Col];
		Heads->AddSlot().FillWidth(W).Padding(bFirst ? 0.f : 3.f, 0.f, 0.f, 0.f).HAlign(HAlign_Center)[FitLabel(FText::FromString(ColName[c]), 12, Hi)];
	};
	for (int32 c = 0; c < TourneyRounds; ++c) AddColumn(c, 0);
	AddColumn(TourneyRounds, -1);
	for (int32 c = TourneyRounds - 1; c >= 0; --c) AddColumn(c, 1);
	return SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()[Label(TAttribute<FText>::CreateLambda([Self] { return FText::FromString(Self.IsValid() ? Self->TourneyStatus() : FString()); }), 14,
			TAttribute<FSlateColor>::CreateLambda([Self] { return FSlateColor(Self.IsValid() && Self->TourneyWon.Num() == TourneyRounds && !Self->TourneyWon.Last()[0].IsEmpty() ? Hi : White); }), true)]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 4.f)[Heads]
		+ SVerticalBox::Slot().AutoHeight()[SNew(SBox).HeightOverride(8.f * 46.f + 3.f * 3.f)[Bracket]]	// 8 rows a side: room for 14-point names
		+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f, 0.f, 4.f)[Label(TAttribute<FText>::CreateLambda([Self]
		{
			if (!Self.IsValid()) return FText();
			TArray<FString> Field;	// the field's Elo, best first, and the last champions
			for (const FString& Seed : Self->TourneySeeds)
				if (Seed != TEXT("BYE")) Field.Add(Seed);
			Field.Sort([&Self](const FString& A, const FString& B) { return Self->EloNum(A) > Self->EloNum(B); });
			FString T = TEXT("ELO ");
			for (const FString& F : Field) T += FString::Printf(TEXT(" %s %.0f "), *F.ToUpper(), Self->EloNum(F));
			const int32 N = Self->TourneyChampions.Num();
			if (N) T += FString::Printf(TEXT("\nCHAMPIONS (%d): %s"), N, *FString::Join(TArray<FString>(Self->TourneyChampions.GetData() + FMath::Max(0, N - 3), FMath::Min(N, 3)), TEXT(", ")).ToUpper());
			return FText::FromString(T);
		}), 12, Dim, true)]
		+ SVerticalBox::Slot().AutoHeight()
		[Button(FText::FromString(TEXT("NEW BRACKET (reseed by Elo)")), 14, [Self]
		{
			if (Self.IsValid() && !Self->bTravelling) Self->NewTourney();	// stops a running one
			return FReply::Handled();
		}, Targets, &Hit[HitTourneyNew], White, Panel)];
}

bool ASlapHUD::IsRematch() const
{
	return Last.Match > 0 && PairingFor(SelectedFighter[0], SelectedFighter[1]) == HerePairing
		&& SlotCards.IsValidIndex(SelectedFighter[0]) && SlotCards[SelectedFighter[0]] == HereCard[0];
}

void ASlapHUD::Start()
{
	if (bTravelling) return;	// a second tap while the level opens does nothing
	// the level for this pairing; none (a character without an arena) starts nothing rather than replaying this level
	const int32 Target = PairingFor(SelectedFighter[0], SelectedFighter[1]);
	if (Target == INDEX_NONE && HerePairing != INDEX_NONE) return;
	if (Target != INDEX_NONE && Target != HerePairing)
	{
		Travel(Target);
		OpenMenu(false);
		return;
	}
	if (Controller.IsValid())
	{
		UNNEModelData* B[2] = {};	// the brain each corner picked; none keeps the one it plays now
		for (int32 a = 0; a < 2; ++a)
			if (Brains.IsValidIndex(BrainPick[a])) B[a] = Cast<UNNEModelData>(Brains[BrainPick[a]].GetAsset());
		Controller->RequestMatch(B[0], B[1]);
		BrainPick[0] = BrainPick[1] = INDEX_NONE;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: %s"), IsRematch() ? TEXT("REMATCH") : TEXT("START"));
	}
	OpenMenu(false);
}

void ASlapHUD::Travel(int32 P)
{
	if (!Pairings.IsValidIndex(P) || bTravelling) return;
	const FString Package = Pairings[P].PackageName.ToString();
	if (!FPackageName::DoesPackageExist(Package))	// the pairing level is not in this build: stay in the menu
	{
		UE_LOG(LogSlap, Warning, TEXT("SlapHUD: pairing %s is not in this build: the menu stays"), *Package);
		return;
	}
	bTravelling = true;	// this HUD dies with the level; until then a second START or a picker tap is ignored
	TravelledAt = GetWorld()->GetTimeSeconds();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: opening pairing %s"), *Package);
	UGameplayStatics::OpenLevel(this, FName(*Package), true, TEXT("SlapGo"));	// SlapGo: START was pressed, the match there starts at once
}

TArray<int32> ASlapHUD::BrainsFor(int32 Corner) const
{
	TArray<int32> Out;
	const FString Card = Last.Card[Corner] + TEXT("_");
	for (int32 i = 0; i < Brains.Num(); ++i)
		if (Brains[i].AssetName.ToString().StartsWith(Card)) Out.Add(i);
	return Out;
}

void ASlapHUD::StepBrain(int32 a, int32 Dir)
{
	const TArray<int32> F = BrainsFor(a);
	if (F.Num() == 0 || bTravelling || PairingFor(SelectedFighter[0], SelectedFighter[1]) != HerePairing) return;	// another pairing: its own brains, picked there
	int32& P = BrainPick[a];
	if (P == INDEX_NONE)	// from the brain the corner plays now
	{
		const FName Now(*Last.Brain[a]);
		P = Brains.IndexOfByPredicate([&](const FAssetData& B) { return B.AssetName == Now; });
	}
	const int32 k = F.Find(P);
	P = F[k == INDEX_NONE ? 0 : (k + Dir + F.Num()) % F.Num()];
}

void ASlapHUD::StepFighter(int32 a, int32 Dir)
{
	const int32 Cur = SelectedFighter[a];	// the next character with an arena (Matt has none yet: skipped)
	for (int32 s = 1; s < RosterSize; ++s)
	{
		const int32 Cand = (Cur + Dir * s + RosterSize * RosterSize) % RosterSize;
		if (Playable(Cand)) { SelectedFighter[a] = Cand; RosterSel = Cand; BrainPick[a] = INDEX_NONE; break; }
	}
}

void ASlapHUD::InstantReplay()
{
	// the same machinery as the KO replay: each athlete plays back its last poses (3 s kept) at half speed while MuJoCo
	// carries on unseen; the director cuts to the replay shot. Tapped again during a replay: back to live at once
	const double Now = GetWorld()->GetTimeSeconds();
	const bool bStop = Now < ReplayEnd && !bAutoReplay;	// a strike replay running is replaced, not stopped: the tap asked for a replay
	constexpr double Window = 2.5;
	const double Rate = bQuarter ? 0.25 : 0.5;
	for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
		if (V.IsValid()) bStop ? V->Replay(0.0, 0.0, 0.0) : V->Replay(Now - Window, Now, Rate);
	ReplayAt = -1.0;	// a pending KO replay would cut this one short
	ReplayEnd = bStop ? Now : Now + Window / Rate;
	bAutoReplay = false;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: instant replay %s at %.2f s"), bStop ? TEXT("stopped") : TEXT("started"), Now);
}

void ASlapHUD::CyclePreset(int32 Dir)
{
	Preset = (Preset + Dir + 4) % 4;	// a swipe on the arena: none, TEL, TEL+SCI, XRAY (the pills still set any mix)
	bShowTelemetry = Preset == 1 || Preset == 2;
	bShowScience = Preset == 2;
	bShowXRay = Preset == 3;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: view preset %d"), Preset);
}

void ASlapHUD::LoadRoster()
{
	// every character card in its slot (SlapNames.h does the reading: the cards, their tape and the brains made for them)
	TArray<FString> Names;
	for (const FAssetData& B : Brains) Names.Add(B.AssetName.ToString());
	Slap::LoadRoster(RosterSize, Slots, Names, &SlotCards);
}

int32 ASlapHUD::PairingFor(int32 RedSlot, int32 BlueSlot) const
{
	if (!SlotCards.IsValidIndex(RedSlot) || !SlotCards.IsValidIndex(BlueSlot) || SlotCards[RedSlot].IsEmpty() || SlotCards[BlueSlot].IsEmpty()) return INDEX_NONE;
	auto Find = [this](const FString& Level)
	{
		return Pairings.IndexOfByPredicate([&](const FAssetData& A) { return DesktopLevel(A.AssetName.ToString()).Equals(Level, ESearchCase::IgnoreCase); });
	};
	const FString& R = SlotCards[RedSlot];
	const FString& B = SlotCards[BlueSlot];
	int32 P = Find(FString::Printf(TEXT("L_Arena_%s_vs_%s"), *R, *B));
	if (P == INDEX_NONE && R == TEXT("grandpa") && B == TEXT("grandpa")) P = Find(TEXT("L_Arena"));
	if (P == INDEX_NONE) P = Find(FString::Printf(TEXT("L_Arena_%s_vs_%s"), *B, *R));	// only the other corner order was built
	return P;
}

bool ASlapHUD::Playable(int32 Slot) const
{
	for (int32 o = 0; o < SlotCards.Num(); ++o)
		if (PairingFor(Slot, o) != INDEX_NONE) return true;
	return false;
}
