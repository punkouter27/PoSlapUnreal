// ASlapHUD: the 16-place tournament (seeded by Elo, BYEs, single elimination), played on its own to a champion.

#include "SlapHUD.h"

#include "Dom/JsonObject.h"
#include "Misc/CommandLine.h"
#include "Misc/DateTime.h"
#include "Misc/Parse.h"
#include "TimerManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "SlapHUDInternal.h"

using namespace SlapUi;

namespace
{
	const FString Bye(TEXT("BYE"));
	// the seed at each bracket position: 1 v 16, 8 v 9, 4 v 13, 5 v 12 | 2 v 15, 7 v 10, 3 v 14, 6 v 11 (the top seeds
	// meet the BYEs, and 1 and 2 only meet in the final)
	constexpr int32 SeedAt[16] = { 1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11 };
	const TCHAR* RoundName[] = { TEXT("ROUND OF 16"), TEXT("QUARTER-FINAL"), TEXT("SEMI-FINAL"), TEXT("FINAL") };

	TArray<TSharedPtr<FJsonValue>> Strings(const TArray<FString>& A)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		for (const FString& S : A) Out.Add(MakeShared<FJsonValueString>(S));
		return Out;
	}
	TArray<FString> Strings(const TArray<TSharedPtr<FJsonValue>>& A)
	{
		TArray<FString> Out;
		for (const TSharedPtr<FJsonValue>& V : A) Out.Add(V->AsString());
		return Out;
	}
}

FString ASlapHUD::TourneyPath()
{
	return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("PoSlap"), TEXT("tournament.json"));
}

void ASlapHUD::LoadTourney()
{
	TourneySeeds.Reset();
	TourneyWon.Reset();
	TourneyHow.Reset();
	TourneyChampions.Reset();
	bTourney = false;
	TourneyPend[0] = TourneyPend[1] = -1;
	TourneyDraws = 0;
	FString Text;
	TSharedPtr<FJsonObject> J;
	if (FFileHelper::LoadFileToString(Text, *TourneyPath()) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), J) && J)
	{
		const TArray<TSharedPtr<FJsonValue>>* A;
		if (J->TryGetArrayField(TEXT("seeds"), A)) TourneySeeds = Strings(*A);
		if (J->TryGetArrayField(TEXT("champions"), A)) TourneyChampions = Strings(*A);
		for (const TCHAR* Field : { TEXT("won"), TEXT("how") })
		{
			TArray<TArray<FString>>& Dest = FCString::Strcmp(Field, TEXT("won")) == 0 ? TourneyWon : TourneyHow;
			if (J->TryGetArrayField(Field, A))
				for (const TSharedPtr<FJsonValue>& R : *A) Dest.Add(Strings(R->AsArray()));
		}
		J->TryGetBoolField(TEXT("running"), bTourney);
		J->TryGetNumberField(TEXT("pend_round"), TourneyPend[0]);
		J->TryGetNumberField(TEXT("pend_index"), TourneyPend[1]);
		J->TryGetNumberField(TEXT("draws"), TourneyDraws);
	}
	bool bShape = TourneySeeds.Num() == 16 && TourneyWon.Num() == TourneyRounds && TourneyHow.Num() == TourneyRounds;
	bool bUntouched = TourneyPend[0] < 0;
	for (int32 r = 0; bShape && r < TourneyRounds; ++r)
	{
		bShape = TourneyWon[r].Num() == (8 >> r) && TourneyHow[r].Num() == (8 >> r);
		for (const FString& W : TourneyWon[r]) bUntouched &= W.IsEmpty();
	}
	if (bShape && TourneyPend[0] >= 0 && !(TourneyPend[0] < TourneyRounds && TourneyWon[TourneyPend[0]].IsValidIndex(TourneyPend[1]))) TourneyPend[0] = TourneyPend[1] = -1;
	// none yet, a file from another layout, or a bracket nobody has played yet (reseeded: the roster and the Elo as they are now)
	const bool bCold = !GetWorld() || !GetWorld()->URL.HasOption(TEXT("SlapGo"));	// not a level the tournament opened
	if (!bShape || (bUntouched && !bTourney) || (bCold && bTourneyAuto))
	{
		const TArray<FString> Keep = TourneyChampions;
		NewTourney();
		TourneyChampions = Keep;
		SaveTourney();
	}
	else if (bTourney && bCold)	// a cold launch pauses a running one: PLAY carries on where it stopped
	{
		bTourney = false;
		SaveTourney();
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament paused (a fresh launch): PLAY carries on"));
	}
	TourneyNextAt = -1.0;
	if (bCold && bTourneyAuto)	// -SlapTourney: a new bracket, played at once (the test, an unattended night)
	{
		bTourney = true;
		TourneyNextAt = 0.0;
		SaveTourney();
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: -SlapTourney: a new bracket, played to the end"));
	}
}

void ASlapHUD::SaveTourney() const
{
	TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetArrayField(TEXT("seeds"), Strings(TourneySeeds));
	TArray<TSharedPtr<FJsonValue>> Won, How;
	for (int32 r = 0; r < TourneyWon.Num(); ++r)
	{
		Won.Add(MakeShared<FJsonValueArray>(Strings(TourneyWon[r])));
		How.Add(MakeShared<FJsonValueArray>(Strings(TourneyHow[r])));
	}
	J->SetArrayField(TEXT("won"), Won);
	J->SetArrayField(TEXT("how"), How);
	J->SetBoolField(TEXT("running"), bTourney);
	J->SetNumberField(TEXT("pend_round"), TourneyPend[0]);
	J->SetNumberField(TEXT("pend_index"), TourneyPend[1]);
	J->SetNumberField(TEXT("draws"), TourneyDraws);
	J->SetArrayField(TEXT("champions"), Strings(TourneyChampions));
	TSharedRef<FJsonObject> Elo = MakeShared<FJsonObject>();	// the seeds' Elo as it stands (the ratings file is the source)
	for (const FString& Seed : TourneySeeds)
		if (Seed != Bye) Elo->SetNumberField(Seed, FMath::RoundToDouble(EloNum(Seed) * 10.0) / 10.0);
	J->SetObjectField(TEXT("elo"), Elo);
	FString Text;
	FJsonSerializer::Serialize(J, TJsonWriterFactory<>::Create(&Text));
	if (!FFileHelper::SaveStringToFile(Text, *TourneyPath())) UE_LOG(LogSlap, Warning, TEXT("SlapHUD: could not save %s"), *TourneyPath());
}

void ASlapHUD::NewTourney()
{
	// every character in the roster, by Elo (ties: roster slot), into the 16 places; the rest are BYEs
	TArray<FString> Field;
	for (const FString& C : SlotCards)
		if (!C.IsEmpty()) Field.Add(C);
	Field.StableSort([this](const FString& A, const FString& B) { return EloNum(A) > EloNum(B); });
	TourneySeeds.Init(Bye, 16);
	for (int32 p = 0; p < 16; ++p)
		if (Field.IsValidIndex(SeedAt[p] - 1)) TourneySeeds[p] = Field[SeedAt[p] - 1];
	TourneyWon.Reset();
	TourneyHow.Reset();
	for (int32 r = 0; r < TourneyRounds; ++r)
	{
		TourneyWon.Emplace_GetRef().Init(FString(), 8 >> r);
		TourneyHow.Emplace_GetRef().Init(FString(), 8 >> r);
	}
	bTourney = false;
	TourneyPend[0] = TourneyPend[1] = -1;
	TourneyDraws = 0;
	TourneyNextAt = -1.0;
	SaveTourney();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: new tournament: %d fighters, %d BYEs (%s)"), Field.Num(), 16 - Field.Num(), *FString::Join(Field, TEXT(", ")));
}

FString ASlapHUD::TourneyEntrant(int32 Round, int32 Index, int32 Side) const
{
	if (Round == 0) return TourneySeeds.IsValidIndex(2 * Index + Side) ? TourneySeeds[2 * Index + Side] : FString();
	return TourneyWon.IsValidIndex(Round - 1) && TourneyWon[Round - 1].IsValidIndex(2 * Index + Side) ? TourneyWon[Round - 1][2 * Index + Side] : FString();
}

bool ASlapHUD::NextTourneyMatch(int32& OutRound, int32& OutIndex)
{
	// round by round, match by match: a BYE goes through at once; a pairing with no level in this build is a walkover to
	// the one who has an arena (both or neither: the higher Elo). The first match that can be played is returned.
	for (int32 r = 0; r < TourneyWon.Num(); ++r)
	{
		for (int32 i = 0; i < TourneyWon[r].Num(); ++i)
		{
			if (!TourneyWon[r][i].IsEmpty()) continue;
			const FString A = TourneyEntrant(r, i, 0), B = TourneyEntrant(r, i, 1);
			if (A.IsEmpty() || B.IsEmpty()) continue;	// waiting on an earlier match
			if (A == Bye || B == Bye)
			{
				TourneyWon[r][i] = A == Bye ? B : A;
				TourneyHow[r][i] = TEXT("BYE");
				continue;
			}
			const int32 SA = SlotCards.Find(A), SB = SlotCards.Find(B);
			if (PairingFor(SA, SB) != INDEX_NONE)
			{
				OutRound = r;
				OutIndex = i;
				return true;
			}
			const bool bA = Playable(SA), bB = Playable(SB);
			TourneyWon[r][i] = bA != bB ? (bA ? A : B) : (EloNum(A) >= EloNum(B) ? A : B);
			TourneyHow[r][i] = TEXT("W/O");
			UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament %s: %s vs %s has no arena in this build: %s goes through (walkover)"), RoundName[r], *A, *B, *TourneyWon[r][i]);
		}
	}
	return false;
}

void ASlapHUD::StartTourneyMatch(int32 Round, int32 Index)
{
	const FString A = TourneyEntrant(Round, Index, 0), B = TourneyEntrant(Round, Index, 1);
	TourneyPend[0] = Round;
	TourneyPend[1] = Index;
	TourneyNextAt = -1.0;
	SelectedFighter[0] = SlotCards.Find(A);	// the higher half of the bracket in red; PairingFor takes either corner order
	SelectedFighter[1] = SlotCards.Find(B);
	BrainPick[0] = BrainPick[1] = INDEX_NONE;	// each pairing plays its own default brains
	SaveTourney();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament %s match %d: %s vs %s%s"), RoundName[Round], Index + 1, *A, *B, TourneyDraws ? *FString::Printf(TEXT(" (replay %d after a draw)"), TourneyDraws) : TEXT(""));
	Start();	// this level, or the pairing's (it opens with ?SlapGo: the match starts there at once)
}

void ASlapHUD::PlayTourney(bool bPlay)
{
	if (bPlay && TourneyWon.Num() == TourneyRounds && !TourneyWon.Last()[0].IsEmpty()) NewTourney();	// the last one is over: a new draw
	bTourney = bPlay;
	TourneyNextAt = bPlay ? GetWorld()->GetTimeSeconds() : -1.0;
	if (bPlay) Tab = TabTourney;
	SaveTourney();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament %s"), bPlay ? TEXT("PLAY") : TEXT("PAUSE"));
}

void ASlapHUD::TourneyTick(double Now)
{
	if (!bTourney || bTravelling || !Controller.IsValid() || TourneyNextAt < 0.0 || Now < TourneyNextAt) return;
	if (TourneyPend[0] >= 0)	// a pending match: carry on with it (a replay after a draw, a resume after a pause)
	{
		StartTourneyMatch(TourneyPend[0], TourneyPend[1]);
		return;
	}
	int32 R = -1, I = -1;
	if (NextTourneyMatch(R, I))
	{
		StartTourneyMatch(R, I);
		return;
	}
	// no match left: the champion
	const FString Champ = TourneyWon.Last()[0];
	bTourney = false;
	TourneyNextAt = -1.0;
	TourneyChampions.Add(FDateTime::Now().ToString(TEXT("%Y-%m-%d ")) + Champ);
	SaveTourney();
	OpenMenu(true);
	Tab = TabTourney;
	Ticker(FString::Printf(TEXT("TOURNAMENT CHAMPION: %s (Elo %.0f)"), *Champ.ToUpper(), EloNum(Champ)), Gold);
	Caption(TEXT("CHAMPION!"), FString::Printf(TEXT("%s WINS THE TOURNAMENT"), *Champ.ToUpper()), Gold, 6.0);
	Announce(TEXT("champion_") + Champ);
	for (int32 a = 0; a < 2; ++a)	// the champion's voice, if he is in this arena
		if (Last.Card[a] == Champ) { EmoteAt[a] = -100.0; Emote(a, EEmote::Happy, 5); }
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament over: CHAMPION %s (Elo %.0f); bracket %s"), *Champ, EloNum(Champ), *TourneyPath());
	if (bTourneyAuto && FParse::Param(FCommandLine::Get(), TEXT("SlapTourneyQuit")))	// the check: done once a champion is crowned
		GetWorld()->GetTimerManager().SetTimer(TourneyQuitTimer, [] { FPlatformMisc::RequestExit(false); }, 4.f, false);
}

void ASlapHUD::TourneyResult(int32 W, const FSlapHud& H)
{
	const int32 R = TourneyPend[0], I = TourneyPend[1];
	if (R < 0 || !TourneyWon.IsValidIndex(R) || !TourneyWon[R].IsValidIndex(I)) return;
	const FString A = TourneyEntrant(R, I, 0), B = TourneyEntrant(R, I, 1);
	if (!((HereCard[0] == A && HereCard[1] == B) || (HereCard[0] == B && HereCard[1] == A))) return;	// not the tournament's match
	const double Now = GetWorld()->GetTimeSeconds();
	TourneyNextAt = bTourney ? Now + ResultHold + TourneyShow : -1.0;	// the result, then the bracket, then the next match
	if (W < 0 && ++TourneyDraws < 3)	// a draw: the same match again
	{
		SaveTourney();
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament: %s vs %s drawn (%d): replayed"), *A, *B, TourneyDraws);
		return;
	}
	TourneyWon[R][I] = W >= 0 ? HereCard[W] : (EloNum(A) >= EloNum(B) ? A : B);
	TourneyHow[R][I] = W < 0 ? TEXT("SEED") : H.KOd >= 0 ? TEXT("KO") : TEXT("DEC");
	TourneyPend[0] = TourneyPend[1] = -1;
	TourneyDraws = 0;
	SaveTourney();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: tournament %s: %s beats %s (%s)"), RoundName[R], *TourneyWon[R][I], *(TourneyWon[R][I] == A ? B : A), *TourneyHow[R][I]);
}

FString ASlapHUD::TourneyStatus() const
{
	if (TourneyWon.Num() != TourneyRounds) return FString();
	const FString& Champ = TourneyWon.Last()[0];
	if (!Champ.IsEmpty()) return FString::Printf(TEXT("CHAMPION: %s   (Elo %.0f)"), *Champ.ToUpper(), EloNum(Champ));
	int32 Fighters = 0;
	for (const FString& Seed : TourneySeeds) Fighters += Seed != Bye;
	if (TourneyPend[0] >= 0)
		return FString::Printf(TEXT("%s%s: %s vs %s"), bTourney ? TEXT("") : TEXT("PAUSED  "), RoundName[TourneyPend[0]],
			*TourneyEntrant(TourneyPend[0], TourneyPend[1], 0).ToUpper(), *TourneyEntrant(TourneyPend[0], TourneyPend[1], 1).ToUpper());
	return FString::Printf(TEXT("%s%d fighters + %d BYEs, seeded by Elo. PLAY runs every match to a champion."), bTourney ? TEXT("Starting... ") : TEXT(""), Fighters, 16 - Fighters);
}
