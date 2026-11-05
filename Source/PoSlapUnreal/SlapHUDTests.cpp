// ASlapHUD's checks: -SlapUiTest (every click target, then the state flow Menu -> Gameplay -> Win/Loss -> Menu -> Reset),
// -SlapRoundTripTest, -SlapLayoutTest, -SlapPairTest. Each logs PASS / FAIL on one line and quits.

#include "SlapHUD.h"

#include "Engine/Canvas.h"
#include "Engine/GameViewportClient.h"
#include "Framework/Application/SlateApplication.h"
#include "HAL/FileManager.h"
#include "Input/HittestGrid.h"
#include "InputCoreTypes.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Layout/WidgetPath.h"
#include "SlapHUDInternal.h"
#include "Widgets/SWindow.h"

using namespace SlapUi;

void ASlapHUD::LayoutAudit()
{
	// -SlapLayoutTest: the sheet as it is actually laid out, measured in Slate units. One line per tab, then a summary.
	// The checks: the sheet stays inside the layer; the tab content fits the box (nothing is clipped inside the sheet);
	// the tab row is on screen at the foot; the sheet never covers a corner anchor; every target is at least 44 units.
	if (!bMenu)	// open the sheet and start on the first tab
	{
		OpenMenu(true);
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
			if (!Unsafe++) UE_LOG(LogSlap, Log, TEXT("SlapHUD layout: outside the safe zone: %s + %s in layer %s + %s"), *APos.ToString(), *ASize.ToString(), *LPos.ToString(), *LSize.ToString());
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
	// The canvas boxes: the scoreboard must never cover the middle of the arena, and no two HUD boxes may overlap.
	// (The overlay boxes - the tell ring on the head - are on the athletes on purpose and are skipped.)
	int32 Overlaps = 0, OnCentre = 0;
	const float CW = Canvas ? Canvas->ClipX : 540.f, CH = Canvas ? Canvas->ClipY : 960.f;
	FIntRect Centre;
	Centre.Min = FIntPoint(FMath::RoundToInt(CW * 0.33f), FMath::RoundToInt(CH * 0.30f));
	Centre.Max = FIntPoint(FMath::RoundToInt(CW * 0.67f), FMath::RoundToInt(CH * 0.70f));
	auto OverlapsBox = [](const FIntRect& A, const FIntRect& B)
	{
		return A.Min.X < B.Max.X && B.Min.X < A.Max.X && A.Min.Y < B.Max.Y && B.Min.Y < A.Max.Y;
	};
	for (int32 i = 0; i < Boxes.Num(); ++i)
	{
		if (Boxes[i].Name.StartsWith(TEXT("Overlay"))) continue;
		if (OverlapsBox(Boxes[i].Rect, Centre)) ++OnCentre;
		for (int32 j = i + 1; j < Boxes.Num(); ++j)
		{
			if (Boxes[j].Name.StartsWith(TEXT("Overlay"))) continue;
			if (OverlapsBox(Boxes[i].Rect, Boxes[j].Rect)) ++Overlaps;
		}
	}
	Fails += Overlaps + OnCentre;
	if (TextOverflows > 0) ++Fails;	// a line that did not fit its box
	LayoutFails += Fails;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD layout tab %d: sheet %.0fx%.0f dp at (%.0f,%.0f), layer %.0fx%.0f dp, budget %.0f, box %.0f, content %.0f of %.0f wanted, tabs %.0f, anchors covered %d, outside the safe zone %d (insets T%.0f B%.0f px, fake %.3f/%.3f, custom %d %.3f/%.3f), smallest target %.0f dp, dpi %.3f, canvas boxes %d overlapping %d, on the centre %d, text overflows %d (shrunk %d), fails %d"),
		Tab, Local(SheetRoot).X, Local(SheetRoot).Y, SPos.X, SPos.Y, Local(Ui).X, Local(Ui).Y, SheetBudget, BoxLocal.Y, ContentLocal.Y, Wanted, TabsLocal.Y, Covered, Unsafe, Safe.Top, Safe.Bottom, FakeSafe.Top, FakeSafe.Bottom, FSlateApplication::Get().IsCustomSafeZoneSet(), FSlateApplication::Get().GetCustomSafeZone().Top, FSlateApplication::Get().GetCustomSafeZone().Bottom, MinTouch, DpiScale, Boxes.Num(), Overlaps, OnCentre, TextOverflows, TextShrunk, Fails);
	LayoutWait = 3;	// a few frames before the next tab
	if (++LayoutStep >= NumTabs)
	{
		UE_LOG(LogSlap, Log, TEXT("SlapHUD layout: %d tabs measured in a %.0f x %.0f layer, %d fails"), (int32)NumTabs, LSize.X, LSize.Y, LayoutFails);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	}
	else Tab = LayoutStep;
}

namespace
{
	constexpr double MenuSettle = 0.6;	// s after the menu opens before a check taps in it: Slate lays it out (and builds its hit grid) on the next paint
	const TCHAR* FlowName(uint8 F) { static const TCHAR* N[] = { TEXT("-"), TEXT("Menu"), TEXT("Gameplay"), TEXT("Result"), TEXT("Reset") }; return N[F]; }
}

ASlapHUD::EFlow ASlapHUD::Flow() const
{
	// the screen's state: the menu with no match running (launch, or after a result), a match being played, the result
	if (bMenu && (Last.Match == 0 || !Last.Result.IsEmpty())) return EFlow::Menu;
	if (Last.Match > 0 && !Last.Result.IsEmpty()) return EFlow::Result;
	if (Last.Match > 0) return EFlow::Gameplay;
	return EFlow::None;
}

bool ASlapHUD::ClickHit(int32 Target)
{
	// A synthetic click at a bound target, hit-tested in the game window itself (the Process*Event path asks the OS which
	// window is under the real cursor). True when the click reached the target (it may be covered, or not laid out yet).
	const TSharedPtr<SWidget> W = Hit[Target];
	FSlateApplication& App = FSlateApplication::Get();
	const FVector2D P = W.IsValid() ? FVector2D(W->GetTickSpaceGeometry().GetAbsolutePositionAtCoordinates(FVector2f(0.5f, Target == HitScrim ? 0.2f : Target == HitPad ? 0.4f : 0.5f))) : FVector2D::ZeroVector;
	const int32 User = App.GetUserIndexForMouse();
	const TSharedPtr<SWindow> Win = GetWorld()->GetGameViewport() ? GetWorld()->GetGameViewport()->GetWindow() : nullptr;
	const FWidgetPath Path = Win ? FWidgetPath(Win->GetHittestGrid().GetBubblePath(P, App.GetCursorRadius(), false, User)) : FWidgetPath();
	auto Ev = [&](const TSet<FKey>& Down, const FKey& Button) { return FPointerEvent(User, FSlateApplication::CursorPointerIndex, P, P, Down, Button, 0.f, FModifierKeysState()); };
	const bool bReached = W.IsValid() && Path.IsValid() && Path.ContainsWidget(W.Get());
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: test click target %d at %s: hit %s%s"), Target, *P.ToString(), Path.IsValid() ? *Path.GetLastWidget()->GetTypeAsString() : TEXT("nothing"), bReached ? TEXT("") : TEXT(" (NOT the target)"));
	App.RoutePointerMoveEvent(Path, Ev({}, EKeys::Invalid), false);	// hover: SButton clicks only when hovered
	App.RoutePointerDownEvent(Path, Ev({ EKeys::LeftMouseButton }, EKeys::LeftMouseButton));
	App.RoutePointerUpEvent(Path, Ev({}, EKeys::LeftMouseButton));
	if (Target == HitPad)	// the second tap of a double tap
	{
		App.RoutePointerDoubleClickEvent(Path, Ev({ EKeys::LeftMouseButton }, EKeys::LeftMouseButton));
		App.RoutePointerUpEvent(Path, Ev({}, EKeys::LeftMouseButton));
	}
	return bReached;
}

void ASlapHUD::UiTest(const FSlapHud& H)
{
	// -SlapUiTest: a normal launch (the menu first). Synthetic clicks at the real widgets, one every 1.5 s from 3 s, each
	// followed by its expected state; every click target must be bound. Then the state flow: the match plays to its result,
	// the result card's MENU, START (REMATCH) from the menu, and the next match running. The flow must read
	// Menu > Gameplay > Result > Menu > Reset > Gameplay. One line per click, one PASS / FAIL line, then it quits.
	enum : uint8 { None, Started, Replay, CamSide, CamAuto, BrainPicked };
	struct FStep { int32 Target; bool bMenuAfter; int32 TabAfter; bool bTelAfter; uint8 Check; };
	static const FStep Steps[] = {
		{ HitStart, false, TabMatch, false, Started },		// START at the launch menu: Menu -> Gameplay
		{ HitGrip, true, TabMatch, false, None },
		{ HitTabView, true, TabView, false, None },
		{ HitTelemetry, true, TabView, true, None },
		{ HitCamSide, true, TabView, true, CamSide },
		{ HitCamAuto, true, TabView, true, CamAuto },
		{ HitScrim, false, TabView, true, None },
		{ HitMenu, true, TabView, true, None },
		{ HitTabBrains, true, TabBrains, true, None },
		{ HitRedNext, true, TabBrains, true, BrainPicked },
		{ HitTabMatch, true, TabMatch, true, None },
		{ HitReplay, false, TabMatch, true, Replay },
		{ HitMenu, true, TabMatch, true, None },
		{ HitStart, false, TabMatch, true, Started },		// a fresh match, red on the brain it picked
		{ HitMenu, true, TabMatch, true, None },
		{ HitClose, false, TabMatch, true, None },
		{ HitPad, false, TabMatch, true, Replay } };		// a double tap on the arena
	constexpr int32 NumSteps = UE_ARRAY_COUNT(Steps);
	const double Now = GetWorld()->GetTimeSeconds();
	auto Quit = [&](bool bOk, const FString& Why)
	{
		bUiDone = true;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test %s: %d of %d clicks wrong; flow %s; %s"), bOk ? TEXT("PASS") : TEXT("FAIL"), UiFails, NumSteps, *FlowTrail, *Why);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);	// short: training shares this machine
	};
	if (bUiDone) return;
	// the state flow, as the screen shows it; a new match after a result is the Reset
	const EFlow F = Flow();
	if (H.MatchSeq != FlowMatchSeq)
	{
		if (FlowSeen == EFlow::Result || (FlowSeen == EFlow::Menu && FlowMatchSeq > 0)) FlowTrail += TEXT(" > Reset");
		FlowMatchSeq = H.MatchSeq;
	}
	if (F != EFlow::None && F != FlowSeen && !(RtStep == 2 && F == EFlow::Result))	// START closes the menu a physics tick before the next match: not a new result
	{
		FlowTrail += (FlowTrail.IsEmpty() ? TEXT("") : TEXT(" > ")) + FString(FlowName((uint8)F));
		FlowSeen = F;
	}
	if (UiStep == 0 && !bUiChecked)	// null bindings: every target the test (and a player) needs exists
	{
		bUiChecked = true;
		for (int32 k = 0; k < HitCount; ++k)
		{
			if (Hit[k].IsValid()) continue;
			++UiFails;
			UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test: click target %d is not bound - WRONG"), k);
		}
	}
	if (UiStep > 0 && UiStep <= NumSteps && !bUiChecked && Now >= UiAt + 0.5)	// check the last click, a frame or more later
	{
		bUiChecked = true;
		const FStep& Was = Steps[UiStep - 1];
		bool bOk = bMenu == Was.bMenuAfter && Tab == Was.TabAfter && bShowTelemetry == Was.bTelAfter;
		FString Extra;
		switch (Was.Check)
		{
		case Started:
			bOk &= H.MatchSeq > UiSeq && H.Result.IsEmpty() && (UiBrain.IsEmpty() || H.Brain[0] == UiBrain);
			Extra = FString::Printf(TEXT(", match seq %d -> %d, red brain %s (want %s)"), UiSeq, H.MatchSeq, *H.Brain[0], UiBrain.IsEmpty() ? TEXT("any") : *UiBrain);
			break;
		case Replay: bOk &= Now < ReplayEnd; Extra = FString::Printf(TEXT(", replay until %.2f s"), ReplayEnd); break;
		case CamSide: bOk &= CamMode == ECam::Side; break;
		case CamAuto: bOk &= CamMode == ECam::Auto; break;
		case BrainPicked: bOk &= BrainPick[0] != INDEX_NONE || BrainsFor(0).Num() == 0; Extra = FString::Printf(TEXT(", red picks %s"), Brains.IsValidIndex(BrainPick[0]) ? *Brains[BrainPick[0]].AssetName.ToString() : TEXT("-")); break;
		default: break;
		}
		UiFails += !bOk;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test click %d (target %d): menu %d tab %d telemetry %d camera %s%s - %s"), UiStep, Was.Target, bMenu, Tab, bShowTelemetry, CamName[(int32)CamMode], *Extra, bOk ? TEXT("ok") : TEXT("WRONG"));
	}
	if (UiStep < NumSteps)
	{
		if (Now < 3.0 + 1.5 * UiStep || (bMenu && Now - MenuAt < MenuSettle) || !bUiChecked) return;
		if (UiStep == 0 && (!bMenu || H.Match != 0)) { ++UiFails; UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test: launch: menu %d, match %d (want the menu, no match) - WRONG"), bMenu, H.Match); }
		if (Steps[UiStep].Check == Started)
		{
			UiSeq = H.MatchSeq;
			UiBrain = UiStep > 0 && Brains.IsValidIndex(BrainPick[0]) ? Brains[BrainPick[0]].AssetName.ToString() : FString();
		}
		ClickHit(Steps[UiStep].Target);
		++UiStep;
		UiAt = Now;
		bUiChecked = false;
		return;
	}
	if (!bUiChecked) return;
	// the tail: the result, the card's MENU, START (REMATCH), the next match
	switch (RtStep)
	{
	case 0:	// waiting for the result card
		if (F == EFlow::Result && Now - UiAt > 1.0 && ResultAt >= 0.0 && Now - ResultAt > 1.0)	// the card has been laid out (ResultAt: RoundTrip)
		{
			RtElo = EloOf(HereCard[0]) + TEXT(" / ") + EloOf(HereCard[1]);
			UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test: result \"%s\" (%s): MENU on the card"), *H.Result, *EloLine);
			ClickHit(HitResultMenu);
			RtStep = 1; RtAt = Now;
		}
		break;
	case 1:	// the menu over the result: REMATCH from START once it has laid out
		if (F != EFlow::Menu) { if (Now - RtAt > 3.0) Quit(false, TEXT("the result card's MENU did not open the menu")); break; }
		if (Now - MenuAt < MenuSettle) break;
		UiSeq = H.MatchSeq;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test: menu over the result (Elo %s); START (%s)"), *RtElo, IsRematch() ? TEXT("REMATCH") : TEXT("START MATCH"));
		ClickHit(HitStart);
		RtStep = 2; RtAt = Now;
		break;
	case 2:	// the next match running
		if (H.MatchSeq > UiSeq && H.Result.IsEmpty() && !bMenu)
		{
			const bool bFlow = FlowTrail.Contains(TEXT("Menu > Gameplay")) && FlowTrail.Contains(TEXT("Gameplay > Result > Menu > Reset"));
			const bool bMirror = HereCard[0] == HereCard[1];
			const bool bElo = bMirror || IFileManager::Get().FileExists(*RatingsPath());
			FString Widgets;	// the display pass: every new widget did its job in the match just played (off: not checked)
			const int32 WidgetFails = bPass ? WidgetChecks(Widgets) : 0;
			UE_LOG(LogSlap, Log, TEXT("SlapHUD: UI test %s"), bPass ? *Widgets : TEXT("widgets: the display pass is off, not checked"));
			Quit(UiFails == 0 && bFlow && bElo && WidgetFails == 0, FString::Printf(TEXT("match %d began after the rematch; Elo %s (%s); widget fails %d"), H.Match, bMirror ? TEXT("unchanged, mirror match") : *EloLine, bElo ? TEXT("saved") : TEXT("NOT saved"), WidgetFails));
		}
		else if (Now - RtAt > 15.0) Quit(false, TEXT("START from the menu did not start the next match within 15 s"));
		break;
	default: break;
	}
	if (!bUiDone && Now > 300.0) Quit(false, FString::Printf(TEXT("timed out at step %d / tail %d"), UiStep, RtStep));
}

void ASlapHUD::RoundTripTest(const FSlapHud& H)
{
	// -SlapRoundTripTest (a normal launch: the menu first, back to it after the result): START at the launch menu, a match to
	// its result, the menu back by itself with the Elo saved, REMATCH (the same START button), and the next match running.
	// 2026-10-30's open bug (J9: REMATCH did not start the next match) was this test tapping START in the same frame the menu
	// came back: the sheet had not been laid out, so its hit grid had no START and the tap fell through to the arena. Every
	// tap now waits MenuSettle after the menu opens, and ClickHit reports a tap that missed its target.
	const double Now = GetWorld()->GetTimeSeconds();
	auto Done = [&](bool bOk, const FString& Why)
	{
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: round trip test %s: %s"), bOk ? TEXT("PASS") : TEXT("FAIL"), *Why);
		RtStep = 99;
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	};
	if (RtStep == 99) return;
	if (Now > 300.0) { Done(false, FString::Printf(TEXT("stuck at step %d after 300 s"), RtStep)); return; }
	switch (RtStep)
	{
	case 0:	// the launch menu, no match
		if (Now < 3.0 || (bMenu && Now - MenuAt < MenuSettle)) return;
		if (!bMenu || H.Match != 0) { Done(false, FString::Printf(TEXT("launch: menu %d, match %d (want the menu, no match)"), bMenu, H.Match)); return; }
		RtElo = EloOf(HereCard[0]) + TEXT(" / ") + EloOf(HereCard[1]);
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: round trip test: launch menu ok (%s vs %s, %s); START"), *HereCard[0], *HereCard[1], *RtElo);
		if (!ClickHit(HitStart)) { Done(false, TEXT("the START tap missed the button")); return; }
		RtStep = 1; RtAt = Now;
		return;
	case 1:	// the match runs, the menu closed
		if (H.Match > 0 && !bMenu) { RtStep = 2; RtAt = Now; UE_LOG(LogSlap, Log, TEXT("SlapHUD: round trip test: match %d running"), H.Match); }
		else if (Now - RtAt > 15.0) Done(false, TEXT("START did not start a match"));
		return;
	case 2:	// the result
		if (!H.Result.IsEmpty()) { RtStep = 3; RtAt = Now; RtSeq = H.MatchSeq; UE_LOG(LogSlap, Log, TEXT("SlapHUD: round trip test: result \"%s\" (%s)"), *H.Result, *EloLine); }
		return;
	case 3:	// back to the menu by itself, the Elo saved
		if (!bMenu) { if (Now - RtAt > ResultHold + 5.0) Done(false, TEXT("the menu did not come back after the result")); return; }
		if (Now - MenuAt < MenuSettle) return;
		{
			const FString Elo = EloOf(HereCard[0]) + TEXT(" / ") + EloOf(HereCard[1]);
			const bool bMirror = HereCard[0] == HereCard[1];
			const bool bSaved = IFileManager::Get().FileExists(*RatingsPath()) || bMirror;
			if (!bMirror && (Elo == RtElo || !bSaved)) { Done(false, FString::Printf(TEXT("Elo not updated or not saved: %s -> %s, file %d"), *RtElo, *Elo, bSaved)); return; }
			UE_LOG(LogSlap, Log, TEXT("SlapHUD: round trip test: menu back %.1f s after the result, Elo %s -> %s (%s); REMATCH"), Now - RtAt, *RtElo, *Elo, *RatingsPath());
		}
		if (!ClickHit(HitStart)) { Done(false, TEXT("the REMATCH tap missed the button")); return; }
		RtStep = 4; RtAt = Now;
		return;
	case 4:	// the rematch is running
		if (H.MatchSeq > RtSeq && H.Result.IsEmpty() && !bMenu) Done(true, FString::Printf(TEXT("launch menu -> START -> result -> menu (%s) -> REMATCH -> match %d"), *EloLine, H.Match));
		else if (Now - RtAt > 15.0) Done(false, TEXT("REMATCH did not start the next match"));
		return;
	default:
		return;
	}
}

void ASlapHUD::PairTest(const FSlapHud& H)
{
	// -SlapPairTest: from /Game/Levels/L_Arena open the next pairing at 3 s, then log what loaded there and quit
	if (H.Match <= 0 || GetWorld()->GetTimeSeconds() < 3.0 || !Pairings.IsValidIndex(HerePairing)) return;
	bPairTest = false;
	if (Pairings[HerePairing].AssetName == TEXT("L_Arena")) Travel((HerePairing + 1) % Pairings.Num());
	else
	{
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: pair test: in %s, %s vs %s, brains %s / %s"), *Pairings[HerePairing].AssetName.ToString(), *H.Card[0], *H.Card[1], *H.Brain[0], *H.Brain[1]);
		UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false);
	}
}
