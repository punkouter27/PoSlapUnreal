// ASlapHUD: the win probability. Display only: it reads the HUD copy and writes nothing back.
//
// training/winprob.py fits a logistic model on every logged match (training/models/winprob.json): P(red wins) from the
// judges' running score, the knockdowns, the neck and stamina left, and the score weighted by how much of the match is
// played. The five numbers all change sign when the corners swap and there is no bias term, so a level match reads 50 %.
// The momentum bar above it is the brains' own short-term view (the critics, ~2 s ahead); this is the long view, calibrated
// on results. A build without the file (a phone) draws no row.

#include "SlapHUD.h"

#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Misc/Paths.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"

using namespace SlapUi;

void ASlapHUD::LoadWinProb()
{
	WinW.Reset();
	const TSharedPtr<FJsonObject> J = Slap::ReadJson(FPaths::ProjectDir() / TEXT("training/models/winprob.json"));
	const TArray<TSharedPtr<FJsonValue>>* W = nullptr;
	if (!J || !J->TryGetArrayField(TEXT("weights"), W) || W->Num() != 5)
	{
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: no win probability model (training/winprob.py writes training/models/winprob.json)"));
		return;
	}
	for (const TSharedPtr<FJsonValue>& V : *W) WinW.Add(V->AsNumber());
	WinMatches = (int32)J->GetNumberField(TEXT("matches"));
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: win probability model from %d matches (held-out Brier %.3f; 0.25 = a coin)"), WinMatches, J->GetNumberField(TEXT("brier_held_out")));
}

double ASlapHUD::WinProb(const FSlapHud& H)
{
	// winprob.py's state_features, in the same order: score, downs, neck, stamina, late
	if (WinW.Num() != 5) return -1.0;
	if (H.Match != WinMatch)	// a new match: its turns are counted from here
	{
		WinMatch = H.Match;
		WinTurn0 = H.TurnSeq;
	}
	if (!H.Result.IsEmpty() && H.Winner >= 0) return H.Winner == 0 ? 1.0 : 0.0;	// decided
	double Score = 0.0;
	for (int32 j = 0; j < 3; ++j) Score += H.Cards[j][0] - H.Cards[j][1];
	Score /= 3.0;
	const double Played = FMath::Min(1.0, (H.TurnSeq - WinTurn0) / double(2 * Rounds));
	const double X[5] = { Score, double(H.Downs[1] - H.Downs[0]), NeckLeft(H.Neck[0]) - NeckLeft(H.Neck[1]),
		StaminaLeft(H.Stamina[0]) - StaminaLeft(H.Stamina[1]), Score * Played };
	double Z = 0.0;
	for (int32 k = 0; k < 5; ++k) Z += WinW[k] * X[k];
	return 1.0 / (1.0 + FMath::Exp(-FMath::Clamp(Z, -30.0, 30.0)));
}

void ASlapHUD::DrawWinProb(const FSlapHud& H)
{
	const double P = WinProb(H);
	if (P < 0.0) return;
	WinShown = FMath::FInterpTo(WinShown, (float)P, GetWorld()->GetDeltaSeconds(), 3.f);	// a turn's result slides in over ~1/2 s
	// one thin row under the momentum bar: red's share from the left, a centre tick, the two numbers
	const float X = 8.f * S, Y = PanelBottom - 3.f * S, W = Canvas->ClipX - 16.f * S, Hh = 15.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), X, Y, W, Hh);
	DrawRect(FLinearColor(TeamColor[0]), X, Y + Hh - 3.f * S, W * WinShown, 3.f * S);
	DrawRect(FLinearColor(TeamColor[1]), X + W * WinShown, Y + Hh - 3.f * S, W * (1.f - WinShown), 3.f * S);
	DrawLine(X + W * 0.5f, Y + Hh - 5.f * S, X + W * 0.5f, Y + Hh, FLinearColor::White, 1.f * S);
	const int32 Pct = FMath::RoundToInt(100.f * WinShown);
	Text(FString::Printf(TEXT("%d%%"), Pct), X + 4.f * S, Y, TeamColor[0], 0.8f * S);
	Text(TEXT("WIN PROBABILITY"), Canvas->ClipX * 0.5f, Y, Gold, 0.8f * S, true);
	const FString Blue = FString::Printf(TEXT("%d%%"), 100 - Pct);
	float Tw = 0.f, Th = 0.f;
	Canvas->StrLen(GEngine->GetSmallFont(), Blue, Tw, Th);
	Text(Blue, X + W - 4.f * S - Tw * 0.8f * S, Y, TeamColor[1], 0.8f * S);
	TrackBox(TEXT("WinProbRow"), X, Y, W, Hh);
	PanelBottom = Y + Hh + 4.f * S;
	++WinDrawn;
}
