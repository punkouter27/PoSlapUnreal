// The match (training/match.py, step for step: same rules, same order, same JSONL events): the toss, the turns, the
// referee before and after each step, the result.

#include "SlapController.h"

#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/DateTime.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "SlapControllerInternal.h"
#include "SlapSwingRead.h"

using namespace SlapCtl;

FString USlapController::Down(const mjModel* m, const mjData* d, const FAthlete& A, bool bDefender, FVector* OutPos) const
{
	for (int32 c = 0; c < d->ncon; ++c)
	{
		const mjContact& C = d->contact[c];
		const int32 G = OnGround(C, Floor, Podium);
		if (G < 0) continue;
		if (bDefender && (C.geom[0] == Podium || C.geom[1] == Podium) && A.Palms.Contains(G)) continue;	// his brace (match.py down())
		if (m->body_rootid[m->geom_bodyid[G]] == A.Pelvis && !A.Feet.Contains(G))
		{
			if (OutPos) *OutPos = MjUtils::MjToUEPosition(C.pos);	// where it landed (display only: the dust puff)
			return Slap::Name(m, mjOBJ_GEOM, G);
		}
	}
	return FString();
}

void USlapController::StartMatch(mjModel* m, mjData* d)
{
	++MatchNo;
	int32 Forced = -1;	// -SlapFirst=0|1: the first match's coin toss (match.py compare)
	const bool Force = MatchNo == 1 && FParse::Value(FCommandLine::Get(), TEXT("SlapFirst="), Forced) && (Forced == 0 || Forced == 1);
	First = Force ? Forced : FMath::RandRange(0, 1);
	int64 FixedSeed = -1;	// -SlapSeed=<n>: the first match's dice (checks); else from the clock
	Seed = MatchNo == 1 && FParse::Value(FCommandLine::Get(), TEXT("SlapSeed="), FixedSeed) && FixedSeed >= 0 ? (uint32)FixedSeed
		: (uint32)(FDateTime::Now().GetTicks() * 2654435761ull >> 16) & 0x7FFFFFFFu;
	Round = 1;
	TurnInRound = 0;
	Referee.Reset();	// the judges start from zero (SlapReferee.h)
	for (FAthlete& A : Athletes)	// fatigue and memory last one match
	{
		A.Mem[0] = A.Mem[1] = 0.0;
		A.Stamina = A.NeckStrength = 1.0;
	}
	delete Log;
	const FString Dir = FPaths::Combine(FPaths::ProjectDir(), TEXT("Logs/Gameplay"), FDateTime::Now().ToString(TEXT("%y%m%d")));
	IFileManager::Get().MakeDirectory(*Dir, true);
	Log = IFileManager::Get().CreateFileWriter(*FPaths::Combine(Dir, FString::Printf(TEXT("match_%s_%d_ue.jsonl"), *FDateTime::Now().ToString(TEXT("%H%M%S")), First)), FILEWRITE_AllowRead);	// commentator/clipper tail it live
	FString Brains[2];	// logged for training/scouting.py (compare skips match_start)
	{
		FScopeLock L(&HudLock);
		Brains[0] = Hud.Brain[0];
		Brains[1] = Hud.Brain[1];
	}
	Emit(FString::Printf(TEXT("{\"type\": \"match_start\", \"first\": %s, \"rules\": {\"rounds\": %d, \"strike_clock\": %.1f, \"recovery\": %.1f}, \"seed\": %u, \"brains\": {\"red\": %s, \"blue\": %s}}"),
		*Str(Athletes[First].Name), Rounds, StrikeClock, Recovery, Seed, *Str(Brains[0]), *Str(Brains[1])));
	UE_LOG(LogSlap, Log, TEXT("match %d: coin toss -> %s strikes first"), MatchNo, *Athletes[First].Name);
	if (bWalkIn && HoldSteps(WalkInHold) > 0)
	{
		const bool bBrainWalk = Athletes[0].WalkNet || Athletes[1].WalkNet;
		WalkInTotal = WalkInLeft = HoldSteps(bBrainWalk ? WalkTimeout : WalkInHold);
		mj_resetData(m, d);	// brain walkers start standing at the top of the aisle; the scripted ones are placed every step
		for (FAthlete& A : Athletes)
		{
			for (int32 j = 0; j < A.Act.Num(); ++j) d->qpos[A.Qadr[j]] = A.Stand[j];
			A.Goal = A.Stand;
			A.PrevAction.Init(0.f, A.Act.Num());
			A.bFresh = true;
			A.WalkPhase = 0.0;
			A.WalkT = 0;
			A.bArrived = A.bFell = false;
			A.SettleK = A.SettleN = 0;
			A.WalkHold.Reset();
		}
		PlaceWalkers(m, d);
		StepWalkIn(m, d);	// sets the walk-in HUD state
	}
	else StartTurn(m, d);
	SetHud([&](FSlapHud& H)	// after StartTurn: a turn clears the last turn's call, the toss is this one's
	{
		H.Match = MatchNo; ++H.MatchSeq; H.Result.Reset(); H.ScoredRound = 0; FMemory::Memzero(H.Cards);
		H.Winner = H.KOd = -1; H.Downs[0] = H.Downs[1] = 0;
		H.Round = 1; H.Striker = Athletes[First].Name; H.Defender = Athletes[1 - First].Name;
		H.Event = Athletes[First].Name + TEXT(" wins the toss");
	});
}

void USlapController::StandIdle(mjModel* m, mjData* d)
{
	ResetToStand(m, d);	// the menu at launch: the stand pose at the foot boxes, fresh brains, full strength
}

void USlapController::ResetToStand(mjModel* m, mjData* d)
{
	mj_resetData(m, d);	// as brain_cpu.stand(): reset, stand pose, forward (then match.py forwards once more)
	for (FAthlete& A : Athletes)
	{
		for (int32 j = 0; j < A.Act.Num(); ++j) d->qpos[A.Qadr[j]] = A.Stand[j];
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, A.Act.Num());
		A.bFresh = true;
		SetForce(m, A, 1.0);	// back from any faint; damage: a weaker neck (match.py: forcerange = own x neck strength)
	}
	SettleMuscles(m, d);
	mj_forward(m, d);
	mj_forward(m, d);
}

void USlapController::StartTurn(mjModel* m, mjData* d)
{
	Turn = FTurn();
	Turn.Striker = TurnInRound == 0 ? First : 1 - First;
	ResetToStand(m, d);	// the dice below and the telemetry resets touch no physics state: the order is free
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)	// fainting (match.py faint_step): 1-10 s in, on a brain step
	{
		const FAthlete& A = Athletes[a];
		if (A.FaintChance <= 0.0 || Mix({ Seed, (uint32)Round, (uint32)Turn.Striker, (uint32)a, 0u }) / 4294967296.0 >= A.FaintChance) continue;
		Turn.FaintAt[a] = (int64)std::nearbyint((1.0 + 9.0 * Mix({ Seed, (uint32)Round, (uint32)Turn.Striker, (uint32)a, 1u }) / 4294967296.0) / m->opt.timestep / 4.0) * 4;
		Turn.FaintEnd[a] = Turn.FaintAt[a] + (int64)std::nearbyint(A.FaintSeconds / m->opt.timestep);
	}
	bTelReady = false;
	ChainAt = ChainNum = 0;
	PrevHandDist = -1.0;
	Ttc = 99.0;
	DangerNow[0] = DangerNow[1] = 0.0;
	SeedGaze(d);	// eye contact: world gaze biases start on the eye line
	for (int32 a = 0; a < 2; ++a)
	{
		for (int32 f = 0; f < 2; ++f)
		{
			Turn.Feet0[a][f][0] = d->geom_xpos[3 * Athletes[a].Feet[f]];
			Turn.Feet0[a][f][1] = d->geom_xpos[3 * Athletes[a].Feet[f] + 1];
		}
	}
	const FAthlete& S = Athletes[Turn.Striker];
	const FAthlete& D = Athletes[1 - Turn.Striker];
	FMemory::Memcpy(Turn.HandPrev, d->geom_xpos + 3 * S.Hand, sizeof(Turn.HandPrev));
	FMemory::Memcpy(Turn.HeadPrev, d->geom_xpos + 3 * D.Head, sizeof(Turn.HeadPrev));
	bInTurn = true;
	Emit(FString::Printf(TEXT("{\"type\": \"turn_start\", \"round\": %d, \"striker\": %s, \"defender\": %s}"), Round, *Str(S.Name), *Str(D.Name)));
	const FString Toss = (Round == 1 && TurnInRound == 0) ? (Athletes[First].Name + TEXT(" wins the toss")) : FString();
	SetHud([&](FSlapHud& H) { H.Round = Round; H.Striker = S.Name; H.Defender = D.Name; H.Clock = StrikeClock; H.bStruck = false;
		H.bWalkIn = false; H.WalkInProgress = 1.0;
		H.Speed = H.HeadPeak = 0.0; H.Part.Reset(); H.Event = Toss; H.Hic = H.Bric = 0.0; H.ChainOrder = -1; });	// the last turn's strike stats and call had stayed up through this one
}

bool USlapController::PreStep(const mjModel* m, mjData* d)
{
	const double T = Turn.Step * m->opt.timestep;
	const FAthlete& S = Athletes[Turn.Striker];
	const FAthlete& D = Athletes[1 - Turn.Striker];
	if (Turn.Step % ControlEvery == 0)	// the referee looks every brain step, before the brains decide
	{
		PublishFootsteps(d);	// display only: a footstep from MuJoCo's own sole contacts
		for (const FAthlete* X : { &D, &S })
		{
			FVector At;
			const FString Geom = Down(m, d, *X, X == &D, &At);
			if (Geom.IsEmpty()) continue;
			Emit(FString::Printf(TEXT("{\"type\": \"knockdown\", \"t\": %s, \"athlete\": %s, \"part\": %s}"), *Num(T, 3), *Str(X->Name), *Str(Part(Geom))));
			UE_LOG(LogSlap, Log, TEXT("t=%.3f knockdown: %s %s"), T, *X->Name, *Part(Geom));
			const int32 Who = X == &D ? 1 - Turn.Striker : Turn.Striker;
			const bool bKO = X == &D && Turn.TouchStep >= 0;
			SetHud([&](FSlapHud& H) { H.DownPos = At; ++H.DownSeq; ++H.Downs[Who]; if (bKO) H.KOd = Who; });	// where it landed: the dust puff; the name badges
			if (bKO)
			{
				Turn.bKO = true;
				EndTurn(m, d, TEXT("KO"));
			}
			else
			{
				Turn.Fouls.Emplace(X == &S ? Turn.Striker : 1 - Turn.Striker, X == &S ? TEXT("knocked down") : TEXT("down before the strike"));
				EndTurn(m, d, TEXT("knockdown"));
			}
			return false;
		}
		for (int32 a = 0; a < 2; ++a)
		{
			bool Out = false;
			for (int32 f = 0; f < 2; ++f)
			{
				const double* P = d->geom_xpos + 3 * Athletes[a].Feet[f];
				Out |= FMath::Abs(P[0] - Turn.Feet0[a][f][0]) > BoxX || FMath::Abs(P[1] - Turn.Feet0[a][f][1]) > BoxY;
			}
			if (!Out) continue;
			Turn.Fouls.Emplace(a, TEXT("out of the box"));
			Emit(FString::Printf(TEXT("{\"type\": \"foul\", \"t\": %s, \"athlete\": %s, \"why\": \"out of the box\"}"), *Num(T, 3), *Str(Athletes[a].Name)));
			EndTurn(m, d, TEXT("out of the box"));
			return false;
		}
	}
	if (Turn.TouchStep < 0 && T >= StrikeClock)
	{
		Turn.Fouls.Emplace(Turn.Striker, TEXT("no strike in 30 s"));
		EndTurn(m, d, TEXT("clock"));
		return false;
	}
	if (Turn.TouchStep >= 0 && (Turn.Step - Turn.TouchStep) * m->opt.timestep >= Recovery)
	{
		EndTurn(m, d, TEXT("recovered"));
		return false;
	}
	if (Turn.Step % ControlEvery == 0 && Turn.TouchStep < 0)	// the feint read (match.py feint_read), every brain step until the touch
	{
		using R = FSlapSwingRead;	// the HUD's thresholds = training/constants.py FEINT_*
		const double V = Turn.LastSpeed;
		if (V > Turn.FeintPeak) { Turn.FeintPeak = V; Turn.FeintAt = T; Turn.bFeintSwing = true; }
		if (Turn.bFeintSwing && !Turn.bFeintTagged && T - Turn.FeintAt > R::FeintAfter && V < R::FeintPullBack * Turn.FeintPeak && Turn.FeintPeak > R::FeintMinPeak)
		{
			Turn.bFeintTagged = true;
			++Turn.Feints;
			Emit(FString::Printf(TEXT("{\"type\": \"feint\", \"t\": %s, \"striker\": %s, \"peak\": %s, \"n\": %d}"), *Num(T, 3), *Str(S.Name), *Num(Turn.FeintPeak, 2), Turn.Feints));
		}
		if (Turn.bFeintTagged && V > R::RearmShare * Turn.FeintPeak && T - Turn.FeintAt > R::RearmAfter)
		{
			Turn.bFeintSwing = Turn.bFeintTagged = false;
			Turn.FeintPeak = 0.0;
		}
	}
	return true;
}

void USlapController::PostStep(const mjModel* m, const mjData* d)
{
	++Turn.Step;
	const double T = Turn.Step * m->opt.timestep;
	bool Finite = true;
	double MaxV = 0.0;
	for (int32 i = 0; i < m->nq; ++i) Finite &= FMath::IsFinite(d->qpos[i]);
	for (int32 i = 0; i < m->nv; ++i) MaxV = FMath::Max(MaxV, FMath::Abs(d->qvel[i]));
	if (!Finite || MaxV > 1e3)
	{
		Emit(FString::Printf(TEXT("{\"type\": \"blowup\", \"t\": %s}"), *Num(T, 3)));
		Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": \"blow-up\", \"winner\": null, \"round\": %d, \"cards\": %s}"),
			Round, *CardsJson(Referee.Cards)));
		UE_LOG(LogSlap, Error, TEXT("t=%.3f sim blow-up: match abandoned"), T);
		SetHud([&](FSlapHud& H) { H.Result = TEXT("No contest (simulation blow-up)"); });
		bInTurn = false;
		Round = Rounds + 1;	// the next callback starts a new match
		return;
	}
	const FAthlete& S = Athletes[Turn.Striker];
	const FAthlete& D = Athletes[1 - Turn.Striker];
	const double* Hand = d->geom_xpos + 3 * S.Hand;
	const double Speed = Dist(Hand, Turn.HandPrev, 3) / m->opt.timestep;
	const double Travel[3] = { Hand[0] - Turn.HandPrev[0], Hand[1] - Turn.HandPrev[1], Hand[2] - Turn.HandPrev[2] };	// broadcast cue only
	FMemory::Memcpy(Turn.HandPrev, Hand, sizeof(Turn.HandPrev));
	Turn.LastSpeed = Speed;	// the feint read's sample at the next brain step (match.py `speed`)
	Telemetry(m, d, Speed);
	Impact(m, d);
	if (Turn.TouchStep < 0) ChainSample(d, Speed);
	const double* Head = d->geom_xpos + 3 * D.Head;
	if (Turn.TouchStep >= 0) Turn.HeadPeak = FMath::Max(Turn.HeadPeak, Dist(Head, Turn.HeadPrev, 3) / m->opt.timestep);
	FMemory::Memcpy(Turn.HeadPrev, Head, sizeof(Turn.HeadPrev));
	if (Turn.TouchStep >= 0) return;
	for (int32 c = 0; c < d->ncon; ++c)
	{
		const mjContact& C = d->contact[c];
		if (C.geom[0] != D.Head && C.geom[1] != D.Head) continue;
		const int32 Other = C.geom[1] == D.Head ? C.geom[0] : C.geom[1];
		if (m->body_rootid[m->geom_bodyid[Other]] != S.Pelvis) continue;
		const double Sign = C.geom[0] == Other ? 1.0 : -1.0;	// normal from the striker's part to the head
		const double* Z = d->geom_xmat + 9 * S.Hand;			// palm = the hand box's -z axis (column 2)
		Turn.bPalm = Other == S.Hand && -Sign * (C.frame[0] * Z[2] + C.frame[1] * Z[5] + C.frame[2] * Z[8]) > 0.5;
		Turn.TouchStep = Turn.Step;
		ChainAtTouch(m->opt.timestep);
		Turn.Com0[0] = d->subtree_com[3 * D.Pelvis];
		Turn.Com0[1] = d->subtree_com[3 * D.Pelvis + 1];
		Turn.Part = Part(Slap::Name(m, mjOBJ_GEOM, Other));
		double Vel[3] = { Travel[0], Travel[1], Travel[2] };	// physics 1: hand_r's travel over the step
		double PartSpeed = Speed;
		if (Physics >= 2 && GeomPrev.Num() == 3 * m->ngeom)	// physics 2: the part that touched (match.py)
		{
			for (int32 i = 0; i < 3; ++i) Vel[i] = d->geom_xpos[3 * Other + i] - GeomPrev[3 * Other + i];
			PartSpeed = Dist(d->geom_xpos + 3 * Other, GeomPrev.GetData() + 3 * Other, 3) / m->opt.timestep;
		}
		Turn.Speed = RoundTo(PartSpeed, 2);
		// the body behind the palm (match.py): the striker's card mass times the hand's velocity, over KickSteps
		const double* Ci = d->xipos + 3 * D.HeadBody;
		mjtNum R[3];
		mju_sub3(R, C.pos, Ci);
		double* K = Turn.Kick;
		for (int32 i = 0; i < 3; ++i) K[i] = Vel[i] / m->opt.timestep * (S.StrikeMass / (KickSteps * m->opt.timestep));
		mju_cross(K + 3, R, K);	// torque = R x force (same expression order as before: bit-identical)
		// hand_speed (hand_r's speed this step), feint, feints: the additive fields (DOCS/20261103/events.md, match.ADDITIVE)
		Emit(FString::Printf(TEXT("{\"type\": \"strike\", \"t\": %s, \"striker\": %s, \"part\": %s, \"palm\": %s, \"speed\": %s, \"hand_speed\": %s, \"feint\": %s, \"feints\": %d}"),
			*Num(T, 3), *Str(S.Name), *Str(Turn.Part), *Bool(Turn.bPalm), *Num(Turn.Speed, 2), *Num(RoundTo(Speed, 2), 2), *Bool(Turn.Feints > 0), Turn.Feints));
		UE_LOG(LogSlap, Log, TEXT("t=%.3f %s strikes with the %s at %.2f m/s%s"), T, *S.Name, *Turn.Part, Turn.Speed, Turn.bPalm ? TEXT("") : TEXT(" (not the palm)"));
		const FVector Pos = MjUtils::MjToUEPosition(C.pos);
		const FVector Dir = MjUtils::MjToUEPosition(Travel).GetSafeNormal();
		SetHud([&](FSlapHud& H)
		{
			H.bStruck = true; H.Speed = Turn.Speed; H.Part = Turn.Part; H.HeadPeak = 0.0; H.Event = Turn.Speed >= Slap::CleanSpeed ? (Turn.bPalm ? TEXT("Clean palm") : TEXT("Clean hit")) : (Turn.bPalm ? TEXT("Palm") : TEXT("Hit"));	// clean = the L2 8 m/s line; every hit scores
			++H.StrikeSeq; H.StrikePos = Pos; H.StrikeDir = Dir; H.StrikeDefender = 1 - Turn.Striker;
		});
		break;
	}
}

void USlapController::EndTurn(const mjModel* m, mjData* d, const FString& End)
{
	FAthlete& S = Athletes[Turn.Striker];
	FAthlete& D = Athletes[1 - Turn.Striker];
	const bool Touched = Turn.TouchStep >= 0;
	if (Touched) Turn.bStagger = Dist(d->subtree_com + 3 * D.Pelvis, Turn.Com0, 2) > 0.05;
	Turn.HeadPeak = RoundTo(Turn.HeadPeak, 2);
	FString Fouls;
	for (const TPair<int32, FString>& F : Turn.Fouls) Fouls += (Fouls.IsEmpty() ? TEXT("") : TEXT(", ")) + Str(Athletes[F.Key].Name + TEXT(": ") + F.Value);
	if (Touched)	// the strike's outcome once the recovery window has measured it (match.py finish, events.md)
	{
		Emit(FString::Printf(TEXT("{\"type\": \"strike_result\", \"t\": %s, \"striker\": %s, \"defender\": %s, \"part\": %s, \"speed\": %s, \"head_peak\": %s, \"feint\": %s, \"stagger\": %s, \"ko\": %s}"),
			*Num(Turn.TouchStep * m->opt.timestep, 3), *Str(S.Name), *Str(D.Name), *Str(Turn.Part), *Num(Turn.Speed, 2), *Num(Turn.HeadPeak, 2),
			*Bool(Turn.Feints > 0), *Bool(Turn.bStagger), *Bool(Turn.bKO)));
	}
	Emit(FString::Printf(TEXT("{\"type\": \"turn_end\", \"round\": %d, \"striker\": %s, \"touch\": %s, \"part\": %s, \"palm\": %s, \"speed\": %s, \"head_peak\": %s, \"stagger\": %s, \"ko\": %s, \"end\": %s, \"fouls\": [%s]}"),
		Round, *Str(S.Name), Touched ? *Num(Turn.TouchStep * m->opt.timestep, 3) : TEXT("null"), Touched ? *Str(Turn.Part) : TEXT("null"),
		*Bool(Turn.bPalm), *Num(Turn.Speed, 2), *Num(Turn.HeadPeak, 2), *Bool(Turn.bStagger), *Bool(Turn.bKO), *Str(End), *Fouls));
	UE_LOG(LogSlap, Log, TEXT("round %d, %s's turn over: %s"), Round, *S.Name, *End);
	if (Touched)	// broadcast science (match.py has no such line; compare checks only the events both write)
	{
		Emit(FString::Printf(TEXT("{\"type\": \"impact\", \"round\": %d, \"striker\": %s, \"hic\": %s, \"bric\": %s, \"chain\": [%s, %s, %s, %s, %s], \"chain_order\": %d}"),
			Round, *Str(S.Name), *Num(Turn.Hic, 0), *Num(Turn.Bric, 3), *Num(Turn.Chain[0], 3), *Num(Turn.Chain[1], 3), *Num(Turn.Chain[2], 3),
			*Num(Turn.Chain[3], 3), *Num(Turn.Chain[4], 3), Turn.ChainOrder));
	}
	ApplyFatigue(m, S, D);	// fatigue and memory (match.py finish(): same order, same arithmetic)
	FString Cond;
	for (const FAthlete& A : Athletes) Cond += FString::Printf(TEXT(", %s: [%s, %s]"), *Str(A.Name), *Num(A.Stamina, 3), *Num(A.NeckStrength, 3));
	Emit(TEXT("{\"type\": \"condition\"") + Cond + TEXT("}"));
	const FString Call = Turn.bKO ? FString(TEXT("KNOCKOUT")) : Turn.Fouls.Num() ? TEXT("Foul: ") + Turn.Fouls.Last().Value : End;
	SetHud([&](FSlapHud& H)
	{
		H.HeadPeak = Turn.HeadPeak; H.Event = Call; H.Hic = Turn.Hic; H.Bric = Turn.Bric;
		H.bStagger = Turn.bStagger;		// the near-miss cue: it landed, but he did not stagger
		for (int32 a = 0; a < 2; ++a) { H.Stamina[a] = Athletes[a].Stamina; H.Neck[a] = Athletes[a].NeckStrength; }
		if (Turn.bKO) ++H.KOSeq;
		++H.TurnSeq;	// the turn is over: the HUD's near-miss, feint and judges cues watch this
	});
	Referee.RoundTurns.Add(Turn);
	bInTurn = false;

	if (Turn.bKO)
	{
		Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": \"KO\", \"winner\": %s, \"round\": %d, \"cards\": %s}"), *Str(S.Name), Round, *CardsJson(Referee.Cards)));
		const FString Res = FString::Printf(TEXT("%s wins by KO, round %d"), *S.Name.ToUpper(), Round);
		SetHud([&](FSlapHud& H) { H.Result = Res; });
		Round = Rounds + 1;	// match over; the next turn starts a new one
		EndMatch(Turn.Striker);
		return;
	}
	if (++TurnInRound < 2) { HoldLeft = HoldSteps(TurnHold); return; }	// let the crowd see how the turn ended
	int32 Pts[3][2];
	Referee.Score(Pts);	// the judges (SlapReferee.h)
	Emit(FString::Printf(TEXT("{\"type\": \"round\", \"round\": %d, \"cards\": %s}"), Round, *CardsJson(Referee.Cards)));
	SetHud([&](FSlapHud& H) { FMemory::Memcpy(H.Cards, Referee.Cards, sizeof(Referee.Cards)); FMemory::Memcpy(H.RoundCards, Pts, sizeof(Pts)); H.ScoredRound = Round; });
	Referee.RoundTurns.Reset();
	TurnInRound = 0;
	if (++Round <= Rounds) { HoldLeft = HoldSteps(TurnHold); return; }
	int32 Winner = -1;
	const FString How = Referee.Decision(Winner, First);	// the decision (SlapReferee.h): the wording and the winner
	const bool Draw = Winner < 0;
	Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": %s, \"winner\": %s, \"round\": %d, \"cards\": %s}"), *Str(How), Draw ? TEXT("null") : *Str(Athletes[Winner].Name), Rounds, *CardsJson(Referee.Cards)));
	const FString Res = Draw ? FString(TEXT("Draw")) : FString::Printf(TEXT("%s wins by %s"), *Athletes[Winner].Name.ToUpper(), *How);
	SetHud([&](FSlapHud& H) { H.Result = Res; });
	EndMatch(Winner);
}

void USlapController::EndMatch(int32 Winner)
{
	LastWinner = Winner;
	HoldLeft = HoldSteps(PostMatchHold);
	SetHud([&](FSlapHud& H) { Winner < 0 ? ++H.Draws : ++H.Wins[Winner]; H.Winner = Winner; });
}

