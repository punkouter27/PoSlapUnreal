// USlapController: the URLab callback, the menu hand-off and the match log. The rest of the controller lives in
// SlapBind.cpp (athletes from the fingerprints), SlapReplay.cpp (matching check), SlapBrainIO.cpp (observations and
// the brains), SlapMatch.cpp (referee), SlapFatigue.cpp, SlapGaze.cpp, SlapWalkIn.cpp and SlapBroadcast.cpp (display only).

#include "SlapController.h"

#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "MuJoCo/Core/AMjManager.h"
#include "SlapControllerInternal.h"

DEFINE_LOG_CATEGORY(LogSlap);

using namespace SlapCtl;

void USlapController::Bind(mjModel* m, mjData* d, const TMap<int32, UMjActuator*>& ActuatorIdMap)
{
	Super::Bind(m, d, ActuatorIdMap);
	StepCount = 0;
	FString CmdMode;	// -SlapMode=Replay|Policy (standalone test runs)
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapMode="), CmdMode))
	{
		Mode = CmdMode == TEXT("Replay") ? ESlapControlMode::Replay : ESlapControlMode::Policy;
	}
	FParse::Value(FCommandLine::Get(), TEXT("SlapRecord="), RecordFile);	// policy mode: record 5 s for the live check
	FString EyeFlag;	// eye contact on by default; -SlapEyeContact=0 off (mirror of match.py's SLAP_EYE_CONTACT)
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapEyeContact="), EyeFlag)) bEyeContact = EyeFlag != TEXT("0");
	FString WalkFlag;	// pre-match walk-in on by default; -SlapWalkIn=0 off
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapWalkIn="), WalkFlag)) bWalkIn = WalkFlag != TEXT("0");
	{	// the round trip (SlapController.h): menu first and back to it, unless a check or the stream runs unattended
		const TCHAR* Cmd = FCommandLine::Get();
		FString V;
		// -SlapUiTest and -SlapRoundTripTest launch as a player does (the menu first): they check that flow
		const bool bCheck = FParse::Param(Cmd, TEXT("SlapPairTest")) || FParse::Param(Cmd, TEXT("SlapLayoutTest"))
			|| FParse::Param(Cmd, TEXT("SlapShotResult")) || FParse::Value(Cmd, TEXT("SlapShot="), V) || FParse::Value(Cmd, TEXT("SlapFxTest="), V)
			|| FParse::Value(Cmd, TEXT("SlapSeed="), V) || FParse::Value(Cmd, TEXT("SlapFirst="), V) || FParse::Value(Cmd, TEXT("SlapMode="), V) || !RecordFile.IsEmpty();
		const bool bGo = GetWorld() && GetWorld()->URL.HasOption(TEXT("SlapGo"));
		bAutoNext = bCheck || FParse::Param(Cmd, TEXT("SlapLoop"));
		bWaitForStart = !bAutoNext && !bGo;
		bIdlePlaced = bStartNext = false;
		UE_LOG(LogSlap, Log, TEXT("round trip: %s"), bAutoNext ? TEXT("unattended loop") : bWaitForStart ? TEXT("menu first, back to it after each result") : TEXT("START picked this pairing: match now, then the menu"));
	}

	// URLab attaches our MJCF as a child spec, whose <option> is dropped (root spec defaults: 0.002 s, Euler),
	// and its own override is single-precision. Set the training options exactly, from the fingerprint.
	const TSharedPtr<FJsonObject> Fp = LoadJson(Fingerprint);
	if (Fp)
	{
		const TSharedPtr<FJsonObject> Opt = Fp->GetObjectField(TEXT("option"));
		m->opt.timestep = Opt->GetNumberField(TEXT("timestep"));
		m->opt.integrator = (int)Opt->GetNumberField(TEXT("integrator"));
		m->opt.solver = (int)Opt->GetNumberField(TEXT("solver"));
		m->opt.iterations = (int)Opt->GetNumberField(TEXT("iterations"));
		const TArray<TSharedPtr<FJsonValue>>& G = Opt->GetArrayField(TEXT("gravity"));
		for (int32 k = 0; k < 3; ++k) m->opt.gravity[k] = G[k]->AsNumber();
		Timestep = m->opt.timestep;
		Physics = Fp->HasField(TEXT("physics")) ? (int32)Fp->GetNumberField(TEXT("physics")) : 1;
		UE_LOG(LogSlap, Log, TEXT("MuJoCo options from %s: timestep %.17g, integrator %d, physics %d"), *Fingerprint, m->opt.timestep, m->opt.integrator, Physics);
	}
	if (Mode == ESlapControlMode::Replay || !RecordFile.IsEmpty()) BindReplay(m);	// replay stream; also the start state
	Athletes.Reset();
	const TSharedPtr<FJsonObject> BlueFp = BlueFingerprint.IsEmpty() ? Fp : LoadJson(BlueFingerprint);
	if (Fp && BlueFp)
	{
		const FJsonObject* const Fps[2] = { Fp.Get(), BlueFp.Get() };
		if (Mode == ESlapControlMode::Policy) BindPolicy(m, Fps);
		else BindHillMotors(m, Fps);	// replay: no athletes, but the Hill cap still has to be applied (see BindHillMotors)
	}
}

void USlapController::ComputeAndApply(mjModel* m, mjData* d, uint8 Source)
{
	if (FrSaved.Num() == 2 * m->nu) FMemory::Memcpy(m->actuator_forcerange, FrSaved.GetData(), 2 * m->nu * sizeof(mjtNum));	// last step's Hill cap off
	Mode == ESlapControlMode::Replay ? StepReplay(m, d) : StepPolicy(m, d);
	ApplyHill(m, d);
	++StepCount;
}

void USlapController::ApplyHill(mjModel* m, const mjData* d)
{
	// brain_cpu.muscle_step: for this step only, every motor's force capped by its joint's speed (Hill's force-velocity
	// law: pushing the way the joint turns, F0 (1 - u) / (1 + u / K), u = |speed| / top speed; braking keeps F0).
	// The forceranges come back at the next step's start, so SetForce (faint, neck damage) keeps owning them.
	FrSaved.SetNumUninitialized(2 * m->nu);
	FMemory::Memcpy(FrSaved.GetData(), m->actuator_forcerange, 2 * m->nu * sizeof(mjtNum));
	for (const FMotor& Motor : HillMotors)
	{
		if (Motor.Act < 0 || Motor.Vadr < 0 || Motor.Vmax <= 0.0) continue;
		const double V = d->qvel[Motor.Vadr];
		const double U = std::min(std::abs(V) / Motor.Vmax, 1.0);
		const double H = (1.0 - U) / (1.0 + U / HillK);
		if (V < 0.0) m->actuator_forcerange[2 * Motor.Act] *= H;
		else if (V > 0.0) m->actuator_forcerange[2 * Motor.Act + 1] *= H;
	}
}

void USlapController::RequestMatch(UNNEModelData* Red, UNNEModelData* Blue)
{
	FNet N[2];	// built here on the game thread; the physics thread only swaps them in
	const bool bOk[2] = { Red && MakeNet(Red, N[0]), Blue && MakeNet(Blue, N[1]) };
	{
		FScopeLock L(&PendingLock);
		for (int32 a = 0; a < 2; ++a)
		{
			if (!bOk[a]) continue;
			Pending[a] = N[a];
			LoadedBrains.AddUnique(a ? Blue : Red);	// the model data outlives the instance built from it
		}
	}
	UE_LOG(LogSlap, Log, TEXT("menu: new match requested, red %s, blue %s"), bOk[0] ? *N[0].Label : TEXT("(same)"), bOk[1] ? *N[1].Label : TEXT("(same)"));
	bPendingMatch = true;
}

void USlapController::BeginDestroy()
{
	delete Log;
	Log = nullptr;
	if (GhostD) mj_deleteData(GhostD);
	GhostD = nullptr;
	if (Pace != 1.0 && AAMjManager::Instance && AAMjManager::Instance->PhysicsEngine) AAMjManager::Instance->PhysicsEngine->SimSpeedPercent = 100.f;
	Super::BeginDestroy();
}

void USlapController::SetHud(TFunctionRef<void(FSlapHud&)> Edit)
{
	FScopeLock L(&HudLock);
	Edit(Hud);
}

void USlapController::Emit(FStringView Json)
{
	if (!Log) return;
	FTCHARToUTF8 U(Json.GetData(), Json.Len());
	Log->Serialize((void*)U.Get(), U.Length());
	Log->Serialize((void*)"\n", 1);
	Log->Flush();	// whole lines only: commentator/clipper tail the file live
	if (bLiveFeed.load(std::memory_order_relaxed) && !Json.StartsWith(TEXT("{\"type\": \"frame\"")))	// the second screen: events, not frames
	{
		FScopeLock L(&HudLock);
		if (Outbox.Num() < 256) Outbox.Emplace(Json);	// nobody taking them: stop at 256
	}
}

void USlapController::ApplyPending()
{
	FScopeLock L(&PendingLock);
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		if (!Pending[a]) continue;
		FAthlete& A = Athletes[a];
		A.Net = Pending[a]->Net;
		A.NumIn = Pending[a]->NumIn;
		A.NumOut = Pending[a]->NumOut;
		const FString Label = Pending[a]->Label;
		SetHud([&](FSlapHud& H) { H.Brain[a] = Label; });
		Pending[a].Reset();
	}
	if (MatchNo > 0 && Round <= Rounds) Emit(TEXT("{\"type\": \"abandoned\"}"));	// the menu stopped it: no result, no series point
	bWaitForStart = false;
	bStartNext = true;	// the menu asked: the next match starts even when the loop is off
	bInTurn = false;
	HoldLeft = 0;
	WalkInLeft = 0;
	Round = Rounds + 1;	// StepPolicy starts a new match at once
}

int64 USlapController::HoldSteps(double Seconds)
{
	return FMath::RoundToInt64(Seconds / ControlEvery / 0.005) * ControlEvery;	// whole brain steps (200 Hz physics)
}

void USlapController::StepPolicy(mjModel* m, mjData* d)
{
	if (!RecordFile.IsEmpty())	// live matching check: same start as training/brain_cpu.py, record 5 s, no match
	{
		if (StepCount == 0)	// Python's exact start state (URLab's own qpos0 differs by 1 ulp in blue's root quat)
		{
			StartFromRecordedPose(m, d);
			Recorded.Reset();
		}
		Recorded.Append(d->qpos, m->nq);	// before this step, as Python records it
		if (Recorded.Num() / m->nq == RecordSteps)
		{
			WriteQposDump(m, RecordFile);
			RecordFile.Reset();
		}
		if (StepCount % ControlEvery == 0) Decide(m, d);
	}
	else if (Athletes.Num() == 2)	// the match (training/match.py)
	{
		if (bPendingMatch.exchange(false)) ApplyPending();	// the menu: new brains, a fresh match
		if (bWaitForStart && MatchNo == 0)	// a normal launch: the menu first; both stand at the podium until START
		{
			if (!bIdlePlaced) { StandIdle(m, d); bIdlePlaced = true; }
			if (StepCount % ControlEvery == 0) Decide(m, d);
		}
		else if (WalkInLeft > 0)
		{
			StepWalkIn(m, d);
			if (HudLive()) --WalkInLeft;
		}
		else
		{
			if (bInTurn) PostStep(m, d);
			for (;;)	// a turn that ends here starts the next at once (match.py's loop), checks included
			{
				if (!bInTurn)
				{
					if (MatchNo > 0 && HoldLeft > 0) break;	// a turn or match just ended: let the crowd see it (StartTurn resets after)
					if (MatchNo == 0 || Round > Rounds)
					{
						if (MatchNo > 0 && !bAutoNext && !bStartNext) break;	// the result stands until the menu asks for the next match
						bStartNext = false;
						StartMatch(m, d);
						if (WalkInLeft > 0) break;
					}
					else StartTurn(m, d);
				}
				Faint(m);
				if (PreStep(m, d)) break;
			}
			if (WalkInLeft == 0 && (bInTurn ? Turn.Step % ControlEvery == 0 : HoldLeft-- % ControlEvery == 0))
			{
				Decide(m, d);	// the hold: both stand (role 0)
				if (!bInTurn && Round > Rounds && LastWinner >= 0 && LastWinner < Athletes.Num())
				{
					// Post-match winner celebration (display-only during PostMatchHold before StartMatch resets)
					FAthlete& W = Athletes[LastWinner];
					if (W.ShSwingL >= 0) W.Goal[W.ShSwingL] = FMath::Clamp(W.Stand[W.ShSwingL] + 0.85, W.Lo[W.ShSwingL], W.Hi[W.ShSwingL]);
					if (W.ShSwingR >= 0) W.Goal[W.ShSwingR] = FMath::Clamp(W.Stand[W.ShSwingR] + 0.85, W.Lo[W.ShSwingR], W.Hi[W.ShSwingR]);
				}
			}
		}
	}
	else if (StepCount % ControlEvery == 0) Decide(m, d);
	for (const FAthlete& A : Athletes)
	{
		for (int32 j = 0; j < A.Act.Num(); ++j) d->ctrl[A.Act[j]] = A.Goal[j];
		FMemory::Memzero(d->xfrc_applied + 6 * A.HeadBody, 6 * sizeof(mjtNum));
	}
	if (Athletes.Num() == 2 && bInTurn && Turn.TouchStep >= 0 && Turn.Step - Turn.TouchStep < KickSteps)
		FMemory::Memcpy(d->xfrc_applied + 6 * Athletes[1 - Turn.Striker].HeadBody, Turn.Kick, sizeof(Turn.Kick));
	if (Physics >= 2 && bInTurn && Turn.TouchStep < 0) GeomPrev = TArray<mjtNum>(d->geom_xpos, 3 * m->ngeom);	// match.py gprev: just before the step
	if (RecordFile.IsEmpty()) SetPace(m);
}

void USlapController::SetPace(const mjModel* m)
{
	// Live slow motion: only how fast URLab feeds steps changes (SimSpeedPercent), never the step itself (Blueprint 4),
	// so every step, decision and event is the one full speed gives. Quarter speed from just before the palm lands to
	// just after; 40 % while a struck defender's capture point is far outside his soles (he is going over).
	double Want = 1.0;
	if (bSlowMo.load(std::memory_order_relaxed) && bInTurn && Athletes.Num() == 2)
	{
		const double Since = Turn.TouchStep >= 0 ? (Turn.Step - Turn.TouchStep) * m->opt.timestep : -1.0;
		if (Since < 0.0 && Ttc < (Pace < 1.0 ? 0.3 : 0.15) && Turn.SlowSteps < HoldSteps(0.4)) { Want = 0.25; ++Turn.SlowSteps; }	// hysteresis (the estimate wobbles); a slap that never lands: 0.4 s at most
		else if (Since >= 0.0 && Since < 0.35) Want = 0.25;
		else if (Since >= 0.0 && Since < 2.5 && DangerNow[1 - Turn.Striker] > 0.8) Want = 0.4;
	}
	if (Want == Pace) return;
	Pace = Want;
	if (AAMjManager* Mgr = AAMjManager::Instance; Mgr && Mgr->PhysicsEngine) Mgr->PhysicsEngine->SimSpeedPercent = (float)(100.0 * Want);
	SetHud([&](FSlapHud& H) { H.SlowMo = (float)Want; });
}

