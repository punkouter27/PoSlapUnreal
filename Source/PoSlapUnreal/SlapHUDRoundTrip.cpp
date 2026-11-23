// ASlapHUD: the round trip (menu -> match -> result -> menu) and the Elo ratings per character.

#include "SlapHUD.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "SlapElo.h"
#include "SlapHUDInternal.h"

using namespace SlapUi;

FString ASlapHUD::RatingsPath()
{
	return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("PoSlap"), TEXT("elo.json"));
}

void ASlapHUD::LoadRatings()
{
	Ratings.Reset();
	FString Text;
	TSharedPtr<FJsonObject> J;
	if (!FFileHelper::LoadFileToString(Text, *RatingsPath()) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), J) || !J) return;
	for (const auto& KV : J->Values)
	{
		const TSharedPtr<FJsonObject>* O;
		if (!KV.Value->TryGetObject(O)) continue;
		FRating R;
		(*O)->TryGetNumberField(TEXT("elo"), R.Elo);
		(*O)->TryGetNumberField(TEXT("w"), R.W);
		(*O)->TryGetNumberField(TEXT("l"), R.L);
		(*O)->TryGetNumberField(TEXT("d"), R.D);
		Ratings.Add(FString(KV.Key), R);	// (5.8: the JSON keys are not FString)
	}
}

void ASlapHUD::SaveRatings() const
{
	TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
	for (const auto& KV : Ratings)
	{
		TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
		O->SetNumberField(TEXT("elo"), FMath::RoundToDouble(KV.Value.Elo * 10.0) / 10.0);
		O->SetNumberField(TEXT("w"), KV.Value.W);
		O->SetNumberField(TEXT("l"), KV.Value.L);
		if (KV.Value.D) O->SetNumberField(TEXT("d"), KV.Value.D);
		J->SetObjectField(KV.Key, O);
	}
	FString Text;
	FJsonSerializer::Serialize(J, TJsonWriterFactory<>::Create(&Text));
	if (!FFileHelper::SaveStringToFile(Text, *RatingsPath())) UE_LOG(LogSlap, Warning, TEXT("SlapHUD: could not save %s"), *RatingsPath());
}

FString ASlapHUD::EloOf(const FString& Card) const
{
	const FRating* R = Ratings.Find(Card);
	if (!R) return FString(TEXT("Elo 1000"));
	return R->D ? FString::Printf(TEXT("Elo %.0f  %d-%d-%d"), R->Elo, R->W, R->L, R->D) : FString::Printf(TEXT("Elo %.0f  %d-%d"), R->Elo, R->W, R->L);
}

double ASlapHUD::EloNum(const FString& Card) const
{
	const FRating* R = Ratings.Find(Card);
	return R ? R->Elo : 1000.0;
}

void ASlapHUD::RoundTrip()
{
	if (!Controller.IsValid() || bTravelling) return;
	const FSlapHud& H = Last;
	const double Now = GetWorld()->GetTimeSeconds();
	if (!bLaunchMenu && Controller->bWaitForStart && H.Match == 0)	// a normal launch: the menu first
	{
		bLaunchMenu = true;
		OpenMenu(true);
		Tab = 0;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: launch menu (waiting for START)"));
	}
	if (SeenWins[0] < 0) { SeenWins[0] = H.Wins[0]; SeenWins[1] = H.Wins[1]; SeenDraws = H.Draws; }
	if (H.Result.IsEmpty()) { ResultAt = -1.0; return; }	// running, or aborted / reset / left: nothing to rate
	if (H.MatchSeq != RatedSeq)	// a new result: rate it once (every completed bout: menu, watch, demo, loop, tournament)
	{
		const SlapElo::EOutcome Out = SlapElo::Outcome(H.Result, H.Wins, H.Draws, SeenWins, SeenDraws);
		if (Out == SlapElo::EOutcome::Pending) return;	// a KO's result line lands a frame before its win count: wait for it
		RatedSeq = H.MatchSeq;
		ResultAt = Now;
		const bool bDraw = Out == SlapElo::EOutcome::Draw;
		const int32 W = Out == SlapElo::EOutcome::RedWins ? 0 : Out == SlapElo::EOutcome::BlueWins ? 1 : -1;	// -1: a draw or no contest
		SeenWins[0] = H.Wins[0]; SeenWins[1] = H.Wins[1]; SeenDraws = H.Draws;
		EloLine.Reset();
		if ((W >= 0 || bDraw) && !HereCard[0].IsEmpty() && !HereCard[1].IsEmpty())
		{
			if (!SlapElo::Rated(HereCard[0], HereCard[1]))	// a mirror match: the character played itself, nothing to rate
				EloLine = FString::Printf(TEXT("%s vs %s: mirror match, Elo unchanged"), *HereCard[0].ToUpper(), *HereCard[1].ToUpper());
			else
			{
				const int32 A = W >= 0 ? W : 0;	// the winner first (a draw: red first)
				Ratings.FindOrAdd(HereCard[A]);
				Ratings.FindOrAdd(HereCard[1 - A]);	// both added before taking references (an add can move the map)
				FRating& Ra = Ratings[HereCard[A]];
				FRating& Rb = Ratings[HereCard[1 - A]];
				const double Delta = SlapElo::Apply(Ra.Elo, Rb.Elo, bDraw ? 0.5 : 1.0);
				if (bDraw) { ++Ra.D; ++Rb.D; }
				else { ++Ra.W; ++Rb.L; }
				EloLine = FString::Printf(TEXT("ELO  %s %.0f (%+.0f)   %s %.0f (%+.0f)%s"), *HereCard[A].ToUpper(), Ra.Elo, Delta, *HereCard[1 - A].ToUpper(), Rb.Elo, -Delta, bDraw ? TEXT("   DRAW") : TEXT(""));
				SaveRatings();
			}
			UE_LOG(LogSlap, Log, TEXT("SlapHUD: rated: %s"), *EloLine);
		}
		if (W >= 0 && H.KOd < 0)	// a decision (a KO already had its voices): the winner pleased, the loser not
		{
			Emote(W, EEmote::Happy, 4);
			Emote(1 - W, EEmote::Angry, 4);
		}
		TourneyResult(W, H);
	}
	// the result card stands until OK (user 2026-11-09: "click OK to go back to the main menu"); a tournament playing itself
	// shows its bracket after ResultHold instead, and the unattended loop starts the next match by itself
	if (ResultAt >= 0.0 && !Controller->bAutoNext && bTourney && Now - ResultAt >= ResultHold)
	{
		ResultAt = -1.0;
		OpenMenu(true);
		Controller->bRestand = true;	// both on their feet behind the bracket
		Tab = bTourney || TourneyPend[0] >= 0 ? TabTourney : TabMatch;	// a tournament shows its bracket between matches
		ActiveCornerSelect = 0;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: result shown %.0f s: back to the menu"), ResultHold);
	}
}

