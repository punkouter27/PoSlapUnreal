// ASlapHUD: the commentary - what is happening, who is winning, when it is over and who won. Display only: it reads the
// controller's HUD copy (counters that move once per event) and writes nothing back.

#include "SlapHUD.h"

#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Kismet/GameplayStatics.h"
#include "SlapHUDInternal.h"
#include "Sound/SoundBase.h"

using namespace SlapUi;

namespace
{
	const FColor Red(255, 80, 60), Amber(255, 170, 40), Green(110, 230, 130);
	constexpr double CaptionIn = 0.25;	// s: a caption's pop-in
}

FString ASlapHUD::Who(const FSlapHud& H, int32 a) const
{
	if (a < 0 || a > 1) return FString();
	if (!HereCard[a].IsEmpty()) return HereCard[a].ToUpper();
	return H.Name[a].IsEmpty() ? FString(a ? TEXT("BLUE") : TEXT("RED")) : H.Name[a].ToUpper();
}

void ASlapHUD::Announce(const FString& LineKey)
{
	if (LineKey.IsEmpty()) return;
	const FString Name = TEXT("S_A_") + LineKey;
	if (!AnnounceSounds.Contains(Name))
	{
		USoundBase* Line = LoadNamed<USoundBase>(TEXT("/Game/Audio/Announcer"), *Name);
		AnnounceSounds.Add(Name, Line);
		if (!Line) UE_LOG(LogSlap, Warning, TEXT("SlapHUD: no announcer line %s (Scripts/announcer.py, then voice_import.py)"), *Name);
	}
	if (AnnounceSounds[Name]) AnnounceQueue.Add(Name);
}

void ASlapHUD::AnnounceTick()
{
	// one line at a time, in real time (the slow motion stretches the world clock, not the PA)
	const double Real = FPlatformTime::Seconds();
	if (AnnounceQueue.IsEmpty() || Real < AnnounceFree) return;
	const FString Name = AnnounceQueue[0];
	AnnounceQueue.RemoveAt(0);
	USoundBase* Line = AnnounceSounds.FindRef(Name);
	if (!Line) return;
	UGameplayStatics::PlaySound2D(this, Line, 1.f);
	AnnounceFree = Real + FMath::Max(0.8, (double)Line->GetDuration() - 1.5);	// the next may start over the hall's tail
	SLAP_HUD_LOG(1, TEXT("SlapHUD: announcer %s"), *Name);
}

void ASlapHUD::Caption(const FString& Big, const FString& Small, FColor C, double Seconds)
{
	CapBig = Big;
	CapSmall = Small;
	CapColor = C;
	CapAt = GetWorld()->GetTimeSeconds();
	CapFor = Seconds;
}

void ASlapHUD::PlayByPlay(const FString& Line)
{
	// into the commentary box, wrapped at ~44 characters (as Scripts/commentator.py's lines are)
	Commentary.Reset();
	FString Cur;
	TArray<FString> Words;
	Line.ParseIntoArrayWS(Words);
	for (const FString& Wd : Words)
	{
		if (Cur.Len() + Wd.Len() > 44) { Commentary.Add(Cur); Cur.Reset(); }
		Cur += (Cur.IsEmpty() ? TEXT("") : TEXT(" ")) + Wd;
	}
	if (!Cur.IsEmpty()) Commentary.Add(Cur);
	CommentaryAt = GetWorld()->GetTimeSeconds();
}

FString ASlapHUD::WayKey(const FSlapHud& H) const
{
	if (H.Winner < 0) return FString();
	if (H.KOd >= 0 || H.Result.Contains(TEXT(" KO"))) return TEXT("ko");
	if (H.Result.Contains(TEXT("unanimous"))) return TEXT("ud");
	if (H.Result.Contains(TEXT("split"))) return TEXT("sd");
	if (H.Result.Contains(TEXT("majority"))) return TEXT("md");
	return TEXT("dec");
}

FString ASlapHUD::ResultPlain(const FSlapHud& H) const
{
	// the result as a person says it: who, how, when / on what cards (the controller's line stays in the log)
	if (H.Result.IsEmpty()) return FString();
	const int32 W = H.Winner;
	auto Cards = [&H](int32 First)
	{
		FString C;
		for (int32 j = 0; j < 3; ++j) C += FString::Printf(TEXT("%s%d-%d"), j ? TEXT("  ") : TEXT(""), H.Cards[j][First], H.Cards[j][1 - First]);
		return C;
	};
	if (W < 0)
	{
		if (H.Result.StartsWith(TEXT("No contest"))) return TEXT("No contest");
		return FString::Printf(TEXT("A DRAW after %d rounds   (%s)"), Slap::Rounds, *Cards(0));
	}
	const FString Way = WayKey(H);
	if (Way == TEXT("ko"))
	{
		const int32 Round = H.Result.Right(1).IsNumeric() ? FCString::Atoi(*H.Result.Right(1)) : H.Round;	// "... by KO, round 2"
		return FString::Printf(TEXT("%s wins by KNOCKOUT in round %d"), *Who(H, W), Round);
	}
	const TCHAR* How = Way == TEXT("ud") ? TEXT("UNANIMOUS DECISION") : Way == TEXT("sd") ? TEXT("SPLIT DECISION") : Way == TEXT("md") ? TEXT("MAJORITY DECISION") : TEXT("DECISION");
	return FString::Printf(TEXT("%s wins by %s   (%s)"), *Who(H, W), How, *Cards(W));
}

FString ASlapHUD::SummaryText() const
{
	const FSlapHud& H = Last;
	FString Turning;
	if (FirstDownWho >= 0) Turning = FString::Printf(TEXT("TURNING POINT: %s knocked down in round %d"), *Who(H, FirstDownWho), FirstDownRound);
	else if (BigRoundWho >= 0 && BigRoundMargin > 0) Turning = FString::Printf(TEXT("TURNING POINT: %s took round %d by %d on the cards"), *Who(H, BigRoundWho), BigRound, BigRoundMargin);
	else Turning = TEXT("TURNING POINT: none - even all the way");
	const int32 F = PeakSpeed[1] > PeakSpeed[0] ? 1 : 0;
	const FString Fast = PeakSpeed[F] > 0.0 ? FString::Printf(TEXT("FASTEST SLAP: %s %.1f m/s"), *Who(H, F), PeakSpeed[F]) : FString(TEXT("FASTEST SLAP: none landed"));
	const FString Acc = FString::Printf(TEXT("LANDED: %s %d of %d   %s %d of %d"), *Who(H, 0), Landed[0], Attempts[0], *Who(H, 1), Landed[1], Attempts[1]);
	return Turning + TEXT("\n") + Fast + TEXT("\n") + Acc;
}

void ASlapHUD::Commentate(const FSlapHud& H, double Now)
{
	// a new match: everything per match starts over; the introductions are announced with their cards
	if (H.MatchSeq != CmMatch)
	{
		CmMatch = H.MatchSeq;
		CmRound = CmScored = 0;
		CmStrike = H.StrikeSeq;
		CmTurn = H.TurnSeq;
		CmKO = H.KOSeq;
		CmDowns[0] = CmDowns[1] = 0;
		CmTurnKey.Reset();
		CmResult.Reset();
		Attempts[0] = Attempts[1] = Landed[0] = Landed[1] = 0;
		FirstDownRound = BigRound = BigRoundMargin = 0;
		FirstDownWho = BigRoundWho = -1;
		bCmTrouble[0] = bCmTrouble[1] = bCmOneMore[0] = bCmOneMore[1] = false;
		ScoreStrip.Reset();
		AnnounceQueue.Reset();
		if (H.Result.IsEmpty())
		{
			Announce(TEXT("red_") + HereCard[0]);
			Announce(TEXT("blue_") + HereCard[1]);
		}
	}
	// a knockdown (not a KO): the caption, the call, the turning point
	for (int32 a = 0; a < 2; ++a)
	{
		if (H.Downs[a] > CmDowns[a] && H.KOd != a)
		{
			Caption(TEXT("KNOCKDOWN!"), FString::Printf(TEXT("%s IS DOWN"), *Who(H, a)), Red, 2.5);
			Announce(TEXT("knockdown"));
			PlayByPlay(FString::Printf(TEXT("Down goes %s! That's knockdown number %d."), *Who(H, a), H.Downs[a]));
			if (FirstDownWho < 0) { FirstDownWho = a; FirstDownRound = H.Round; }
		}
		CmDowns[a] = H.Downs[a];
	}
	// the knockout
	if (H.KOSeq != CmKO)
	{
		CmKO = H.KOSeq;
		if (H.KOd >= 0)
		{
			Caption(TEXT("KNOCKOUT!"), FString::Printf(TEXT("%s IS OUT"), *Who(H, H.KOd)), Red, 2.5);
			Announce(TEXT("knockout"));
			if (FirstDownWho < 0) { FirstDownWho = H.KOd; FirstDownRound = H.Round; }
		}
	}
	if (!H.Result.IsEmpty() && H.Result != CmResult)	// the end: the bell, the call, the winner
	{
		CmResult = H.Result;
		const FString Way = WayKey(H);
		if (H.Winner < 0)
		{
			if (Bell) UGameplayStatics::PlaySound2D(this, Bell);
			Announce(TEXT("finalbell"));
			Announce(TEXT("draw"));
			Caption(TEXT("IT'S A DRAW"), ResultPlain(H), Gold, 5.0);
		}
		else
		{
			if (Way != TEXT("ko"))	// a KO already rang the bell and called it
			{
				if (Bell) UGameplayStatics::PlaySound2D(this, Bell);
				if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, 1.2f);
				Announce(TEXT("finalbell"));
			}
			Announce(FString::Printf(TEXT("win_%s_%s"), *Way, *HereCard[H.Winner]));
			if (Way != TEXT("ko")) Caption(FString::Printf(TEXT("%s WINS!"), *Who(H, H.Winner)), ResultPlain(H), TeamColor[H.Winner], 3.5);
		}
		PlayByPlay(TEXT("It's over! ") + ResultPlain(H) + TEXT("."));
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: commentary: %s"), *ResultPlain(H));
		return;
	}
	if (!H.Result.IsEmpty() || H.bWalkIn) return;

	// a new round: the call and the caption
	if (H.Round != CmRound && H.Round > 0)
	{
		CmRound = H.Round;
		const bool bFinal = H.Round >= Slap::Rounds;
		Caption(bFinal ? FString(TEXT("FINAL ROUND")) : FString::Printf(TEXT("ROUND %d"), H.Round),
			FString::Printf(TEXT("%s STRIKES FIRST"), *Who(H, Team(H.Striker))), Gold, 2.2);
		Announce(FString::Printf(TEXT("round%d"), FMath::Clamp(H.Round, 1, 3)));
		bCmTrouble[0] = bCmTrouble[1] = false;	// a fresh danger call each round
	}
	// a new turn: who strikes, who defends
	const FString TurnKey = FString::Printf(TEXT("%d/%d/%s"), H.MatchSeq, H.Round, *H.Striker);
	if (TurnKey != CmTurnKey)
	{
		CmTurnKey = TurnKey;
		const int32 Sk = Team(H.Striker);
		CmTurnDef = 1 - Sk;
		CmTurnStrike = H.StrikeSeq;
		++Attempts[Sk];
		TurnBanner = FString::Printf(TEXT("%s STRIKES   >   %s DEFENDS"), *Who(H, Sk), *Who(H, 1 - Sk));
		TurnBannerColor = TeamColor[Sk];
		TurnBannerAt = Now;
	}
	// a strike: the play-by-play by its speed and what it did
	if (H.StrikeSeq != CmStrike)
	{
		CmStrike = H.StrikeSeq;
		const int32 D = FMath::Clamp(H.StrikeDefender, 0, 1), Sk = 1 - D;
		++Landed[Sk];
		const TCHAR* Call = H.Speed >= CleanSpeed * 1.15 ? TEXT("HUGE SLAP!") : H.Speed >= CleanSpeed ? TEXT("Clean hit!") : TEXT("Glancing blow.");
		FString Part = H.Part.Replace(TEXT("_r"), TEXT("")).Replace(TEXT("_l"), TEXT("")).ToLower();
		PlayByPlay(FString::Printf(TEXT("%s %s lands at %.1f m/s with the %s - %s takes it on the cheek."), Call, *Who(H, Sk), H.Speed, *Part, *Who(H, D)));
	}
	// the turn ended without a touch: the defender slipped it
	if (H.TurnSeq != CmTurn)
	{
		CmTurn = H.TurnSeq;
		if (H.StrikeSeq == CmTurnStrike && H.KOd < 0) PlayByPlay(FString::Printf(TEXT("%s holds back - no contact. %s stays safe."), *Who(H, 1 - CmTurnDef), *Who(H, CmTurnDef)));
	}
	// danger: once a round "<name> is in trouble", once a match "one more and it's over" (the neck near its floor)
	for (int32 a = 0; a < 2; ++a)
	{
		const double Neck = NeckLeft(H.Neck[a]);
		if (!bCmOneMore[a] && Neck < 0.15 && H.Round > 0)
		{
			bCmOneMore[a] = bCmTrouble[a] = true;
			Caption(TEXT("ONE MORE AND IT'S OVER"), FString::Printf(TEXT("%s'S NECK IS ALMOST GONE"), *Who(H, a)), Amber, 2.5);
			Announce(TEXT("onemore"));
		}
		else if (!bCmTrouble[a] && Struck == a && Now - StrikeAt > 0.3 && Now - StrikeAt < 3.0 && (DangerOf(H, a) > 0.75 || Neck < 0.35))	// just hit
		{
			bCmTrouble[a] = true;
			Caption(FString::Printf(TEXT("%s IN TROUBLE"), *Who(H, a)), Neck < 0.35 ? FString(TEXT("THE NECK IS GOING")) : FString(TEXT("BARELY ON THE FEET")), Amber, 2.2);
			Announce(TEXT("trouble_") + HereCard[a]);
		}
	}
	// a round scored: the judges' running totals, in words
	if (H.ScoredRound != CmScored && H.ScoredRound > 0)
	{
		CmScored = H.ScoredRound;
		int32 ForR = 0, ForB = 0, RoundR = 0, RoundB = 0;
		for (int32 j = 0; j < 3; ++j)
		{
			ForR += H.Cards[j][0] > H.Cards[j][1];
			ForB += H.Cards[j][1] > H.Cards[j][0];
			RoundR += H.RoundCards[j][0];
			RoundB += H.RoundCards[j][1];
		}
		const int32 Lead = ForR > ForB ? 0 : ForB > ForR ? 1 : -1;
		const FString Totals = FString::Printf(TEXT("%d-%d  %d-%d  %d-%d"), H.Cards[0][0], H.Cards[0][1], H.Cards[1][0], H.Cards[1][1], H.Cards[2][0], H.Cards[2][1]);
		if (Lead < 0) ScoreStrip = FString::Printf(TEXT("AFTER ROUND %d: EVEN ON THE CARDS  (%s)"), H.ScoredRound, *Totals);
		else ScoreStrip = FString::Printf(TEXT("AFTER ROUND %d: %s LEADS ON %s  (%s)"), H.ScoredRound, *Who(H, Lead),
			(Lead ? ForB : ForR) == 3 ? TEXT("ALL THREE CARDS") : TEXT("TWO CARDS"), *Totals);
		ScoreStripAt = Now;
		const int32 Margin = FMath::Abs(RoundR - RoundB);
		if (Margin > BigRoundMargin) { BigRoundMargin = Margin; BigRound = H.ScoredRound; BigRoundWho = RoundR > RoundB ? 0 : 1; }
		PlayByPlay(ScoreStrip.Replace(TEXT("  ("), TEXT(" - cards ")).Replace(TEXT(")"), TEXT(".")));
	}
}

void ASlapHUD::DrawCaptions(const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	const float Cx = Canvas->ClipX * 0.5f;
	// the turn banner: who strikes, who defends (2.5 s at each turn's start, under the scoreboard)
	if (H.Result.IsEmpty() && !H.bWalkIn && Now - TurnBannerAt < 2.5 && !TurnBanner.IsEmpty())
	{
		const float Y = Canvas->ClipY * 0.36f, W = Canvas->ClipX - 60.f * S, Hb = 30.f * S;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.78f), Cx - W * 0.5f, Y, W, Hb);
		DrawRect(FLinearColor(TurnBannerColor), Cx - W * 0.5f, Y, W, 3.f * S);
		TextBox(TurnBanner, Cx, Y + 7.f * S, W - 16.f * S, TurnBannerColor, 1.15f * S, true);
		TrackBox(TEXT("TurnBanner"), Cx - W * 0.5f, Y, W, Hb);
	}
	// the judges' running score after each round, until the next round's first strike (12 s at most)
	if (H.Result.IsEmpty() && !ScoreStrip.IsEmpty() && Now - ScoreStripAt < 12.0 && Now - ScoreStripAt > 5.0)	// after the judges' panel
	{
		const float Y = PanelBottom + 4.f * S, W = Canvas->ClipX - 16.f * S, Hb = 24.f * S;
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.8f), 8.f * S, Y, W, Hb);
		DrawRect(FLinearColor(Gold), 8.f * S, Y, 4.f * S, Hb);
		TextBox(ScoreStrip, Cx, Y + 5.f * S, W - 20.f * S, Gold, 0.95f * S, true);
		TrackBox(TEXT("ScoreStrip"), 8.f * S, Y, W, Hb);
	}
	// the big caption: pops in, holds, fades
	const double T = Now - CapAt;
	if (T >= 0.0 && T < CapFor && !CapBig.IsEmpty())
	{
		const float Pop = (float)FMath::Clamp(T / CaptionIn, 0.0, 1.0), Fade = (float)FMath::Clamp((CapFor - T) / 0.4, 0.0, 1.0);
		const float Scale = (2.3f - 0.5f * Pop) * S;
		const float Y = Canvas->ClipY * (H.Result.IsEmpty() ? 0.42f : 0.27f), Hb = (CapSmall.IsEmpty() ? 64.f : 92.f) * S;	// above the result card
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.72f * Fade), 0.f, Y, Canvas->ClipX, Hb);
		DrawRect(FLinearColor(CapColor.R / 255.f, CapColor.G / 255.f, CapColor.B / 255.f, Fade), 0.f, Y, Canvas->ClipX, 3.f * S);
		DrawRect(FLinearColor(CapColor.R / 255.f, CapColor.G / 255.f, CapColor.B / 255.f, Fade), 0.f, Y + Hb - 3.f * S, Canvas->ClipX, 3.f * S);
		FColor Big = CapColor;
		Big.A = (uint8)(255 * Fade);
		TextBox(CapBig, Cx, Y + 8.f * S, Canvas->ClipX - 30.f * S, Big, Scale, true, GEngine->GetLargeFont());
		if (!CapSmall.IsEmpty())
		{
			FColor Small = FColor::White;
			Small.A = (uint8)(255 * Fade);
			TextBox(CapSmall, Cx, Y + 60.f * S, Canvas->ClipX - 30.f * S, Small, 1.0f * S, true);
		}
		TrackBox(TEXT("Caption"), 0.f, Y, Canvas->ClipX, Hb);
	}
}
