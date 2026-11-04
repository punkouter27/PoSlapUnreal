// ASlapHUD's canvas: the scoreboard, the momentum bar, the name plates, the stats panel (TAB), the overlays, the impact
// card with its head-snap sparkline, the judges' reveal, the AI read and the result. Authored at 540 x 960 (S scales it).

#include "SlapHUD.h"

#include "Components/PoseableMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "SlapHUDInternal.h"

using namespace SlapUi;

namespace
{
	constexpr double JudgeGap = 0.6, JudgeFlip = 0.25;	// s between the three cards; each card's flip
	constexpr double JudgeHold = 5.0;					// s the judges' panel stays up
	constexpr double ImpactCardSeconds = SnapSeconds + 1.2;	// the impact card outlives its sparkline a little
	constexpr float PlateH = 46.f;						// a name plate's height (authored px)
	const FColor Amber(250, 170, 60), Green(120, 230, 140), Red(250, 80, 60);
}

void ASlapHUD::Text(const FString& Str, float X, float Y, FColor C, float Scale, bool bCentre, UFont* Font)
{
	Font = Font ? Font : GEngine->GetSmallFont();
	float W = 0, Ht = 0;
	if (bCentre) Canvas->StrLen(Font, Str, W, Ht);	// only centring needs the width
	Canvas->SetDrawColor(C);
	// whole pixels: the glyphs land on the pixel grid at every scale, so the type stays crisp
	const float PX = bCentre ? X - W * Scale * 0.5f : X;
	Canvas->DrawText(Font, Str, FMath::RoundToFloat(PX), FMath::RoundToFloat(Y), Scale, Scale);
}

float ASlapHUD::TextBox(const FString& Str, float X, float Y, float MaxW, FColor C, float Scale, bool bCentre, UFont* Font)
{
	// A line that may not overflow its box: a long one (a long name) shrinks to fit, down to MinTextScale of its size so it
	// stays readable; past that it is counted (the layout audit reports it). Nothing is logged per frame.
	Font = Font ? Font : GEngine->GetSmallFont();
	float W = 0.f, Ht = 0.f;
	Canvas->StrLen(Font, Str, W, Ht);
	float Use = Scale;
	if (W * Scale > MaxW + 0.5f && W > 0.f)
	{
		Use = FMath::Max(MaxW / W, Scale * MinTextScale);
		++TextShrunk;
		if (W * Use > MaxW + 0.5f) ++TextOverflows;
	}
	Text(Str, X, Y, C, Use, bCentre, Font);
	return Use;
}

void ASlapHUD::TrackBox(const FString& Name, float X, float Y, float W, float H)
{
	// what the canvas drew and where: the layout audit checks the boxes do not overlap each other or the centre
	FIntRect R;
	R.Min = FIntPoint(FMath::RoundToInt(X), FMath::RoundToInt(Y));
	R.Max = FIntPoint(FMath::RoundToInt(X + W), FMath::RoundToInt(Y + H));
	Boxes.Add({ Name, R });
}

void ASlapHUD::DrawMatch(const FSlapHud& H)
{
	// The scoreboard. The lines are collected first, so the panel behind them is sized to what is on it.
	UFont* Big = GEngine->GetLargeFont();
	UFont* Small = GEngine->GetSmallFont();
	const float X = 16.f * S, MaxW = Canvas->ClipX - 32.f * S;
	struct FLine { UFont* Font; FString Str; FColor Color; float Scale; float H; float W; };
	TArray<FLine> Lines;
	auto Add = [&](UFont* F, const FString& Str, FColor C, float Scale = 1.f)
	{
		float W = 0.f, Ht = 0.f;
		Canvas->StrLen(F, Str, W, Ht);
		Lines.Add({ F, Str, C, Scale, Ht * S * Scale + 4.f * S, FMath::Min(W * S * Scale, MaxW) });
	};
	const FColor Tc = TeamColor[Team(H.Striker)];
	if (H.bWalkIn)
	{
		Add(Big, FString::Printf(TEXT("WALK-IN  %d%%"), FMath::RoundToInt(100.0 * H.WalkInProgress)), FColor::White, 1.2f);
		Add(Small, H.WalkNote.IsEmpty() ? TEXT("Approaching the podium") : TEXT("Walking in on their own legs"), Grey, 1.1f);
	}
	else
	{
		// the match's six turns: done, this one, to come
		const int32 TurnInRound = (H.Striker == CoinTossWinner || CoinTossWinner.IsEmpty()) ? 0 : 1;
		const int32 CurrentTurn = FMath::Clamp((H.Round - 1) * 2 + TurnInRound, 0, 5);
		FString Pips;
		for (int32 r = 1; r <= Rounds; ++r)
		{
			auto PipChar = [&](int32 t) -> const TCHAR*
			{
				if (t < CurrentTurn || (t == CurrentTurn && H.bStruck)) return TEXT("●");
				return t == CurrentTurn ? TEXT("◈") : TEXT("○");
			};
			Pips += FString::Printf(TEXT("R%d [%s %s]  "), r, PipChar((r - 1) * 2), PipChar((r - 1) * 2 + 1));
		}
		Add(Small, Pips.TrimEnd() + (H.SlowMo < 1.f ? TEXT("   SLOW-MO") : TEXT("")), Gold, 1.1f);
		// the clock: the strike clock, the recovery once struck; its colour says how urgent
		const FColor ClockCol = H.bStruck ? Amber : (H.Clock < 5.0 ? FColor(255, 70, 70) : (H.Clock < 10.0 ? FColor(255, 210, 60) : Tc));
		const FString Phase = H.bStruck ? TEXT("RECOVERY") : (Read.bReading || Tell > 0.05f ? TEXT("WIND-UP") : TEXT("SHOT CLOCK"));
		Add(Big, FString::Printf(TEXT("R%d/%d   %s   %s %.1fs"), H.Round, Rounds, *H.Striker.ToUpper(), *Phase, FMath::Max(0.0, H.Clock)), ClockCol, 1.2f);
		const FString TouchLine = H.bStruck || H.Speed > 0 ? FString::Printf(TEXT("%.1f m/s (%s)   snap %.1f   "), H.Speed, *H.Part, H.HeadPeak) : FString();
		if (!(TouchLine + H.Event).IsEmpty()) Add(Small, TouchLine + H.Event, Gold, 1.15f);
	}
	Add(Small, FString::Printf(TEXT("J %d:%d  %d:%d  %d:%d     SERIES %d-%d%s"), H.Cards[0][0], H.Cards[0][1], H.Cards[1][0], H.Cards[1][1], H.Cards[2][0], H.Cards[2][1],
		H.Wins[0], H.Wins[1], H.Draws ? *FString::Printf(TEXT(" (%d D)"), H.Draws) : TEXT("")), Grey, 1.1f);
	float Total = 0.f, W = 0.f;
	for (const FLine& L : Lines) { Total += L.H; W = FMath::Max(W, L.W); }
	const float PanelW = FMath::Max(130.f * S, W + 16.f * S);
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.65f), 8.f * S, Top, PanelW, Total + 6.f * S);	// legible over the truss
	float Y = Top + 3.f * S;
	for (const FLine& L : Lines)
	{
		TextBox(L.Str, X, Y, PanelW - 16.f * S, L.Color, S * L.Scale, false, L.Font);
		Y += L.H;
	}
	PanelBottom = Top + Total + 6.f * S;
	TrackBox(TEXT("Scoreboard"), 8.f * S, Top, PanelW, Total + 6.f * S);
}

void ASlapHUD::DrawMomentum(const FSlapHud& H)
{
	// momentum: each brain's critic (expected reward over the next ~2 s) against what it usually expects in that role
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
	const float MX = 8.f * S, MY = PanelBottom + 4.f * S, MW = Canvas->ClipX - 16.f * S, MH = 16.f * S;	// right under the scoreboard
	DrawRect(FLinearColor(TeamColor[1]), MX, MY, MW, MH);
	DrawRect(FLinearColor(TeamColor[0]), MX, MY, MW * Momentum, MH);
	DrawLine(MX + MW * 0.5f, MY - 2.f * S, MX + MW * 0.5f, MY + MH + 2.f * S, FLinearColor::White, 2.f * S);
	const int32 Pct = FMath::RoundToInt(100.f * Momentum);
	Text(FString::Printf(TEXT("RED %d%%    ◄ WIN MOMENTUM ►    %d%% BLUE"), Pct, 100 - Pct), Canvas->ClipX * 0.5f, MY + 2.f * S, FColor::White, 0.9f * S, true);
	PanelBottom = MY + MH + 6.f * S;
	TrackBox(TEXT("MomentumBar"), MX, MY, MW, MH);
}

void ASlapHUD::DrawNamePlates(const FSlapHud& H)
{
	// One plate per athlete, red left, blue right: the name (a long one shrinks to fit), the KD / KO badges, and three bars -
	// stamina (joint speed) and neck strength from fresh down to their floors (the card's stamina_per_strike and
	// neck_damage_per_snap, tracked by the controller), and the KO danger right now (the capture point outside the soles).
	const float Gap = 6.f * S, W = (Canvas->ClipX - 16.f * S - Gap) * 0.5f, Hh = PlateH * S, Y = PanelBottom;
	for (int32 a = 0; a < 2; ++a)
	{
		const float D = FMath::Clamp((float)H.Danger[a], 0.f, 1.f);
		DangerShown[a] = FMath::FInterpTo(DangerShown[a], D, GetWorld()->GetDeltaSeconds(), 8.f);
		if (D > 0.8f) bDangerHigh[a] = true;
		else if (D < 0.7f) bDangerHigh[a] = false;

		const float X = 8.f * S + a * (W + Gap);
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), X, Y, W, Hh);
		DrawRect(FLinearColor(TeamColor[a]), a ? X + W - 3.f * S : X, Y, 3.f * S, Hh);	// the corner's colour on the outside edge
		// badges first (right-aligned inside the plate), so the name knows how much room it has
		TArray<TPair<FString, FColor>> Badges;
		if (H.KOd == a) Badges.Emplace(TEXT("KO"), Red);
		else if (H.Downs[a] > 0) Badges.Emplace(FString::Printf(TEXT("KD %d"), H.Downs[a]), Amber);
		if (!H.Result.IsEmpty() && H.Winner == a) Badges.Emplace(TEXT("WIN"), Gold);
		if (Team(H.Striker) == a && H.Result.IsEmpty() && !H.bWalkIn) Badges.Emplace(TEXT("STRIKES"), FColor::White);
		float Bx = X + W - 8.f * S;
		for (const TPair<FString, FColor>& B : Badges)
		{
			float Tw = 0.f, Th = 0.f;
			Canvas->StrLen(GEngine->GetSmallFont(), B.Key, Tw, Th);
			const float Pw = Tw * 0.85f * S + 8.f * S;
			Bx -= Pw;
			DrawRect(FLinearColor(B.Value).CopyWithNewOpacity(0.85f), Bx, Y + 4.f * S, Pw, 14.f * S);
			Text(B.Key, Bx + 4.f * S, Y + 4.f * S, FColor::Black, 0.85f * S);
			Bx -= 3.f * S;
		}
		const FString Name = H.Name[a].IsEmpty() ? (a ? TEXT("BLUE") : TEXT("RED")) : H.Name[a].ToUpper();
		TextBox(Name, X + 8.f * S, Y + 3.f * S, FMath::Max(Bx - X - 12.f * S, 40.f * S), TeamColor[a], 1.1f * S);
		// the bars
		const double Bars[3] = { StaminaLeft(H.Stamina[a]), NeckLeft(H.Neck[a]), DangerShown[a] };
		static const TCHAR* Labels[3] = { TEXT("STM"), TEXT("NECK"), TEXT("DNG") };
		const float Lw = 34.f * S, Bw = W - 16.f * S - Lw, By0 = Y + 21.f * S;
		for (int32 b = 0; b < 3; ++b)
		{
			const float By = By0 + b * 8.f * S;
			Text(Labels[b], X + 8.f * S, By - 3.f * S, Grey, 0.6f * S);
			DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.15f), X + 8.f * S + Lw, By, Bw, 5.f * S);
			FLinearColor Col(TeamColor[a]);
			if (b == 2)
			{
				const float Dv = DangerShown[a];
				Col = Dv < 0.35f ? FLinearColor(0.25f, 0.85f, 0.35f) : Dv < 0.8f ? FLinearColor(1.f, 0.75f, 0.2f) : FLinearColor(1.f, 0.25f, 0.2f);
				if (bDangerHigh[a]) Col.A = 0.75f + 0.25f * FMath::Sin(GetWorld()->GetTimeSeconds() * 12.f);
			}
			DrawRect(Col, X + 8.f * S + Lw, By, Bw * FMath::Clamp((float)Bars[b], 0.f, 1.f), 5.f * S);
		}
		TrackBox(a ? TEXT("PlateBlue") : TEXT("PlateRed"), X, Y, W, Hh);
	}
	PanelBottom = Y + Hh + 6.f * S;
}

void ASlapHUD::DrawTelemetry(const FSlapHud& H)
{
	const int32 Sk = Team(H.Striker);
	// balance: a ring under each athlete's soles, the dot is his centre of mass over the floor (green < 5 cm off, red > 10)
	for (int32 a = 0; a < 2; ++a)
	{
		const double Off = FVector2D(H.Com[a] - H.Feet[a]).Size();
		const FColor C = Off < 5.0 ? FColor(90, 230, 120) : Off < 10.0 ? FColor(250, 210, 60) : Red;
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
	const float GX = Canvas->ClipX - 36.f * S, GH = 230.f * S, GY = FMath::Max(PanelBottom + 20.f * S, Bot - 100.f * S - GH), GW = 16.f * S, Max = 12.f;
	const float V = H.bStruck ? H.Speed : H.HandSpeed;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), GX - 4.f * S, GY - 6.f * S, GW + 8.f * S, GH + 32.f * S);
	DrawRect(FLinearColor(TeamColor[Sk]), GX, GY + GH * (1.f - FMath::Clamp(V / Max, 0.f, 1.f)), GW, GH * FMath::Clamp(V / Max, 0.f, 1.f));
	const float Y8 = GY + GH * (1.f - CleanSpeed / Max);
	DrawLine(GX - 4.f * S, Y8, GX + GW + 4.f * S, Y8, FLinearColor::White, 2.f * S);
	Text(FString::Printf(TEXT("%.1f"), V), GX + GW * 0.5f, GY + GH + 6.f * S, H.bStruck && H.Speed >= CleanSpeed ? Gold : FColor::White, S, true);

	// the defender's head acceleration, last 2 s (a strip: the feet, where knockdowns show, stay in view)
	const float BX = 16.f * S, BY = Bot - 34.f * S, BW = 230.f * S, BH = 26.f * S;
	const float Peak = FMath::Max(HeadG);
	const float Scale = FMath::Max(20.f, Peak);
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), BX - 6.f * S, BY - 18.f * S, BW + 12.f * S, BH + 22.f * S);
	Text(FString::Printf(TEXT("HEAD G  %s  peak %.0f g"), *H.Defender.ToUpper(), Peak), BX, BY - 17.f * S, Grey, 0.85f * S);
	for (int32 i = 1; i < HeadG.Num(); ++i)
	{
		const float X0 = BX + BW * (i - 1) / 119.f, X1 = BX + BW * i / 119.f;
		DrawLine(X0, BY + BH * (1.f - HeadG[i - 1] / Scale), X1, BY + BH * (1.f - HeadG[i] / Scale), FLinearColor(Gold), 2.f * S);
	}
	TrackBox(TEXT("HeadG"), BX - 6.f * S, BY - 18.f * S, BW + 12.f * S, BH + 22.f * S);

	// the swing census and the AI read's record (the name plates carry stamina and neck now)
	const float FX = 290.f * S, FY = Bot - 34.f * S, FW = Canvas->ClipX - FX - 10.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), FX - 6.f * S, FY - 18.f * S, FW, 48.f * S);
	Text(TEXT("SWINGS"), FX, FY - 17.f * S, Grey, 0.85f * S);
	Text(FString::Printf(TEXT("FEINT %d   REAL %d"), Read.Feints, Read.Reals), FX, FY - 3.f * S, Read.Feints ? Gold : FColor::White, 0.95f * S);
	Text(FString::Printf(TEXT("AI READ %d / %d right   now %d%%"), Read.ReadsRight, Read.Reads, FMath::RoundToInt(100.f * Read.Score)), FX, FY + 12.f * S, Read.bReading ? Amber : Grey, 0.85f * S);
	TrackBox(TEXT("Swings"), FX - 6.f * S, FY - 18.f * S, FW, 48.f * S);
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
		if (C.Z > 0) Ring(this, C, R, 40, FLinearColor(1.f, 0.95f, 0.8f, 1.f - Ts / 0.4f), 3.f * S);
	}
	const double Tk = Now - KOAt;
	if (Tk < 0.3) DrawRect(FLinearColor(0.9f, 0.1f, 0.05f, 0.35f * (1.f - Tk / 0.3f)), 0, 0, Canvas->ClipX, Canvas->ClipY);

	// the swing's verdict over the athlete: FEINT (pulled back), REAL (it landed) or NEAR MISS (it landed, he did not stagger)
	if (!Read.Tag.IsEmpty() && Now - Read.TagAt < FSlapSwingRead::TagSeconds)
	{
		const bool bFeint = Read.Tag == TEXT("FEINT");
		FVector At = StrikePos;
		const int32 Who = Team(H.Striker);
		if (bFeint && Body[Who].IsValid()) At = Body[Who]->GetBoneLocation(BoneHead);
		const FString Tag = !bFeint && bNearMiss ? TEXT("REAL - NEAR MISS") : Read.Tag;
		const FVector P = Canvas->Project(At + FVector(0.f, 0.f, 26.f));
		if (P.Z > 0) Text(Tag, P.X, P.Y, bFeint ? Gold : (bNearMiss ? Amber : Green), 1.1f * S, true, GEngine->GetMediumFont());
	}

	// tale of the tape at the match's start and through the walk-in
	if ((Now - TapeAt < 5.0 || H.bWalkIn) && H.Result.IsEmpty())
	{
		const float Y = PanelBottom;
		const bool bScout = Scout.Contains(H.Brain[0]) || Scout.Contains(H.Brain[1]);
		const float Row = bScout ? 48.f : 34.f;
		const float CardH = (44.f + 2.f * Row) * S, CardW = Canvas->ClipX - 16.f * S;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.85f), 8.f * S, Y, CardW, CardH);
		DrawRect(FLinearColor(Gold), 8.f * S, Y, CardW, 2.f * S);
		TextBox(TEXT("OFFICIAL POWER SLAP BOUT • 3 ROUNDS • 30s SHOT CLOCK"), Cx, Y + 4.f * S, CardW - 16.f * S, Gold, 0.95f * S, true);
		TextBox(TEXT("VICTORY BY KNOCKOUT OR 3-JUDGE 10-POINT MUST DECISION"), Cx, Y + 18.f * S, CardW - 16.f * S, Grey, 0.78f * S, true);
		for (int32 a = 0; a < 2; ++a)
		{
			const float Ya = Y + (32.f + Row * a) * S;
			TextBox(H.Name[a], 16.f * S, Ya, CardW - 16.f * S, TeamColor[a], 1.2f * S);
			TextBox(H.Tape[a] + TEXT("  ") + H.Brain[a], 16.f * S, Ya + 17.f * S, CardW - 16.f * S, Grey, 0.85f * S);
			if (const FString* L = Scout.Find(H.Brain[a])) TextBox(*L, 16.f * S, Ya + 31.f * S, CardW - 16.f * S, Gold, 0.85f * S);	// training/scouting.py
		}
		TrackBox(TEXT("TaleOfTheTape"), 8.f * S, Y, CardW, CardH);
		// the coin toss, under the tape
		if (H.Round == 1 && H.Speed == 0.0 && !CoinTossWinner.IsEmpty() && !H.bWalkIn)
		{
			const float Yt = Y + CardH + 6.f * S;
			DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.82f), 30.f * S, Yt, Canvas->ClipX - 60.f * S, 24.f * S);
			DrawRect(FLinearColor(TeamColor[Team(CoinTossWinner)]), 30.f * S, Yt, Canvas->ClipX - 60.f * S, 2.f * S);
			TextBox(FString::Printf(TEXT("COIN TOSS: %s STRIKES FIRST"), *CoinTossWinner.ToUpper()), Cx, Yt + 4.f * S, Canvas->ClipX - 76.f * S, Gold, 1.0f * S, true);
		}
	}

	// the knockdown 10-count, until the result
	if (Now - DownAt < 10.0 && DownAt > 0.0 && H.Result.IsEmpty())
	{
		const int32 Count = FMath::Clamp(FMath::FloorToInt((float)(Now - DownAt)) + 1, 1, 10);
		const float Yk = Canvas->ClipY * 0.28f, Wk = Canvas->ClipX - 60.f * S, Hk = 68.f * S;
		const float Pulse = 0.5f + 0.5f * FMath::Abs(FMath::Sin(Now * 8.f));
		DrawRect(FLinearColor(0.85f, 0.1f, 0.05f, 0.22f * Pulse), 0, 0, Canvas->ClipX, Canvas->ClipY);
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.90f), Cx - Wk * 0.5f, Yk, Wk, Hk);
		DrawRect(FLinearColor(1.f, 0.2f, 0.1f, Pulse), Cx - Wk * 0.5f, Yk, Wk, 3.f * S);
		TextBox(FString::Printf(TEXT("REFEREE 10-COUNT:  %d  / 10"), Count), Cx, Yk + 8.f * S, Wk - 16.f * S, FColor(255, 80, 60), 1.4f * S, true);
		TextBox(TEXT("KNOCKDOWN! DEFENDER MUST BEAT THE COUNT"), Cx, Yk + 38.f * S, Wk - 16.f * S, FColor::White, 1.0f * S, true);
	}

	// chat hype (Scripts/chat.py): right side, under the name plates
	if (Now - ChatAt < 15.0)
	{
		const float W = 192.f * S, X = Canvas->ClipX - W - 8.f * S, Y = PanelBottom;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), X, Y, W, 42.f * S);
		Text(TEXT("CHAT HYPE"), X + 8.f * S, Y + 3.f * S, Grey, 0.85f * S);
		DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.12f), X + 80.f * S, Y + 6.f * S, W - 88.f * S, 8.f * S);
		DrawRect(FLinearColor(0.6f, 0.35f, 1.f), X + 80.f * S, Y + 6.f * S, (W - 88.f * S) * Hype, 8.f * S);
		TextBox(ChatLine, X + 8.f * S, Y + 21.f * S, W - 16.f * S, FColor::White, 0.85f * S);
	}

	// commentary, bottom: the latest line for 8 s, wrapped at ~44 characters
	if (bShowCommentary && Now - CommentaryAt < 8.0 && CommentaryAt >= TurnAt && Commentary.Num())
	{
		const float Y = Bot - 108.f * S - Commentary.Num() * 20.f * S;	// above the X-ray lines and the telemetry strips
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), 16.f * S, Y - 6.f * S, Canvas->ClipX - 32.f * S, (Commentary.Num() * 20.f + 10.f) * S);
		for (int32 i = 0; i < Commentary.Num(); ++i) TextBox(Commentary[i], Cx, Y + i * 20.f * S, Canvas->ClipX - 48.f * S, FColor::White, 1.1f * S, true);
	}
}

void ASlapHUD::DrawImpactCard(const FSlapHud& H)
{
	// After each strike, low in the frame: the palm's speed and the part, the referee's read, and a sparkline of the
	// defender's head g over the 2 s after the touch (20 ms averages from the controller), with its peak.
	const double Now = GetWorld()->GetTimeSeconds();
	if (StrikeAt <= 0.0 || Now - StrikeAt > ImpactCardSeconds) return;
	const float Cx = Canvas->ClipX * 0.5f, W = Canvas->ClipX - 40.f * S, Hh = 86.f * S;
	const float Y = Bot - (bShowTelemetry ? 64.f : 10.f) * S - Hh;
	const float X = Cx - W * 0.5f;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.82f), X, Y, W, Hh);
	DrawRect(FLinearColor(StrikeSpeed >= CleanSpeed ? Gold : Grey), X, Y, W, 2.f * S);
	const FString PartName = H.Part.Replace(TEXT("_r"), TEXT("")).Replace(TEXT("_l"), TEXT(""));
	TextBox(FString::Printf(TEXT("IMPACT %.1f m/s (%s)   SNAP %.1f m/s"), StrikeSpeed, *PartName.ToUpper(), H.HeadPeak), Cx, Y + 5.f * S, W - 16.f * S, StrikeSpeed >= CleanSpeed ? Gold : FColor::White, 1.05f * S, true);
	const FString Call = H.bStagger ? TEXT("REFEREE: STAGGER") : (H.Speed >= CleanSpeed ? TEXT("REFEREE: CLEAN STRIKE") : (!H.Event.IsEmpty() ? H.Event : TEXT("REFEREE: LEGAL CONTACT")));
	TextBox(Call, Cx, Y + 22.f * S, W - 16.f * S, H.bStagger ? FColor(255, 120, 60) : (H.Speed >= CleanSpeed ? Green : Grey), 0.9f * S, true);
	// the sparkline: x = s since the touch (0-2), y = head g, scaled to the larger of 20 g and the peak
	const float SX = X + 12.f * S, SY = Y + 40.f * S, SW = W - 24.f * S, SH = 36.f * S;
	DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.06f), SX, SY, SW, SH);
	float Peak = 0.f, PeakT = 0.f;
	for (const FVector2f& P : Snap) if (P.Y > Peak) { Peak = P.Y; PeakT = P.X; }
	const float GMax = FMath::Max(20.f, Peak);
	for (int32 i = 1; i < Snap.Num(); ++i)
	{
		const FVector2f& A = Snap[i - 1];
		const FVector2f& B = Snap[i];
		DrawLine(SX + SW * A.X / SnapSeconds, SY + SH * (1.f - A.Y / GMax), SX + SW * B.X / SnapSeconds, SY + SH * (1.f - B.Y / GMax), FLinearColor(Gold), 2.f * S);
	}
	if (Peak > 0.f)
	{
		const float Px = SX + SW * PeakT / SnapSeconds;
		DrawRect(FLinearColor::White, Px - 2.f * S, SY + SH * (1.f - Peak / GMax) - 2.f * S, 4.f * S, 4.f * S);
		Text(FString::Printf(TEXT("HEAD SNAP  peak %.0f g at %.2f s"), Peak, PeakT), SX + 4.f * S, SY + 2.f * S, Grey, 0.75f * S);
	}
	TrackBox(TEXT("ImpactCard"), X, Y, W, Hh);
}

void ASlapHUD::DrawJudges(const FSlapHud& H)
{
	// The judges' scorecards, revealed one at a time: each card flips over JudgeGap after the one before, then the round's
	// verdict. Drawn from H.RoundCards, the round the controller just scored.
	const double Tc = GetWorld()->GetTimeSeconds() - CardAt;
	if (Tc < 0.0 || Tc > JudgeHold || H.ScoredRound <= 0) return;
	const float W = 260.f * S, Hh = 138.f * S, X = Canvas->ClipX - W - 8.f * S, Y = PanelBottom;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.88f), X, Y, W, Hh);
	DrawRect(FLinearColor(Gold), X, Y, W, 2.f * S);
	Text(FString::Printf(TEXT("ROUND %d SCORECARDS"), H.ScoredRound), X + W * 0.5f, Y + 6.f * S, Gold, 1.05f * S, true);
	int32 ForRed = 0, ForBlue = 0, Shown = 0;
	for (int32 j = 0; j < 3; ++j)
	{
		const double Tj = Tc - 0.3 - JudgeGap * j;	// this card's own clock
		const float Yj = Y + (28.f + 26.f * j) * S;
		const float Cw = W - 20.f * S;
		const float Flip = FMath::Clamp((float)(Tj / JudgeFlip), 0.f, 1.f);	// 0 face down, 1 face up
		const float Sq = FMath::Abs(FMath::Cos(PI * Flip));					// the card narrows to an edge mid-flip
		const float Cx = X + W * 0.5f, Hw = 0.5f * Cw * FMath::Max(Sq, 0.05f);
		const int32 R = H.RoundCards[j][0], B = H.RoundCards[j][1];
		const bool bUp = Flip >= 0.5f;
		const FLinearColor Face = !bUp ? FLinearColor(0.2f, 0.2f, 0.24f) : R > B ? FLinearColor(TeamColor[0]) * 0.6f : B > R ? FLinearColor(TeamColor[1]) * 0.6f : FLinearColor(0.35f, 0.35f, 0.35f);
		DrawRect(Face.CopyWithNewOpacity(1.f), Cx - Hw, Yj - 3.f * S, 2.f * Hw, 22.f * S);
		if (Sq > 0.6f)
			Text(bUp ? FString::Printf(TEXT("JUDGE %d    %d - %d"), j + 1, R, B) : FString::Printf(TEXT("JUDGE %d    ?"), j + 1), Cx, Yj, FColor::White, 0.95f * S, true);
		if (bUp) { ++Shown; ForRed += R > B; ForBlue += B > R; }
	}
	if (Shown == 3 && Tc > 0.3 + JudgeGap * 2 + JudgeFlip + 0.3)	// the verdict, once the last card is up
	{
		const int32 Agree = FMath::Max(ForRed, ForBlue);
		const FString Who = ForRed > ForBlue ? TEXT("RED") : ForBlue > ForRed ? TEXT("BLUE") : FString();
		const FString Verdict = Who.IsEmpty() ? TEXT("ROUND EVEN") : FString::Printf(TEXT("ROUND TO %s (%s)"), *Who, Agree == 3 ? TEXT("UNANIMOUS") : Agree == 2 ? TEXT("SPLIT") : TEXT("1 CARD"));
		TextBox(Verdict, X + W * 0.5f, Y + Hh - 22.f * S, W - 16.f * S, Gold, 0.95f * S, true);
	}
	TrackBox(TEXT("Judges"), X, Y, W, Hh);
}

void ASlapHUD::DrawWinner(const FSlapHud& H)
{
	// the winner marquee and the match's numbers (the Slate result card below carries REMATCH / MENU)
	if (H.Result.IsEmpty()) return;
	const float Cx = Canvas->ClipX * 0.5f, Yw = Top + 8.f * S, Ww = Canvas->ClipX - 20.f * S, Hw = 110.f * S;
	const int32 Win = H.Winner;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.90f), Cx - Ww * 0.5f, Yw, Ww, Hw);
	DrawRect(FLinearColor(Gold), Cx - Ww * 0.5f, Yw, Ww, 3.f * S);
	DrawRect(FLinearColor(Gold), Cx - Ww * 0.5f, Yw + Hw - 3.f * S, Ww, 3.f * S);
	Text(Win >= 0 ? TEXT("★ ★ ★ MATCH WINNER ★ ★ ★") : TEXT("★ ★ ★ MATCH RESULT ★ ★ ★"), Cx, Yw + 8.f * S, Gold, 1.15f * S, true);
	const FString Name = Win >= 0 ? (H.Name[Win].IsEmpty() ? (Win ? TEXT("BLUE") : TEXT("RED")) : H.Name[Win].ToUpper()) : TEXT("DRAW");
	TextBox(Name, Cx, Yw + 26.f * S, Ww - 20.f * S, Win >= 0 ? TeamColor[Win] : FColor::White, 1.5f * S, true, GEngine->GetLargeFont());
	TextBox(H.Result, Cx, Yw + 58.f * S, Ww - 20.f * S, FColor::White, 1.05f * S, true);
	TextBox(FString::Printf(TEXT("STRIKES %d - %d   PEAK %.1f - %.1f m/s   KD %d - %d   SERIES %d - %d"), TotalStrikes[0], TotalStrikes[1], PeakSpeed[0], PeakSpeed[1],
		H.Downs[0], H.Downs[1], H.Wins[0], H.Wins[1]), Cx, Yw + 82.f * S, Ww - 20.f * S, Grey, 0.9f * S, true);
	TrackBox(TEXT("WinnerBanner"), Cx - Ww * 0.5f, Yw, Ww, Hw);
}

void ASlapHUD::DrawTell(const FSlapHud& H)
{
	// The AI read and the tell, over the striker's head. The read (SlapSwingRead.h) sees the wind-up from the shoulder's swing
	// and the palm speeding up while it is still out of reach: a dashed ring that fills with the read's confidence and the
	// words "AI READ: WIND-UP". The tell follows as the palm closes in fast: a solid ring and an arrow at the defender.
	const int32 Who = Team(H.Striker);
	if (H.bWalkIn || !Body[Who].IsValid() || (Read.Score <= 0.05f && Tell <= 0.02f)) return;
	const FVector P = Canvas->Project(Body[Who]->GetBoneLocation(BoneHead));
	if (P.Z <= 0) return;
	if (Read.Score > 0.05f && !H.bStruck)
	{
		const float R = 34.f * S;
		const int32 Seg = 24, Lit = FMath::RoundToInt(Seg * FMath::Clamp(Read.Score, 0.f, 1.f));
		for (int32 k = 0; k < Seg; k += 1)	// dashes, lit up to the read's score
		{
			const float A0 = -0.5f * PI + 2.f * PI * k / Seg, A1 = A0 + 2.f * PI / Seg * 0.6f;
			const FLinearColor C = k < Lit ? FLinearColor(0.4f, 0.85f, 1.f, 0.95f) : FLinearColor(1.f, 1.f, 1.f, 0.2f);
			DrawLine(P.X + R * FMath::Cos(A0), P.Y + R * FMath::Sin(A0), P.X + R * FMath::Cos(A1), P.Y + R * FMath::Sin(A1), C, 2.5f * S);
		}
		if (Read.bReading) Text(FString::Printf(TEXT("AI READ: WIND-UP %d%%"), FMath::RoundToInt(100.f * Read.Score)), P.X, P.Y - R - 18.f * S, FColor(110, 215, 255), 0.95f * S, true);
	}
	if (Tell > 0.02f)
	{
		const float R = (26.f + 26.f * (1.f - Tell)) * S;
		const FLinearColor Col = FMath::Lerp(FLinearColor(1.f, 0.85f, 0.3f, 0.9f), FLinearColor(1.f, 0.35f, 0.2f, 0.95f), Tell);
		Ring(this, P, R, 28, Col, 3.f * S);
		if (Body[1 - Who].IsValid())	// the arrow points at the defender and grows with the tell
		{
			const FVector Q = Canvas->Project(Body[1 - Who]->GetBoneLocation(BoneHead));
			if (Q.Z > 0)
			{
				const FVector D = (Q - P).GetSafeNormal2D();
				const float L = 26.f * S * Tell;
				DrawLine(P.X + D.X * R, P.Y + D.Y * R, P.X + D.X * (R + L), P.Y + D.Y * (R + L), Col, 3.f * S);
			}
		}
	}
	TrackBox(TEXT("OverlayTell"), P.X - 40.f * S, P.Y - 60.f * S, 80.f * S, 100.f * S);
}
