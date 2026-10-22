#include "SlapController.h"

#include "Dom/JsonObject.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "NNE.h"
#include "NNEModelData.h"
#include "NNERuntimeCPU.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SlapNames.h"

#include <cmath>

DEFINE_LOG_CATEGORY_STATIC(LogSlap, Log, All);

namespace
{
	constexpr int32 ControlEvery = 4;	// 50 Hz brain over 200 Hz physics
	constexpr int32 NumObs = 108;		// DOCS/BRAIN_IO.md

	TSharedPtr<FJsonObject> LoadJson(const FString& RelPath)
	{
		FString Text;
		TSharedPtr<FJsonObject> Obj;
		if (FFileHelper::LoadFileToString(Text, *FPaths::Combine(FPaths::ProjectDir(), RelPath)))
		{
			FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Obj);
		}
		if (!Obj.IsValid())
		{
			UE_LOG(LogSlap, Error, TEXT("Could not read %s"), *RelPath);
		}
		return Obj;
	}

	/** One name per qpos slot; same as joint_names() in training/replay_check.py. */
	TArray<FString> QposNames(const mjModel* m)
	{
		TArray<FString> Out;
		for (int32 j = 0; j < m->njnt; ++j)
		{
			const int32 N = m->jnt_type[j] == mjJNT_FREE ? 7 : m->jnt_type[j] == mjJNT_BALL ? 4 : 1;
			for (int32 k = 0; k < N; ++k)
			{
				Out.Add(N > 1 ? FString::Printf(TEXT("%s[%d]"), *Slap::Name(m, mjOBJ_JOINT, j), k) : Slap::Name(m, mjOBJ_JOINT, j));
			}
		}
		return Out;
	}
}

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
		UE_LOG(LogSlap, Log, TEXT("MuJoCo options from %s: timestep %.17g, integrator %d"), *Fingerprint, m->opt.timestep, m->opt.integrator);
	}
	if (Mode == ESlapControlMode::Replay || !RecordFile.IsEmpty()) BindReplay(m);	// replay stream; also the start state
	Athletes.Reset();
	Striker = -1;	// no match flow until BindPolicy finds both athletes
	if (Mode == ESlapControlMode::Policy && Fp) BindPolicy(m, *Fp);
}

void USlapController::ComputeAndApply(mjModel* m, mjData* d, uint8 Source)
{
	Mode == ESlapControlMode::Replay ? StepReplay(m, d) : StepPolicy(m, d);
	++StepCount;
}

// ---------------------------------------------------------------- replay (matching check)

void USlapController::BindReplay(mjModel* m)
{
	ReplayAct.Reset(); ReplayCtrl.Reset(); ReplayQ0.Reset(); Recorded.Reset();
	ReplaySteps = 0;
	const TSharedPtr<FJsonObject> Ref = LoadJson(ReplayFile);
	if (!Ref) return;

	for (const TSharedPtr<FJsonValue>& V : Ref->GetArrayField(TEXT("actuators")))
	{
		const int32 Id = Slap::Find(m, mjOBJ_ACTUATOR, m->nu, V->AsString());
		UE_CLOG(Id < 0, LogSlap, Error, TEXT("Replay: no actuator %s"), *V->AsString());
		ReplayAct.Add(Id);
	}
	for (const TSharedPtr<FJsonValue>& Row : Ref->GetArrayField(TEXT("ctrl")))
	{
		for (const TSharedPtr<FJsonValue>& V : Row->AsArray()) ReplayCtrl.Add(V->AsNumber());
	}
	const TArray<FString> Mine = QposNames(m);
	const TArray<TSharedPtr<FJsonValue>>& RefNames = Ref->GetArrayField(TEXT("names"));
	const TArray<TSharedPtr<FJsonValue>>& Q0 = Ref->GetArrayField(TEXT("qpos"))[0]->AsArray();
	for (int32 i = 0; i < RefNames.Num(); ++i)
	{
		const int32 Slot = Mine.IndexOfByPredicate([&](const FString& N) { return Slap::Matches(N, RefNames[i]->AsString()); });
		UE_CLOG(Slot < 0, LogSlap, Error, TEXT("Replay: no qpos slot %s"), *RefNames[i]->AsString());
		if (Slot >= 0) ReplayQ0.Emplace(Slot, Q0[i]->AsNumber());
	}
	UE_LOG(LogSlap, Log, TEXT("Replay: %d actuators, %d control steps, model timestep %g"),
		ReplayAct.Num(), ReplayCtrl.Num() / FMath::Max(1, ReplayAct.Num()), m->opt.timestep);
}

void USlapController::StepReplay(mjModel* m, mjData* d)
{
	const int32 NumCtrl = ReplayCtrl.Num() / FMath::Max(1, ReplayAct.Num());
	if (ReplaySteps >= NumCtrl * ControlEvery) return;	// done (or nothing loaded); d->ctrl keeps the last values

	if (ReplaySteps == 0) StartFromRecordedPose(m, d);
	const int32 C = ReplaySteps / ControlEvery;
	for (int32 a = 0; a < ReplayAct.Num(); ++a)
	{
		if (ReplayAct[a] >= 0) d->ctrl[ReplayAct[a]] = ReplayCtrl[C * ReplayAct.Num() + a];
	}
	Recorded.Append(d->qpos, m->nq);

	if (++ReplaySteps == NumCtrl * ControlEvery) WriteQposDump(m, ReplayDump);
}

void USlapController::StartFromRecordedPose(const mjModel* m, mjData* d) const	// same start as Python: reset, stand pose, forward
{
	mj_resetData(m, d);
	for (const TPair<int32, double>& Q : ReplayQ0) d->qpos[Q.Key] = Q.Value;
	mj_forward(m, d);
}

void USlapController::WriteQposDump(const mjModel* m, const FString& RelPath)
{
	const int32 Steps = Recorded.Num() / m->nq;
	FString Out = TEXT("{\"names\": [");
	const TArray<FString> Names = QposNames(m);
	for (int32 i = 0; i < Names.Num(); ++i) Out += FString::Printf(TEXT("%s\"%s\""), i ? TEXT(", ") : TEXT(""), *Names[i]);
	Out += TEXT("], \"qpos\": [");
	for (int32 s = 0; s < Steps; ++s)
	{
		Out += s ? TEXT(", [") : TEXT("[");
		for (int32 i = 0; i < m->nq; ++i) Out += FString::Printf(TEXT("%s%.17g"), i ? TEXT(", ") : TEXT(""), Recorded[s * m->nq + i]);
		Out += TEXT("]");
	}
	Out += TEXT("]}");
	const FString Path = FPaths::Combine(FPaths::ProjectDir(), RelPath);
	FFileHelper::SaveStringToFile(Out, *Path);
	UE_LOG(LogSlap, Log, TEXT("qpos dump: %d steps -> %s"), Steps, *Path);
}

// ---------------------------------------------------------------- policy (DOCS/BRAIN_IO.md)

void USlapController::BindPolicy(mjModel* m, const FJsonObject& Fp)
{
	MaxStep = Fp.GetNumberField(TEXT("max_joint_speed_rad_s")) * 0.02;
	const TArray<TSharedPtr<FJsonValue>>& Order = Fp.GetArrayField(TEXT("joint_order"));
	const TArray<TSharedPtr<FJsonValue>>& Range = Fp.GetArrayField(TEXT("ctrl_range_rad"));
	const TSharedPtr<FJsonObject> StandRad = Fp.GetObjectField(TEXT("stand_pose_rad"));
	Action.SetNumZeroed(Order.Num());

	for (int32 b = 0; b < m->nbody; ++b)
	{
		const FString BodyName = Slap::Name(m, mjOBJ_BODY, b);
		if (!BodyName.EndsWith(TEXT("pelvis"))) continue;
		FAthlete& A = Athletes.AddDefaulted_GetRef();
		A.Prefix = BodyName.LeftChop(6);
		A.Pelvis = b;
		const int32 Root = mj_name2id(m, mjOBJ_JOINT, TCHAR_TO_UTF8(*(A.Prefix + TEXT("root"))));
		A.RootQ = m->jnt_qposadr[Root];
		A.RootV = m->jnt_dofadr[Root];
		for (int32 j = 0; j < Order.Num(); ++j)
		{
			const FString J = Order[j]->AsString();
			const int32 Act = mj_name2id(m, mjOBJ_ACTUATOR, TCHAR_TO_UTF8(*(A.Prefix + J)));
			const int32 Jnt = m->actuator_trnid[2 * Act];
			A.Act.Add(Act);
			A.Qadr.Add(m->jnt_qposadr[Jnt]);
			A.Vadr.Add(m->jnt_dofadr[Jnt]);
			A.Lo.Add(Range[j]->AsArray()[0]->AsNumber());
			A.Hi.Add(Range[j]->AsArray()[1]->AsNumber());
			double Rad = 0.0;	// exact radians from Python; a degree conversion here can differ in the last bit
			StandRad->TryGetNumberField(J, Rad);
			A.Stand.Add(Rad);
		}
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, Order.Num());
		A.Root0 = TArray<double>(m->qpos0 + A.RootQ, 7);	// foot-box position the athlete resets to
		for (int32 g = 0; g < m->ngeom; ++g)	// the foot box is the sole (Blueprint 2.1)
		{
			const FString Body = Slap::Name(m, mjOBJ_BODY, m->geom_bodyid[g]);
			if (Body == A.Prefix + TEXT("foot_l") || Body == A.Prefix + TEXT("foot_r")) A.Feet.Add(g);
		}
	}
	Floor = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("floor"));
	Podium = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("podium"));
	for (FAthlete& A : Athletes) A.Head = mj_name2id(m, mjOBJ_GEOM, TCHAR_TO_UTF8(*(A.Prefix + TEXT("head"))));
	Striker = Athletes.Num() == 2 ? FMath::RandRange(0, 1) : -1;	// coin toss
	TurnStart = 0.0;
	StruckAt = -1.0;
	if (Striker >= 0) UE_LOG(LogSlap, Log, TEXT("coin toss: %s strikes first"), *Athletes[Striker].Prefix);

	Instance.Reset();
	if (Brain)
	{
		TWeakInterfacePtr<INNERuntimeCPU> Runtime = UE::NNE::GetRuntime<INNERuntimeCPU>(TEXT("NNERuntimeORTCpu"));
		TSharedPtr<UE::NNE::IModelCPU> Model = Runtime.IsValid() ? Runtime->CreateModelCPU(Brain) : nullptr;
		Instance = Model ? Model->CreateModelInstanceCPU() : nullptr;
		if (Instance)
		{
			const uint32 Shape[] = { 1, NumObs };
			Instance->SetInputTensorShapes({ UE::NNE::FTensorShape::Make(Shape) });
		}
	}
	UE_LOG(LogSlap, Log, TEXT("Policy: %d athletes, %d joints each, brain %s"),
		Athletes.Num(), Order.Num(), Instance ? TEXT("loaded") : TEXT("MISSING (holding stand pose)"));
}

int32 USlapController::Fallen(const mjModel* m, const mjData* d, FString& Why) const
{
	for (int32 c = 0; c < d->ncon; ++c)
	{
		const mjContact& C = d->contact[c];
		const bool S0 = C.geom[0] == Floor || C.geom[0] == Podium, S1 = C.geom[1] == Floor || C.geom[1] == Podium;
		if (S0 == S1) continue;
		const int32 G = S0 ? C.geom[1] : C.geom[0];
		for (int32 a = 0; a < Athletes.Num(); ++a)
		{
			if (Athletes[a].Pelvis == m->body_rootid[m->geom_bodyid[G]] && !Athletes[a].Feet.Contains(G))
			{
				Why = FString::Printf(TEXT("knockdown: %s on %s"), *Slap::Name(m, mjOBJ_GEOM, G), *Slap::Name(m, mjOBJ_GEOM, S0 ? C.geom[0] : C.geom[1]));
				return a;
			}
		}
	}
	constexpr double MatHalf = 2.0;	// m; safety mat edge
	for (int32 a = 0; a < Athletes.Num(); ++a)
	{
		const double* P = d->qpos + Athletes[a].RootQ;
		if (FMath::Abs(P[0]) > MatHalf || FMath::Abs(P[1]) > MatHalf)
		{
			Why = TEXT("out of bounds");
			return a;
		}
	}
	return -1;
}

void USlapController::ResetAthletes(const mjModel* m, mjData* d)
{
	for (FAthlete& A : Athletes)	// reuse, never respawn
	{
		for (int32 k = 0; k < 7; ++k) d->qpos[A.RootQ + k] = A.Root0[k];
		for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0;
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			d->qpos[A.Qadr[j]] = A.Stand[j];
			d->qvel[A.Vadr[j]] = 0;
		}
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, A.Act.Num());
	}
	mj_forward(m, d);
}

void USlapController::StepPolicy(mjModel* m, mjData* d)
{
	if (!RecordFile.IsEmpty())	// live matching check: same start as training/brain_cpu.py, record 5 s, no resets
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
	}
	else if (StepCount % ControlEvery == 0)
	{
		FString Why;
		const int32 Down = Fallen(m, d, Why);
		if (Down >= 0)
		{
			UE_LOG(LogSlap, Log, TEXT("t=%.3f %s%s -> reset both athletes (#%d)"), d->time, *Athletes[Down].Prefix, *Why, ++Resets);
			NextTurn(m, d);	// a knockdown ends the turn too
		}
	}
	if (RecordFile.IsEmpty() && Striker >= 0) MatchFlow(m, d);
	if (Instance && StepCount % ControlEvery == 0)
	{
		mj_kinematics(m, d);	// positions for the current qpos (mj_step leaves xpos one step behind)
		float Obs[NumObs];
		for (FAthlete& A : Athletes)
		{
			float Value = 0.f;	// trained brains also output the critic's value (later: the HUD's win bar)
			BuildObs(m, d, A, Obs);
			const UE::NNE::FTensorBindingCPU In{ Obs, sizeof(Obs) };
			const UE::NNE::FTensorBindingCPU Out[] = { { Action.GetData(), (uint64)Action.Num() * sizeof(float) }, { &Value, sizeof(float) } };
			const int32 NumOut = Instance->GetOutputTensorDescs().Num() > 1 ? 2 : 1;
			if (Instance->RunSync({ In }, TConstArrayView<UE::NNE::FTensorBindingCPU>(Out, NumOut)) != UE::NNE::EResultStatus::Ok) continue;
			if (!RecordFile.IsEmpty() && StepCount == 0)	// live check diagnostics: first decision at full precision
			{
				FString S;
				for (int32 k = 0; k < NumObs; ++k) S += FString::Printf(TEXT("%.9g "), Obs[k]);
				UE_LOG(LogSlap, Log, TEXT("first obs %s: %s"), *A.Prefix, *S);
				S.Reset();
				for (float V : Action) S += FString::Printf(TEXT("%.9g "), V);
				UE_LOG(LogSlap, Log, TEXT("first action %s: %s"), *A.Prefix, *S);
			}
			for (int32 j = 0; j < A.Act.Num(); ++j)
			{
				const double a = FMath::Clamp((double)Action[j], -1.0, 1.0);
				const double T = FMath::Clamp(A.Stand[j] + a * 0.5 * (A.Hi[j] - A.Lo[j]), A.Lo[j], A.Hi[j]);
				A.Goal[j] = FMath::Clamp(T, A.Goal[j] - MaxStep, A.Goal[j] + MaxStep);
				A.PrevAction[j] = (float)a;
			}
		}
	}
	for (const FAthlete& A : Athletes)
	{
		for (int32 j = 0; j < A.Act.Num(); ++j) d->ctrl[A.Act[j]] = A.Goal[j];
	}
}

void USlapController::MatchFlow(const mjModel* m, mjData* d)
{
	const FAthlete& S = Athletes[Striker];
	const int32 Defender = Athletes[1 - Striker].Head;
	for (int32 c = 0; StruckAt < 0 && c < d->ncon; ++c)	// any striker part on the defender's head (training's "hit")
	{
		const mjContact& C = d->contact[c];
		const int32 Other = C.geom[0] == Defender ? C.geom[1] : C.geom[1] == Defender ? C.geom[0] : -1;
		if (Other >= 0 && m->body_rootid[m->geom_bodyid[Other]] == S.Pelvis)
		{
			StruckAt = d->time;
			UE_LOG(LogSlap, Log, TEXT("t=%.3f %s %s hits the head"), d->time, *S.Prefix, *Slap::Name(m, mjOBJ_GEOM, Other));
		}
	}
	const bool Settled = StruckAt >= 0 && d->time - StruckAt >= 1.5, Timeout = d->time - TurnStart >= 30.0;
	if (!Settled && !Timeout) return;
	UE_LOG(LogSlap, Log, TEXT("t=%.3f turn over (%s) -> %s strikes"), d->time, Settled ? TEXT("hit") : TEXT("clock"), *Athletes[1 - Striker].Prefix);
	NextTurn(m, d);
}

void USlapController::NextTurn(const mjModel* m, mjData* d)
{
	ResetAthletes(m, d);	// both back to the stand pose in their boxes, as every training episode starts
	if (Striker >= 0) Striker = 1 - Striker;
	TurnStart = d->time;
	StruckAt = -1.0;
}

void USlapController::BuildObs(const mjModel* m, const mjData* d, const FAthlete& A, float* Obs) const
{
	const int32 N = A.Act.Num();
	for (int32 j = 0; j < N; ++j)
	{
		Obs[j] = (float)(d->qpos[A.Qadr[j]] - A.Stand[j]);
		Obs[N + j] = (float)(d->qvel[A.Vadr[j]] * 0.1);
	}
	mjtNum R[9];
	mju_quat2Mat(R, d->qpos + A.RootQ + 3);	// pelvis -> world
	const double Yaw = std::atan2(R[3], R[0]);	// C runtime, as Python's math (FMath::Atan2 is UE's own approximation)
	const double C = std::cos(Yaw), S = std::sin(Yaw);
	auto Heading = [&](const double* V, float* Dst)	// world -> heading frame
	{
		Dst[0] = (float)(C * V[0] + S * V[1]);
		Dst[1] = (float)(-S * V[0] + C * V[1]);
		Dst[2] = (float)V[2];
	};
	const double* Pelvis = d->xpos + 3 * A.Pelvis;
	float* O = Obs + 2 * N;
	O[0] = (float)Pelvis[2];
	O[1] = (float)-R[6]; O[2] = (float)-R[7]; O[3] = (float)-R[8];	// R^T (0,0,-1)
	Heading(d->qvel + A.RootV, O + 4);
	for (int32 k = 0; k < 3; ++k) O[7 + k] = (float)(d->qvel[A.RootV + 3 + k] * 0.25);
	for (int32 j = 0; j < N; ++j) O[10 + j] = A.PrevAction[j];
	const bool Striking = RecordFile.IsEmpty() && Striker >= 0 && &A == &Athletes[Striker];
	O[10 + N] = Striking ? 1.f : 0.f;	// role
	O[11 + N] = Striking ? (float)(1.0 - (d->time - TurnStart) / 30.0) : 0.f;	// turn clock remaining
	for (int32 k = 15 + N; k < 18 + N; ++k) O[k] = 0.f;	// opponent hand: zero, as L2 trained alone
	if (!Striking)	// own foot-box centre (start pelvis x, y) - pelvis, heading frame; opponent hand stays zero
	{
		const double Box[3] = { A.Root0[0] - d->qpos[A.RootQ], A.Root0[1] - d->qpos[A.RootQ + 1], 0.0 };
		Heading(Box, O + 12 + N);
		return;
	}
	const double* P = d->geom_xpos + 3 * Athletes[1 - Striker].Head;	// striker: the defender's head - pelvis
	const double Rel[3] = { P[0] - Pelvis[0], P[1] - Pelvis[1], P[2] - Pelvis[2] };
	Heading(Rel, O + 12 + N);
}
