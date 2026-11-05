// ASlapHUD: the display pass of 2026-11-04 (user's approved list, the Unreal stream; Web/ mirrors it). Display only: it
// reads MuJoCo's render snapshot (body positions, actuator forces) and the HUD copy of the match, and writes nothing back -
// no physics, no brain input, no match event, no timing. -SlapDisplayPass=0 (slap.DisplayPass 0) turns every piece off.
//
// - the danger meter: the capture point (USlapController::Wobble) combined with the neck damage taken (the fatigue model:
//   neck strength drops by neck_damage_per_snap x head snap per strike absorbed)
// - FLINCH: the defender's head > 5 cm off where it stood before the contact (training/envs/arena.py's own flinch rule)
// - the per-strike instant replay at the turn's end, captioned (palm peak, head snap), skippable (a tap, SPACE)
// - the scouting card between rounds: Elo, KO rate, the favourite slap side
// - joint strain (TAB): shoulder / elbow / wrist |actuator force| / its force cap, as heat on the joints
// - the one-line event ticker above the bottom row
// The name plates' clock ring and health chips live in SlapHUDDraw.cpp; the crowd's impulse in SlapHUDFx/Audio.cpp.

#include "SlapHUD.h"

#include "Components/PoseableMeshComponent.h"
#include "Async/Async.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "MuJoCo/Core/AMjManager.h"
#include "MuJoCo/Core/MjPhysicsEngine.h"
#include "MuJoCo/Core/MjRenderSnapshot.h"
#include "MuJoCo/Utils/MjUtils.h"
#include "SlapAthleteVisual.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"

using namespace SlapUi;

namespace
{
	TAutoConsoleVariable<int32> CVarHudLog(TEXT("slap.HudLog"), 1,
		TEXT("PoSlap HUD log: 0 warnings only, 1 one line per event (default), 2 + per-frame diagnostics. -SlapHudLog=<n>."));
	TAutoConsoleVariable<int32> CVarDisplayPass(TEXT("slap.DisplayPass"), 1,
		TEXT("PoSlap 2026-11-04 display pass (danger x neck, FLINCH, strike replays, scouting, strain, ticker, ring, chips): 1 on, 0 off. -SlapDisplayPass=0."));

	constexpr double FlinchLimitCm = 5.0;			// cm of head travel before the contact (arena.py: hdev > 0.05 m)
	constexpr double FlinchSettle = 0.4;		// s after a turn starts before the head's rest spot is taken (the reset)
	constexpr double FlinchSwing = 1.5;			// m/s: the striker's palm is moving (a flinch is a reaction to a swing)
	constexpr double NeckWeight = 0.6;			// a neck at its floor alone reads 60 % danger (amber), the capture point does the rest
	constexpr double ScoutSeconds = 6.0;
	constexpr double TickerFade = 10.0;			// s: an old ticker line dims
	const FColor Amber(250, 170, 60), Green(120, 230, 140), Red(250, 80, 60), Violet(190, 120, 255);
	const TCHAR* const JointBone[6] = { TEXT("upperarm_l"), TEXT("forearm_l"), TEXT("hand_l"), TEXT("upperarm_r"), TEXT("forearm_r"), TEXT("hand_r") };
	const TCHAR* const JointActNames[3][3] = { { TEXT("shoulder_swing"), TEXT("shoulder_raise"), TEXT("shoulder_twist") }, { TEXT("elbow"), nullptr, nullptr }, { TEXT("wrist_flex"), TEXT("wrist_dev"), nullptr } };
	FString Who(const FSlapHud& H, int32 a) { return H.Name[a].IsEmpty() ? (a ? TEXT("BLUE") : TEXT("RED")) : H.Name[a].ToUpper(); }
	FColor HeatColour(float K)	// green -> amber -> red
	{
		const FLinearColor C = K < 0.5f ? FMath::Lerp(FLinearColor(0.25f, 0.85f, 0.35f), FLinearColor(1.f, 0.75f, 0.2f), K * 2.f)
			: FMath::Lerp(FLinearColor(1.f, 0.75f, 0.2f), FLinearColor(1.f, 0.2f, 0.15f), FMath::Min(1.f, (K - 0.5f) * 2.f));
		return C.ToFColor(true);
	}
}

int32 SlapUi::HudLogLevel() { return CVarHudLog.GetValueOnAnyThread(); }
bool SlapUi::DisplayPassOn() { return CVarDisplayPass.GetValueOnAnyThread() != 0; }

double ASlapHUD::CombineDanger(double Capture, double NeckLeftShare)
{
	// either one alone can say "in danger": 1 - (1 - capture) x (1 - w x neck damage). Fresh neck: the capture point as it was.
	const double C = FMath::Clamp(Capture, 0.0, 1.0), N = 1.0 - FMath::Clamp(NeckLeftShare, 0.0, 1.0);
	return 1.0 - (1.0 - C) * (1.0 - NeckWeight * N);
}

double ASlapHUD::DangerOf(const FSlapHud& H, int32 a) const
{
	return bPass ? CombineDanger(H.Danger[a], NeckLeft(H.Neck[a])) : FMath::Clamp(H.Danger[a], 0.0, 1.0);
}

void ASlapHUD::BindSense()
{
	AAMjManager* Mgr = AAMjManager::GetManager();
	const mjModel* m = Mgr && Mgr->PhysicsEngine ? Mgr->PhysicsEngine->GetModel() : nullptr;
	if (!m) return;
	SenseActs = 0;
	for (int32 a = 0; a < 2; ++a)
	{
		const FString P = a ? TEXT("blue_") : TEXT("red_");
		HeadBody[a] = Slap::Find(m, mjOBJ_BODY, m->nbody, P + TEXT("head"));
		PelvisBody[a] = Slap::Find(m, mjOBJ_BODY, m->nbody, P + TEXT("pelvis"));
		for (int32 j = 0; j < NumJoints; ++j)
		{
			JointBody[a][j] = Slap::Find(m, mjOBJ_BODY, m->nbody, P + JointBone[j]);
			JointActs[a][j].Reset();
			const TCHAR* Side = j < JShoulderR ? TEXT("_l") : TEXT("_r");
			for (const TCHAR* N : JointActNames[j % 3])
			{
				if (!N) continue;
				const int32 Id = Slap::Find(m, mjOBJ_ACTUATOR, m->nu, P + N + Side);
				if (Id < 0) continue;
				const double Cap = m->actuator_forcelimited[Id] ? FMath::Max(FMath::Abs(m->actuator_forcerange[2 * Id]), FMath::Abs(m->actuator_forcerange[2 * Id + 1])) : 0.0;
				if (Cap <= 0.0) continue;	// no cap, no strain to show
				JointActs[a][j].Emplace(Id, Cap);
				++SenseActs;
			}
		}
	}
	bSenseBound = HeadBody[0] >= 0 && HeadBody[1] >= 0;
	if (bSenseBound) SLAP_HUD_LOG(1, TEXT("SlapHUD: display pass: heads %d/%d, %d arm actuators with a force cap (strain heat)"), HeadBody[0], HeadBody[1], SenseActs);
}

void ASlapHUD::Sense(const FSlapHud& H)
{
	if (!bPass) return;
	if (!bSenseBound) BindSense();
	const double Now = GetWorld()->GetTimeSeconds();
	const float Dt = GetWorld()->GetDeltaSeconds();
	float Raw[2][NumJoints] = {};
	bool bFresh = false;
	AAMjManager* Mgr = AAMjManager::GetManager();
	if (bSenseBound && Mgr && Mgr->PhysicsEngine)
	{
		Mgr->PhysicsEngine->WithRenderState([&](const FMjRenderSnapshot& Snap)
		{
			if (Snap.XPos.Num() == 0) return;
			bFresh = Snap.FrameId != SenseFrame;
			SenseFrame = Snap.FrameId;
			auto At = [&](int32 b) { return b >= 0 && 3 * b + 2 < Snap.XPos.Num() ? MjUtils::MjToUEPosition(&Snap.XPos[3 * b]) : FVector::ZeroVector; };
			for (int32 a = 0; a < 2; ++a)
			{
				HeadNow[a] = At(HeadBody[a]);
				PelvisNow[a] = At(PelvisBody[a]);
				for (int32 j = 0; j < NumJoints; ++j)
				{
					JointNow[a][j] = At(JointBody[a][j]);
					for (const TPair<int32, double>& X : JointActs[a][j])
						if (X.Key < Snap.ActuatorForce.Num()) Raw[a][j] = FMath::Max(Raw[a][j], (float)(FMath::Abs(Snap.ActuatorForce[X.Key]) / X.Value));
				}
			}
		});
	}
	for (int32 a = 0; a < 2; ++a)	// the heat jumps up with the load and cools over ~0.7 s
		for (int32 j = 0; j < NumJoints; ++j)
		{
			if (bFresh) Strain[a][j] = FMath::Max(FMath::Min(Raw[a][j], 1.f), Strain[a][j] - 1.5f * Dt);
			PeakStrain[a][j] = FMath::Max(PeakStrain[a][j], Strain[a][j]);
		}

	// the flinch: the defender's head moved more than 5 cm from where it stood, while the striker swings and before contact
	const int32 TurnKey = H.Match * 16 + H.Round * 2 + Team(H.Striker);
	if (TurnKey != FlinchTurnKey)
	{
		FlinchTurnKey = TurnKey;
		FlinchTurnAt = Now;
		bHead0 = bFlinched = bFlinchFeint = false;
		FMemory::Memzero(PeakStrain, sizeof(PeakStrain));
	}
	const int32 Def = Team(H.Defender);
	const bool bOpen = H.Match > 0 && !H.bWalkIn && H.Result.IsEmpty() && !H.bStruck && H.Clock > 0.05 && bSenseBound;
	if (bOpen && !bHead0 && Now - FlinchTurnAt >= FlinchSettle)
	{
		Head0 = HeadNow[Def];
		bHead0 = true;
		++FlinchArmed;
	}
	if (bOpen && bHead0 && !bFlinched)
	{
		FlinchCm = (float)FVector::Dist2D(HeadNow[Def], Head0);
		const bool bFeintNear = Read.Tag == TEXT("FEINT") && Now - Read.TagAt < 1.0;
		// a head that moves because the whole body is going over is a fall, not a flinch: his capture point stays inside his soles
		if (FlinchCm > FlinchLimitCm && (H.HandSpeed > FlinchSwing || bFeintNear) && H.Danger[Def] < 0.5)
		{
			bFlinched = true;
			FlinchAt = Now;
			++Flinches[Def];
			bFlinchFeint = bFeintNear;
			Ticker(FString::Printf(TEXT("FLINCH: %s's head moved %.0f cm before any contact%s"), *Who(H, Def), FlinchCm, bFlinchFeint ? TEXT(" - the feint drew it") : TEXT("")), Violet);
		}
		SLAP_HUD_LOG(2, TEXT("SlapHUD: flinch watch: %s head %.1f cm off, palm %.1f m/s"), *H.Defender, FlinchCm, H.HandSpeed);
	}

	// the ticker's events the HUD sees change (the strike and the feint add theirs where they fire)
	if (H.DownSeq != TickerSeenDown)
	{
		TickerSeenDown = H.DownSeq;
		// who went down: the athlete whose knockdown count moved (a striker who falls is a foul, and is not counted)
		int32 Down = H.Downs[Def] != TickerDowns[Def] ? Def : H.Downs[1 - Def] != TickerDowns[1 - Def] ? 1 - Def : Team(H.Striker);
		if (H.DownSeq > 0) Ticker(Down == Def ? FString::Printf(TEXT("KNOCKDOWN: %s is down (%d this match)"), *Who(H, Def), H.Downs[Def])
			: FString::Printf(TEXT("DOWN: the striker %s fell"), *Who(H, Down)), Red);
	}
	TickerDowns[0] = H.Downs[0];
	TickerDowns[1] = H.Downs[1];
	if (H.TurnSeq != TickerSeenTurn)
	{
		TickerSeenTurn = H.TurnSeq;
		if (H.TurnSeq > 0 && !H.Event.IsEmpty()) Ticker(FString::Printf(TEXT("TURN %s: %s"), *H.Striker.ToUpper(), *H.Event), Grey);
	}
	if (H.ScoredRound != TickerSeenRound)
	{
		TickerSeenRound = H.ScoredRound;
		if (H.ScoredRound > 0)
		{
			int32 R = 0, B = 0;
			for (int32 j = 0; j < 3; ++j) { R += H.RoundCards[j][0]; B += H.RoundCards[j][1]; }
			Ticker(FString::Printf(TEXT("ROUND %d SCORED  %d - %d  (cards %d:%d %d:%d %d:%d)"), H.ScoredRound, R, B, H.RoundCards[0][0], H.RoundCards[0][1], H.RoundCards[1][0], H.RoundCards[1][1], H.RoundCards[2][0], H.RoundCards[2][1]), Gold);
			if (H.ScoredRound < Rounds && H.Result.IsEmpty()) { ScoutAt = Now; ScoutRound = H.ScoredRound; ++ScoutRoundsSeen; }
		}
	}
	if (H.Result != TickerSeenResult)
	{
		TickerSeenResult = H.Result;
		if (!H.Result.IsEmpty()) Ticker(TEXT("RESULT: ") + H.Result, Gold);
	}
}

void ASlapHUD::Ticker(const FString& Line, FColor C)
{
	TickerLine = Line;
	TickerColor = C;
	TickerAt = GetWorld()->GetTimeSeconds();
	++TickerCount;
	SLAP_HUD_LOG(1, TEXT("SlapHUD: ticker: %s"), *Line);
}

void ASlapHUD::DrawTicker(const FSlapHud& H)
{
	if (!bPass || TickerAt < 0.0) return;
	const double Age = GetWorld()->GetTimeSeconds() - TickerAt;
	const float X = 8.f * S, W = Canvas->ClipX - 16.f * S, Hh = 16.f * S, Y = TickerY;
	const float A = Age < TickerFade ? 1.f : 0.55f;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.62f), X, Y, W, Hh);
	DrawRect(FLinearColor(TickerColor).CopyWithNewOpacity(A), X, Y, 3.f * S, Hh);
	const FString Stamp = Age < 60.0 ? FString::Printf(TEXT("%2.0fs"), Age) : TEXT("1m+");
	Text(Stamp, X + W - 26.f * S, Y + 2.f * S, Grey, 0.75f * S);
	FColor C = TickerColor;
	C.A = (uint8)(255 * A);
	TextBox(TickerLine, X + 8.f * S, Y + 1.f * S, W - 40.f * S, C, 0.9f * S);
	TrackBox(TEXT("Ticker"), X, Y, W, Hh);
}

void ASlapHUD::DrawFlinch(const FSlapHud& H)
{
	if (!bPass || FlinchAt < 0.0) return;
	const double Since = GetWorld()->GetTimeSeconds() - FlinchAt;
	if (Since > FSlapSwingRead::TagSeconds) return;
	const int32 Def = Team(H.Defender);
	if (!Body[Def].IsValid()) return;
	const FVector P = Canvas->Project(Body[Def]->GetBoneLocation(BoneHead) + FVector(0.f, 0.f, 30.f));
	if (P.Z <= 0) return;
	const float Pop = 1.f + 0.25f * FMath::Max(0.f, 1.f - (float)Since / 0.2f);
	Text(bFlinchFeint ? TEXT("FLINCH - FEINT DREW IT") : TEXT("FLINCH"), P.X, P.Y, Violet, 1.1f * S * Pop, true, GEngine->GetMediumFont());
	Text(FString::Printf(TEXT("head %.0f cm before contact"), FlinchCm), P.X, P.Y + 20.f * S, Grey, 0.8f * S, true);
	TrackBox(TEXT("OverlayFlinch"), P.X - 60.f * S, P.Y, 120.f * S, 32.f * S);
}

void ASlapHUD::DrawStrain(const FSlapHud& H)
{
	// TAB only: heat on each shoulder, elbow and wrist - the motors' |force| over the card's cap (Blueprint 2.3). Drawn on the
	// live joints, so it hides during a replay (the drawn pose is an old one then).
	if (!bPass || !bSenseBound || SenseActs == 0 || GetWorld()->GetTimeSeconds() < ReplayEnd) return;
	for (int32 a = 0; a < 2; ++a)
		for (int32 j = 0; j < NumJoints; ++j)
		{
			if (JointActs[a][j].Num() == 0) continue;
			const FVector P = Canvas->Project(JointNow[a][j]);
			if (P.Z <= 0) continue;
			const float K = FMath::Clamp(Strain[a][j], 0.f, 1.f);
			const float R = (3.f + 7.f * K) * S;
			DrawRect(FLinearColor(HeatColour(K)).CopyWithNewOpacity(0.35f + 0.55f * K), P.X - R, P.Y - R, 2.f * R, 2.f * R);
			if (K > 0.9f) Ring(this, P, R + 3.f * S, 16, FLinearColor(1.f, 0.2f, 0.15f, 0.9f), 1.5f * S);
		}
	// the striker's peak this turn, per joint (the worse of the two arms)
	const int32 Sk = Team(H.Striker);
	const float FX = 290.f * S, BoxW = Canvas->ClipX - FX - 52.f * S, Y = Bot - 34.f * S - 18.f * S - 6.f * S - 30.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), FX - 6.f * S, Y, BoxW, 28.f * S);
	TextBox(FString::Printf(TEXT("JOINT STRAIN %s (turn peak / cap)"), *H.Striker.ToUpper()), FX, Y + 1.f * S, BoxW - 12.f * S, Grey, 0.7f * S);
	static const TCHAR* Short[3] = { TEXT("SHLD"), TEXT("ELBW"), TEXT("WRST") };
	for (int32 k = 0; k < 3; ++k)
	{
		const float Pk = FMath::Max(PeakStrain[Sk][k], PeakStrain[Sk][k + 3]);
		Text(FString::Printf(TEXT("%s %d%%"), Short[k], FMath::RoundToInt(100.f * Pk)), FX + k * (BoxW - 12.f * S) / 3.f, Y + 13.f * S, HeatColour(Pk), 0.8f * S);
	}
	TrackBox(TEXT("Strain"), FX - 6.f * S, Y, BoxW, 28.f * S);
	++StrainDrawn;
}

void ASlapHUD::LoadScoutLogs()
{
	// Unreal's own match logs (Logs/Gameplay/<YYMMDD>/*_ue.jsonl, ~5 MB): per character, which hand landed (left / right) and
	// how many landed strikes ended in a KO. The striker's character is his brain's prefix ("grandpa_final_vs_kim"). Read on a
	// worker thread (a few seconds on a loaded machine): the card shows "-" until it is in.
	const FString Root = FPaths::ProjectDir() / TEXT("Logs/Gameplay");
	TWeakObjectPtr<ASlapHUD> Self(this);
	Async(EAsyncExecution::ThreadPool, [Self, Root]
	{
		TMap<FString, FIntPoint> LogSides, LogKOs;
		TArray<FString> Days;
		IFileManager::Get().FindFiles(Days, *(Root / TEXT("*")), false, true);
		int32 Files = 0;
		const double T0 = FPlatformTime::Seconds();
		for (const FString& Day : Days)
		{
			TArray<FString> Logs;
			IFileManager::Get().FindFiles(Logs, *(Root / Day / TEXT("*_ue.jsonl")), true, false);
			for (const FString& L : Logs)
			{
				TArray<FString> Lines;
				if (!FFileHelper::LoadFileToStringArray(Lines, *(Root / Day / L))) continue;
				++Files;
				FString Card[2];
				for (const FString& Line : Lines)
				{
					if (Line.StartsWith(TEXT("{\"type\": \"frame\""))) continue;
					auto Field = [&](const TCHAR* Key) -> FString	// "key": "value"
					{
						const FString Tag = FString::Printf(TEXT("\"%s\": \""), Key);
						const int32 I = Line.Find(Tag);
						if (I < 0) return FString();
						const int32 B = I + Tag.Len(), E = Line.Find(TEXT("\""), ESearchCase::CaseSensitive, ESearchDir::FromStart, B);
						return E > B ? Line.Mid(B, E - B) : FString();
					};
					if (Line.StartsWith(TEXT("{\"type\": \"match_start\"")))
					{
						const int32 Bi = Line.Find(TEXT("\"brains\""));
						const FString BrainPart = Bi >= 0 ? Line.Mid(Bi) : FString();
						for (int32 a = 0; a < 2; ++a)
						{
							const FString Tag = a ? TEXT("\"blue\": \"") : TEXT("\"red\": \"");
							const int32 I = BrainPart.Find(Tag);
							FString Name = I >= 0 ? BrainPart.Mid(I + Tag.Len()) : FString();
							Name.Split(TEXT("\""), &Name, nullptr);
							Name.Split(TEXT("_"), &Name, nullptr);
							Card[a] = Name.ToLower();
						}
					}
					else if (Line.StartsWith(TEXT("{\"type\": \"strike\"")))
					{
						const FString& C = Card[Team(Field(TEXT("striker")))];
						const FString Part = Field(TEXT("part"));
						if (C.IsEmpty() || Part.IsEmpty()) continue;
						FIntPoint& Sd = LogSides.FindOrAdd(C);
						(Part.EndsWith(TEXT("_l")) ? Sd.X : Sd.Y) += Part.EndsWith(TEXT("_l")) || Part.EndsWith(TEXT("_r")) ? 1 : 0;
					}
					else if (Line.StartsWith(TEXT("{\"type\": \"strike_result\"")))
					{
						const FString& C = Card[Team(Field(TEXT("striker")))];
						if (C.IsEmpty()) continue;
						FIntPoint& K = LogKOs.FindOrAdd(C);
						K.X += Line.Contains(TEXT("\"ko\": true")) ? 1 : 0;
						++K.Y;
					}
				}
			}
		}
		const double Took = FPlatformTime::Seconds() - T0;
		AsyncTask(ENamedThreads::GameThread, [Self, LogSides = MoveTemp(LogSides), LogKOs = MoveTemp(LogKOs), Files, Took]() mutable
		{
			if (!Self.IsValid()) return;
			Self->LogSides = MoveTemp(LogSides);
			Self->LogKOs = MoveTemp(LogKOs);
			SLAP_HUD_LOG(1, TEXT("SlapHUD: scouting: %d Unreal match logs read in %.1f s (worker thread), %d characters with slap sides"), Files, Took, Self->LogSides.Num());
		});
	});
}

FString ASlapHUD::ScoutLine(int32 a) const
{
	const FSlapHud& H = Last;
	const FString& Card = H.Card[a];
	FString Line = EloOf(Card);	// the character's own (Saved/PoSlap/elo.json)
	if (const double* BrainElo = LeagueElo.Find(H.Brain[a])) Line += FString::Printf(TEXT("  brain %.0f"), *BrainElo);	// training/models/league.json
	// KO rate: the league's (per brain, every logged match) or Unreal's own logs (per character)
	if (const double* KO = LeagueKO.Find(H.Brain[a])) Line += FString::Printf(TEXT("   KO %.0f%%"), 100.0 * *KO);
	else if (const FIntPoint* K = LogKOs.Find(Card); K && K->Y > 0) Line += FString::Printf(TEXT("   KO %.0f%% of %d"), 100.0 * K->X / K->Y, K->Y);
	else Line += TEXT("   KO -");
	// the favourite slap side: the logs plus this session's touches
	FIntPoint Sd = LogSides.FindRef(Card);
	Sd.X += SideHits[a][0];
	Sd.Y += SideHits[a][1];
	const int32 N = Sd.X + Sd.Y;
	if (N > 0)
	{
		const bool bRight = Sd.Y >= Sd.X;
		Line += FString::Printf(TEXT("   favours %s %d%%"), bRight ? TEXT("RIGHT") : TEXT("LEFT"), FMath::RoundToInt(100.0 * (bRight ? Sd.Y : Sd.X) / N));
	}
	else Line += TEXT("   side -");
	return Line;
}

void ASlapHUD::DrawScouting(const FSlapHud& H)
{
	// between rounds (after round 1 and 2 are scored, until the next strike or 6 s): one row per corner
	const double Now = GetWorld()->GetTimeSeconds();
	// gone once the next turn is 2 s old (its wind-up needs the room), at the next strike, or at the result
	if (!bPass || ScoutAt < 0.0 || Now - ScoutAt > ScoutSeconds || StrikeAt > ScoutAt || !H.Result.IsEmpty() || (Now - FlinchTurnAt > 2.0 && FlinchTurnAt > ScoutAt)) return;
	const float Cx = Canvas->ClipX * 0.5f - (bShowTelemetry ? 22.f * S : 0.f), W = Canvas->ClipX - 40.f * S - (bShowTelemetry ? 44.f * S : 0.f), Hh = 70.f * S;	// TAB: clear of the palm gauge
	const float Y = Bot - (bShowTelemetry ? 64.f : 10.f) * S - Hh, X = Cx - W * 0.5f;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.84f), X, Y, W, Hh);
	DrawRect(FLinearColor(Gold), X, Y, W, 2.f * S);
	TextBox(FString::Printf(TEXT("SCOUTING  -  AFTER ROUND %d OF %d"), ScoutRound, Rounds), Cx, Y + 4.f * S, W - 16.f * S, Gold, 0.95f * S, true);
	for (int32 a = 0; a < 2; ++a)
	{
		const float Ya = Y + (22.f + 23.f * a) * S;
		TextBox(Who(H, a), X + 10.f * S, Ya, 90.f * S, TeamColor[a], 1.0f * S);
		TextBox(ScoutLine(a), X + 100.f * S, Ya + 2.f * S, W - 110.f * S, FColor::White, 0.82f * S);
	}
	TrackBox(TEXT("Scouting"), X, Y, W, Hh);
	if (ScoutShown == 0 || ScoutRound != ScoutLogged) { ScoutLogged = ScoutRound; SLAP_HUD_LOG(1, TEXT("SlapHUD: scouting card after round %d: red %s | blue %s"), ScoutRound, *ScoutLine(0), *ScoutLine(1)); }
	++ScoutShown;
}

void ASlapHUD::StrikeReplay(double Now)
{
	// The per-strike instant replay: at the turn's end (both athletes stand through the referee's hold, so nothing live is
	// missed) each athlete plays back 0.32 s before the touch to 0.12 s after at 0.4x (~1.1 s), or at 0.3x when the strike was
	// clean and the chain ran hips to hand. Drawing only: the physics, the brains and the match clock run on (Blueprint 4).
	const double From = StrikeAt - 0.32, To = StrikeAt + 0.12;
	bReplayChain = StrikeSpeed >= CleanSpeed && Last.ChainOrder >= 3;
	const double Rate = bReplayChain ? 0.3 : 0.4;
	for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
		if (V.IsValid()) V->Replay(From, To, Rate);
	ReplayEnd = Now + (To - From) / Rate;
	bAutoReplay = true;
	ReplayTouch = StrikeAt;
	ReplayPeak = FMath::Max(Read.Peak, StrikeSpeed);
	ReplaySnap = Last.HeadPeak;
	++StrikeReplays;
	SLAP_HUD_LOG(1, TEXT("SlapHUD: strike replay %d: %.2f s of the touch at %.2fx (%.1f s), palm %.1f m/s, snap %.1f m/s%s"), StrikeReplays, To - From, Rate, ReplayEnd - Now, ReplayPeak, ReplaySnap, bReplayChain ? TEXT(", kinetic chain") : TEXT(""));
}

void ASlapHUD::SkipReplay()
{
	const double Now = GetWorld()->GetTimeSeconds();
	if (Now >= ReplayEnd) return;
	for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
		if (V.IsValid()) V->Replay(0.0, 0.0, 0.0);
	ReplayEnd = Now;
	SkippedAt = Now;
	SLAP_HUD_LOG(1, TEXT("SlapHUD: replay skipped at %.2f s (%s)"), Now, bAutoReplay ? TEXT("strike replay") : TEXT("replay"));
	bAutoReplay = false;
}

void ASlapHUD::DrawStrikeReplayCaption(const FSlapHud& H)
{
	// the judges' panel (right, under the plates) may be up at the same turn's end: the caption keeps to the left of it
	const bool bJudges = H.ScoredRound > 0 && GetWorld()->GetTimeSeconds() - CardAt < 5.0;
	const float W = bJudges ? Canvas->ClipX - 284.f * S : Canvas->ClipX - 60.f * S, Cx = bJudges ? 8.f * S + W * 0.5f : Canvas->ClipX * 0.5f, Y = PanelBottom + 6.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.7f), Cx - W * 0.5f, Y - 2.f * S, W, 46.f * S);
	DrawRect(FLinearColor(Gold), Cx - W * 0.5f, Y - 2.f * S, W, 2.f * S);
	TextBox((bJudges ? FString::Printf(TEXT("REPLAY %.1f m/s  SNAP %.1f"), ReplayPeak, ReplaySnap) : FString::Printf(TEXT("REPLAY  PALM %.1f m/s   SNAP %.1f m/s"), ReplayPeak, ReplaySnap)), Cx, Y + 2.f * S, W - 16.f * S, Gold, 1.15f * S, true, GEngine->GetMediumFont());
	TextBox(bReplayChain ? (bJudges ? TEXT("KINETIC CHAIN - tap to skip") : TEXT("KINETIC CHAIN HIPS TO HAND  -  tap or SPACE to skip")) : TEXT("tap or SPACE to skip"), Cx, Y + 27.f * S, W - 16.f * S, Grey, 0.8f * S, true);
	TrackBox(TEXT("ReplayCaption"), Cx - W * 0.5f, Y - 2.f * S, W, 46.f * S);
}

int32 ASlapHUD::WidgetChecks(FString& Out) const
{
	// -SlapUiTest's display-pass assertions: each new widget was drawn or did its job in the match the test played
	int32 Fails = 0, N = 0;
	auto Check = [&](const TCHAR* Name, bool bOk, const FString& Why)
	{
		++N;
		Fails += !bOk;
		Out += FString::Printf(TEXT("%s%s %s (%s)"), Out.IsEmpty() ? TEXT("") : TEXT("; "), Name, bOk ? TEXT("ok") : TEXT("WRONG"), *Why);
	};
	// the danger maths: a fresh neck leaves the capture point alone, a neck at its floor alone reads 0.6, both cap at 1
	const double D0 = CombineDanger(0.3, 1.0), D1 = CombineDanger(0.0, 0.0), D2 = CombineDanger(1.0, 0.0);
	Check(TEXT("danger"), FMath::IsNearlyEqual(D0, 0.3) && FMath::IsNearlyEqual(D1, NeckWeight) && FMath::IsNearlyEqual(D2, 1.0), FString::Printf(TEXT("%.2f %.2f %.2f"), D0, D1, D2));
	Check(TEXT("sense"), bSenseBound && SenseActs == 24, FString::Printf(TEXT("heads %d/%d, %d arm actuators"), HeadBody[0], HeadBody[1], SenseActs));
	Check(TEXT("flinch"), FlinchArmed > 0, FString::Printf(TEXT("armed %d turns, flinches %d/%d"), FlinchArmed, Flinches[0], Flinches[1]));
	Check(TEXT("ticker"), TickerCount > 0, FString::Printf(TEXT("%d lines, last \"%s\""), TickerCount, *TickerLine));
	Check(TEXT("ring"), RingDrawn > 0, FString::Printf(TEXT("%d frames"), RingDrawn));
	Check(TEXT("chips"), ChipsDrawn > 0, FString::Printf(TEXT("%d frames"), ChipsDrawn));
	Check(TEXT("strain"), StrainDrawn > 0, FString::Printf(TEXT("%d frames (TAB on)"), StrainDrawn));
	Check(TEXT("replay"), StrikeReplays > 0 || ReplayDue == 0, FString::Printf(TEXT("%d replays, %d turns ended on a landed strike without a KO"), StrikeReplays, ReplayDue));
	Check(TEXT("scouting"), ScoutShown > 0 || ScoutRoundsSeen == 0, FString::Printf(TEXT("%d frames, %d rounds scored before a result"), ScoutShown, ScoutRoundsSeen));
	// the name plates mirror each other about the screen's centre line (red left, blue right)
	const FCanvasBox* R = Boxes.FindByPredicate([](const FCanvasBox& B) { return B.Name == TEXT("PlateRed"); });
	const FCanvasBox* B = Boxes.FindByPredicate([](const FCanvasBox& X) { return X.Name == TEXT("PlateBlue"); });
	const float CW = Canvas ? Canvas->ClipX : 0.f;
	const bool bMirror = R && B && FMath::Abs(R->Rect.Min.X - (CW - B->Rect.Max.X)) <= 1 && R->Rect.Min.Y == B->Rect.Min.Y && R->Rect.Width() == B->Rect.Width() && R->Rect.Height() == B->Rect.Height();
	Check(TEXT("mirror"), bMirror, R && B ? FString::Printf(TEXT("red x %d-%d, blue x %d-%d in %.0f"), R->Rect.Min.X, R->Rect.Max.X, B->Rect.Min.X, B->Rect.Max.X, CW) : FString(TEXT("plates not drawn")));
	Out = FString::Printf(TEXT("%d/%d widget checks: "), N - Fails, N) + Out;
	return Fails;
}
