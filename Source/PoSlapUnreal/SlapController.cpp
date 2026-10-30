#include "SlapController.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/DateTime.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "MuJoCo/Core/AMjManager.h"
#include "NNE.h"
#include "NNEModelData.h"
#include "NNERuntimeBasicCpuBuilder.h"
#include "NNERuntimeCPU.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SlapNames.h"

#include <cmath>

DEFINE_LOG_CATEGORY_STATIC(LogSlap, Log, All);

namespace
{
	constexpr int32 ControlEvery = 4;	// 50 Hz brain over 200 Hz physics
	constexpr int32 NumObs = 148;		// DOCS/BRAIN_IO.md (136 base + 12 extended proprioceptive foot-ground/phase floats)
	constexpr int32 Lags[3] = { 5, 10, 15 };	// memory: inputs 102-107 as they were 0.1, 0.2, 0.3 s ago
	constexpr double HillK = 0.25;	// Hill's force-velocity curvature (brain_cpu.HILL_K)
	constexpr int32 React = 6;	// the defender sees the striker's hand 6 decisions (0.12 s) late (brain_cpu.REACT)

	void SettleMuscles(const mjModel* m, mjData* d)	// brain_cpu.settle_muscles: each muscle's filter state at its joint's angle
	{
		for (int32 a = 0; a < m->nu; ++a)
			if (m->actuator_actadr[a] >= 0) d->act[m->actuator_actadr[a]] = d->qpos[m->jnt_qposadr[m->actuator_trnid[2 * a]]];
	}
	using Slap::Rounds, Slap::MinStamina, Slap::MinNeck;

	TSharedPtr<FJsonObject> LoadJson(const FString& RelPath)
	{
		FString Text;
		TSharedPtr<FJsonObject> Obj;
		// source checkout / editor, then packaged Content/Data/<file> (Android / packaged builds), then Content/<RelPath>
		for (const FString& Path : { FPaths::Combine(FPaths::ProjectDir(), RelPath),
			FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Data"), FPaths::GetCleanFilename(RelPath)),
			FPaths::Combine(FPaths::ProjectContentDir(), RelPath) })
		{
			if (FFileHelper::LoadFileToString(Text, *Path) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Obj) && Obj) return Obj;
		}
		UE_LOG(LogSlap, Error, TEXT("Could not read %s"), *RelPath);
		return nullptr;
	}

	/** UE 5.8 has no ONNX runtime on Android: the same brain rebuilt for NNE's basic CPU runtime from the weights
	 * training/export_cpu_brains.py stages in Content/Data/Brains (normalise, clip, then the actor and critic MLPs side by
	 * side: one output, the actions then the value). Null when the file is missing or malformed. */
	UNNEModelData* RebuildForBasicCpu(const FString& Name)
	{
		TArray<uint8> Raw;
		if (!FFileHelper::LoadFileToArray(Raw, *FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Data/Brains"), Name + TEXT(".bin"))))
		{
			UE_LOG(LogSlap, Error, TEXT("No Content/Data/Brains/%s.bin (training/export_cpu_brains.py)"), *Name);
			return nullptr;
		}
		// int32 tensor count, then per tensor int32 ndims, int32 dims[ndims], float32 data: mean, std, clip lo, clip hi,
		// then (weight [out][in], bias) per linear layer, the actor's then the critic's
		int64 At = 0;
		auto Int = [&](int32& V) { if (At + 4 > Raw.Num()) return false; FMemory::Memcpy(&V, Raw.GetData() + At, 4); At += 4; return true; };
		int32 Count = 0;
		if (!Int(Count) || Count < 8 || (Count - 4) % 4) return nullptr;
		TArray<TArray<float>> T;
		TArray<TArray<int32>> Dims;
		for (int32 t = 0; t < Count; ++t)
		{
			int32 Nd = 0, Num = 1;
			if (!Int(Nd) || Nd < 1 || Nd > 2) return nullptr;
			TArray<int32>& D = Dims.AddDefaulted_GetRef();
			for (int32 k = 0; k < Nd; ++k)
			{
				int32 V = 0;
				if (!Int(V) || V < 1) return nullptr;
				D.Add(V);
				Num *= V;
			}
			if (At + 4 * (int64)Num > Raw.Num()) return nullptr;
			TArray<float>& Values = T.AddDefaulted_GetRef();
			Values.SetNumUninitialized(Num);
			FMemory::Memcpy(Values.GetData(), Raw.GetData() + At, 4 * (int64)Num);
			At += 4 * (int64)Num;
		}

		using namespace UE::NNE::RuntimeBasic;
		FModelBuilder B;
		const int32 Layers = (Count - 4) / 4;
		auto Head = [&](int32 First)
		{
			TArray<FModelBuilderElement> Seq;
			for (int32 l = 0; l < Layers; ++l)
			{
				const int32 w = First + 2 * l, O = Dims[w][0], I = Dims[w].Num() > 1 ? Dims[w][1] : 1;
				TArrayView<float> Wt = B.MakeValuesZero(I * O);	// the builder wants [in][out]
				for (int32 r = 0; r < O; ++r) for (int32 c = 0; c < I; ++c) Wt[c * O + r] = T[w][r * I + c];
				Seq.Add(B.MakeLinear(I, O, Wt, B.MakeValuesCopy(T[w + 1])));
				if (l < Layers - 1) Seq.Add(B.MakeELU(O));
			}
			return B.MakeSequence(Seq);
		};
		const uint32 In = T[0].Num();
		const FModelBuilderElement Net = B.MakeSequence({
			B.MakeNormalize(In, B.MakeValuesCopy(T[0]), B.MakeValuesCopy(T[1])),
			B.MakeClamp(In, B.MakeValuesConstant(In, T[2][0]), B.MakeValuesConstant(In, T[3][0])),
			B.MakeSpread({ Head(4), Head(4 + 2 * Layers) }) });
		TArray<uint8> File;
		File.SetNumUninitialized(B.GetWriteByteNum(Net));
		uint32 NumIn = 0, NumOut = 0;
		B.WriteFileData(File, NumIn, NumOut, Net);
		UNNEModelData* Data = NewObject<UNNEModelData>(GetTransientPackage(), FName(*Name));
		Data->Init(TEXT("ubnne"), File);
		return Data;
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
	FString EyeFlag;	// eye contact on by default; -SlapEyeContact=0 off (mirror of match.py's SLAP_EYE_CONTACT)
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapEyeContact="), EyeFlag)) bEyeContact = EyeFlag != TEXT("0");
	FString WalkFlag;	// pre-match walk-in on by default; -SlapWalkIn=0 off
	if (FParse::Value(FCommandLine::Get(), TEXT("SlapWalkIn="), WalkFlag)) bWalkIn = WalkFlag != TEXT("0");

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
		UE_LOG(LogSlap, Log, TEXT("MuJoCo options from %s: timestep %.17g, integrator %d"), *Fingerprint, m->opt.timestep, m->opt.integrator);
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

// ---------------------------------------------------------------- replay (matching check)

void USlapController::BindHillMotors(mjModel* m, const FJsonObject* const (&Fps)[2])
{
	// The replay path runs no brains, so it has no athletes - but the Hill force-velocity cap (G4) is part of the
	// PHYSICS, not of the brain: Python's replay harness applies it (brain_cpu.muscle_step) while the replayed model
	// must match it (2026-10-29: without it the replay diverged 1.2 m over 5 s). So the replay binds the same motor
	// list the policy path does, straight from the fingerprints: per joint, the actuator, its joint's dof address and
	// the joint's top speed (fp["max_joint_speed_rad_s"]).
	HillMotors.Reset();
	TArray<int32> Pelvises;
	for (const TCHAR* Corner : { TEXT("red_pelvis"), TEXT("blue_pelvis") })
		for (int32 b = 0; b < m->nbody; ++b)
			if (Slap::Name(m, mjOBJ_BODY, b).EndsWith(Corner)) Pelvises.Add(b);
	for (int32 i = 0; i < Pelvises.Num(); ++i)
	{
		const FString BodyName = Slap::Name(m, mjOBJ_BODY, Pelvises[i]);
		const FString Prefix = BodyName.LeftChop(6);	// "..._red_pelvis" -> "..._red_" (as BindPolicy does)
		const FJsonObject& Fp = *Fps[FMath::Min(i, 1)];
		const TArray<TSharedPtr<FJsonValue>>& Order = Fp.GetArrayField(TEXT("joint_order"));
		const TArray<TSharedPtr<FJsonValue>>* Speeds;
		const bool bPerJoint = Fp.TryGetArrayField(TEXT("max_joint_speed_rad_s"), Speeds);
		for (int32 j = 0; j < Order.Num(); ++j)
		{
			const FString J = Order[j]->AsString();
			const int32 Act = mj_name2id(m, mjOBJ_ACTUATOR, TCHAR_TO_UTF8(*(Prefix + J)));
			if (Act < 0) continue;	// this corner does not have that joint (a mixed pairing)
			FMotor Motor;
			Motor.Act = Act;
			Motor.Vadr = m->jnt_dofadr[m->actuator_trnid[2 * Act]];
			Motor.Vmax = bPerJoint ? (*Speeds)[j]->AsNumber() : Fp.GetNumberField(TEXT("max_joint_speed_rad_s"));
			HillMotors.Add(Motor);
		}
	}
}

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
	SettleMuscles(m, d);
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

void USlapController::BindPolicy(mjModel* m, const FJsonObject* const (&Fps)[2])
{
	// each corner its own character: joint ranges, stand pose, speed limit and card come from that athlete's fingerprint
	FString Tape[2] = { TEXT("?"), TEXT("?") }, Display[2] = { TEXT("?"), TEXT("?") }, CardName[2];
	// red, then blue, by name: a cooked build compiles the components in another order (blue's body first on Android),
	// and everything indexed by corner (walk-in side, fingerprint, turns) assumes 0 = red
	TArray<int32> Pelvises;
	for (const TCHAR* Corner : { TEXT("red_pelvis"), TEXT("blue_pelvis") })
	{
		for (int32 b = 0; b < m->nbody; ++b)
		{
			if (Slap::Name(m, mjOBJ_BODY, b).EndsWith(Corner)) Pelvises.Add(b);
		}
	}
	for (const int32 b : Pelvises)
	{
		const FString BodyName = Slap::Name(m, mjOBJ_BODY, b);
		const int32 c = FMath::Min(Athletes.Num(), 1);
		const FJsonObject& Fp = *Fps[c];
		const TArray<TSharedPtr<FJsonValue>>& Order = Fp.GetArrayField(TEXT("joint_order"));
		const TArray<TSharedPtr<FJsonValue>>& Range = Fp.GetArrayField(TEXT("ctrl_range_rad"));
		const TSharedPtr<FJsonObject> StandRad = Fp.GetObjectField(TEXT("stand_pose_rad"));
		Action.SetNumZeroed(Order.Num());
		// the character card is the source of truth (the fingerprint keeps a build-time copy): fatigue, display name
		CardName[c] = Fp.GetObjectField(TEXT("card"))->GetStringField(TEXT("name"));
		const TSharedPtr<FJsonObject> Card = LoadJson(TEXT("training/characters/") + CardName[c] + TEXT(".json"));
		if (Card)
		{
			Tape[c] = FString::Printf(TEXT("%.2f m  %.0f kg  STR %.2f  SPD %.2f  TGH %.2f"), Card->GetNumberField(TEXT("height_m")), Card->GetNumberField(TEXT("mass_kg")),
				Card->GetNumberField(TEXT("strength")), Card->GetNumberField(TEXT("speed")), Card->GetNumberField(TEXT("toughness")));
			Display[c] = Card->GetStringField(TEXT("display"));
		}
		FAthlete& A = Athletes.AddDefaulted_GetRef();
		const TArray<TSharedPtr<FJsonValue>>* Speeds;	// per joint, joint order (a single number in older fingerprints)
		const bool bPerJoint = Fp.TryGetArrayField(TEXT("max_joint_speed_rad_s"), Speeds);
		for (int32 j = 0; j < Order.Num(); ++j)
		{
			A.Vmax.Add(bPerJoint ? (*Speeds)[j]->AsNumber() : Fp.GetNumberField(TEXT("max_joint_speed_rad_s")));	// Hill's top speed
			A.MaxStep.Add(A.Vmax.Last() * 0.02);
		}
		A.Prefix = BodyName.LeftChop(6);
		A.Name = A.Prefix.LeftChop(1);
		A.Name = A.Name.Mid(A.Name.Find(TEXT("_"), ESearchCase::CaseSensitive, ESearchDir::FromEnd) + 1);	// "Arena_red" -> "red"
		A.Pelvis = b;
		A.Chest = mj_name2id(m, mjOBJ_BODY, TCHAR_TO_UTF8(*(A.Prefix + TEXT("chest"))));	// eye contact aims in the CHEST frame (the neck hinges hang off it; the waist ball separates it from the pelvis)
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
			if (J.StartsWith(TEXT("neck")))
			{
				A.NeckAct.Add(Act);
				A.Fr0.Append({ m->actuator_forcerange[2 * Act], m->actuator_forcerange[2 * Act + 1] });
				if (J == TEXT("neck_turn")) A.YawIdx = j;
				if (J == TEXT("neck_nod")) A.PitchIdx = j;
			}
			if (J == TEXT("hip_flex_l")) A.HipL = j;
			else if (J == TEXT("hip_flex_r")) A.HipR = j;
			else if (J == TEXT("knee_l")) A.KneeL = j;
			else if (J == TEXT("knee_r")) A.KneeR = j;
			else if (J == TEXT("ankle_flex_l")) A.AnkleL = j;
			else if (J == TEXT("ankle_flex_r")) A.AnkleR = j;
			else if (J == TEXT("shoulder_swing_l")) A.ShSwingL = j;
			else if (J == TEXT("shoulder_swing_r")) A.ShSwingR = j;
			else if (J == TEXT("waist_twist")) A.WaistTwist = j;
			else if (J == TEXT("elbow_r")) A.ElbowR = j;
		}
		for (int32 j = 0; j < A.Act.Num() && j < A.Vmax.Num(); ++j)
		{
			FMotor Motor;	// the Hill cap's list (ApplyHill): this joint's actuator, dof address and top speed
			Motor.Act = A.Act[j];
			Motor.Vadr = A.Vadr[j];
			Motor.Vmax = A.Vmax[j];
			HillMotors.Add(Motor);
		}
		for (int32 k = 0; k < m->nbody; ++k)	// the brain X-ray's ghost: every body of this athlete, linked to its parent
		{
			if (m->body_rootid[k] != b) continue;
			A.BodyParent.Add(A.Bodies.Find(m->body_parentid[k]));
			A.Bodies.Add(k);
		}
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, Order.Num());
		if (Card)
		{
			A.StaminaPerStrike = Card->GetNumberField(TEXT("stamina_per_strike"));
			A.NeckDamage = Card->GetNumberField(TEXT("neck_damage_per_snap"));
			A.StrikeMass = Card->GetNumberField(TEXT("strike_mass"));
			Card->TryGetNumberField(TEXT("faint_chance_per_turn"), A.FaintChance);
			Card->TryGetNumberField(TEXT("faint_seconds"), A.FaintSeconds);
			Card->TryGetNumberField(TEXT("faint_strength"), A.FaintStrength);
			double Walk = 1.2;	// card walk_speed_mps scales the walk-in command (locomotion.py speed_scale: / 1.2)
			Card->TryGetNumberField(TEXT("walk_speed_mps"), Walk);
			A.WalkSpeed = WalkSpeed * Walk / 1.2;
		}
		for (const int32 Act : A.Act) A.FrAll.Append({ m->actuator_forcerange[2 * Act], m->actuator_forcerange[2 * Act + 1] });
		auto Geom = [&](const TCHAR* N) { return mj_name2id(m, mjOBJ_GEOM, TCHAR_TO_UTF8(*(A.Prefix + N))); };
		A.Feet = { Geom(TEXT("foot_l")), Geom(TEXT("foot_r")) };	// the foot box is the sole (Blueprint 2.1)
		A.Head = Geom(TEXT("head"));
		A.HeadBody = m->geom_bodyid[A.Head];
		A.Hand = Geom(TEXT("hand_r"));
		A.Palms = { Geom(TEXT("hand_l")), A.Hand };
	}
	Floor = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("floor"));
	Podium = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("podium"));
	bInTurn = false;
	MatchNo = 0;
	HoldLeft = 0;

	// one brain per corner (roster): the asset, or an .onnx file from the command line (tournament replays, checks)
	auto FromFile = [this](const TCHAR* Key, UNNEModelData* Asset, FString& Label) -> UNNEModelData*
	{
		FString Path;
		TArray<uint8> Bytes;
		Label = Asset ? Asset->GetName() : TEXT("-");
		if (!FParse::Value(FCommandLine::Get(), Key, Path)) return Asset;
		if (!FFileHelper::LoadFileToArray(Bytes, *FPaths::Combine(FPaths::ProjectDir(), Path)))
		{
			UE_LOG(LogSlap, Error, TEXT("%s%s: no such file"), Key, *Path);
			return Asset;
		}
		UNNEModelData* Data = NewObject<UNNEModelData>(this);
		Data->Init(TEXT("onnx"), Bytes);
		LoadedBrains.Add(Data);
		Label = FPaths::GetBaseFilename(Path);
		return Data;
	};
	FString Labels[2];
	UNNEModelData* Red = FromFile(TEXT("SlapRedBrain="), Brain.Get(), Labels[0]);
	UNNEModelData* Blue = FromFile(TEXT("SlapBlueBrain="), BlueBrain.Get(), Labels[1]);
	if (!Blue)	// no brain of his own: red's
	{
		Blue = Red;
		Labels[1] = Labels[0];
	}
	for (int32 a = 0; a < Athletes.Num(); ++a)
	{
		FAthlete& A = Athletes[a];
		FNet N;
		if (!MakeNet(a == 1 ? Blue : Red, N)) continue;	// athletes bind red, then blue (BindPolicy)
		A.Net = N.Net;
		A.NumIn = N.NumIn;
		A.NumOut = N.NumOut;
		UE_LOG(LogSlap, Log, TEXT("Policy: %s brain %s reads %d inputs"), *A.Name, *Labels[FMath::Min(a, 1)], A.NumIn);
	}
	// The walk-in on each character's walking brain (S2, the cooked /Game/Brains/<card>_S2, so Android has it too; user
	// 2026-10-29: the walk-in is pure RL); a character without one gets the scripted stride. -SlapWalkBrain=0: scripted for all
	FString WalkFlag;
	const bool bWalkBrains = !(FParse::Value(FCommandLine::Get(), TEXT("SlapWalkBrain="), WalkFlag) && WalkFlag == TEXT("0"));
	FString Walkers;
	for (int32 a = 0; a < Athletes.Num() && bWalkBrains; ++a)
	{
		const FString Name = CardName[FMath::Min(a, 1)] + TEXT("_S2");
		UNNEModelData* Data = LoadObject<UNNEModelData>(nullptr, *FString::Printf(TEXT("/Game/Brains/%s.%s"), *Name, *Name), nullptr, LOAD_NoWarn);
		FNet N;
		if (!Data || !MakeNet(Data, N)) continue;
		LoadedBrains.Add(Data);
		Athletes[a].WalkNet = N.Net;
		Athletes[a].WalkIn = N.NumIn;
		Athletes[a].WalkOut = N.NumOut;
		Walkers += (Walkers.IsEmpty() ? TEXT("") : TEXT(", ")) + Athletes[a].Name;
	}
	UE_LOG(LogSlap, Log, TEXT("Walk-in: walking brains for %s"), Walkers.IsEmpty() ? TEXT("nobody (scripted stride)") : *Walkers);
	if (GhostD) mj_deleteData(GhostD);
	GhostD = mj_makeData(m);
	UE_LOG(LogSlap, Log, TEXT("Policy: %d athletes (%s vs %s), %d joints each, brain %s, defender role %d"), Athletes.Num(), *CardName[0], *CardName[1], Action.Num(),
		Athletes.Num() && Athletes[0].Net ? TEXT("loaded") : TEXT("MISSING (holding stand pose)"), DefenderRole);
	SetHud([&](FSlapHud& H)
	{
		for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
		{
			H.Name[a] = Display[a] + TEXT(" ") + Athletes[a].Name.ToUpper();
			H.Brain[a] = Labels[a];
			H.Tape[a] = Tape[a];
			H.Card[a] = CardName[a];
			H.StrikeMass[a] = Athletes[a].StrikeMass;
			H.GhostParent[a] = Athletes[a].BodyParent;
		}
		H.WalkNote = Walkers;
	});
}

bool USlapController::MakeNet(UNNEModelData* Data, FNet& Out)
{
	if (!Data) return false;

	// ORT on desktop (the bit-exact matching check); without it (Android) the brain rebuilt for the basic CPU runtime.
	// -SlapBasicCpu takes Android's path on desktop and logs how far it lands from ORT on one made-up observation
	auto Make = [](const TCHAR* RuntimeName, UNNEModelData* D, FNet& N)
	{
		TWeakInterfacePtr<INNERuntimeCPU> R = UE::NNE::GetRuntime<INNERuntimeCPU>(RuntimeName);
		TSharedPtr<UE::NNE::IModelCPU> Model = R.IsValid() && D ? R->CreateModelCPU(D) : nullptr;
		N.Net = Model ? Model->CreateModelInstanceCPU() : nullptr;
		if (!N.Net) return false;
		const TConstArrayView<int32> In = N.Net->GetInputTensorDescs()[0].GetShape().GetData();
		N.NumIn = FMath::Min(NumObs, In.Num() > 1 && In[1] > 0 ? In[1] : 108);
		const uint32 Shape[] = { 1, (uint32)N.NumIn };
		N.Net->SetInputTensorShapes({ UE::NNE::FTensorShape::Make(Shape) });
		N.NumOut = N.Net->GetOutputTensorDescs().Num() > 1 ? 2 : 1;
		return true;
	};
	Out.Label = Data->GetName();
	FNet Ort;
	const bool bOrt = Make(TEXT("NNERuntimeORTCpu"), Data, Ort);
	if (bOrt && !FParse::Param(FCommandLine::Get(), TEXT("SlapBasicCpu")))
	{
		Out.Net = Ort.Net;
		Out.NumIn = Ort.NumIn;
		Out.NumOut = Ort.NumOut;
		return true;
	}
	if (!Make(TEXT("NNERuntimeBasicCpu"), RebuildForBasicCpu(Data->GetName()), Out)) return false;
	Out.NumOut = 0;	// one output: the actions, then the value
	// probe: one made-up observation, logged on every platform (compare a device's line with desktop's)
	TArray<float> Obs, A, B;
	for (int32 k = 0; k < Out.NumIn; ++k) Obs.Add(2.f * FMath::Sin(1.7f * k + 0.3f));
	A.SetNumZeroed(31);
	B.SetNumZeroed(31);
	const UE::NNE::FTensorBindingCPU In{ Obs.GetData(), (uint64)Obs.Num() * sizeof(float) };
	const bool bProbe = Out.Net->RunSync({ In }, { { B.GetData(), 31 * sizeof(float) } }) == UE::NNE::EResultStatus::Ok;
	double Sum = 0.0;
	for (const float V : B) Sum += FMath::Abs(V);
	UE_LOG(LogSlap, Log, TEXT("Basic CPU brain %s probe: %s, sum |out| %.6f, action 0 %.6f, value %.4f"), *Out.Label, bProbe ? TEXT("ran") : TEXT("FAILED TO RUN"), Sum, B[0], B[30]);
	if (bOrt)
	{
		const UE::NNE::FTensorBindingCPU OrtOut[] = { { A.GetData(), 30 * sizeof(float) }, { &A[30], sizeof(float) } };
		const bool bRan = bProbe && Ort.Net->RunSync({ In }, TConstArrayView<UE::NNE::FTensorBindingCPU>(OrtOut, Ort.NumOut)) == UE::NNE::EResultStatus::Ok;
		float Worst = 0.f;
		for (int32 k = 0; k < (Ort.NumOut > 1 ? 31 : 30); ++k) Worst = FMath::Max(Worst, FMath::Abs(A[k] - B[k]) / FMath::Max(1.f, FMath::Abs(A[k])));
		UE_LOG(LogSlap, Log, TEXT("Basic CPU brain %s vs ORT: %s, max relative difference %g (actions %g / %g, value %g / %g)"), *Out.Label,
			bRan ? TEXT("ran") : TEXT("FAILED TO RUN"), Worst, A[0], B[0], A[30], B[30]);
	}
	return true;
}

bool USlapController::Infer(const FAthlete& A, const float* InObs, float& Value)
{
	return RunNet(*A.Net, A.NumIn, A.NumOut, InObs, Value);
}

bool USlapController::RunNet(UE::NNE::IModelInstanceCPU& Net, int32 NumIn, int32 NumOut, const float* InObs, float& Value)
{
	const UE::NNE::FTensorBindingCPU In{ (void*)InObs, (uint64)NumIn * sizeof(float) };
	if (NumOut == 0)	// the rebuilt brain: one output, the actions then the value
	{
		Action.SetNumUninitialized(Action.Num() + 1, EAllowShrinking::No);
		const bool bOk = Net.RunSync({ In }, { { Action.GetData(), (uint64)Action.Num() * sizeof(float) } }) == UE::NNE::EResultStatus::Ok;
		Value = Action.Pop(EAllowShrinking::No);
		return bOk;
	}
	const UE::NNE::FTensorBindingCPU Out[] = { { Action.GetData(), (uint64)Action.Num() * sizeof(float) }, { &Value, sizeof(float) } };
	return Net.RunSync({ In }, TConstArrayView<UE::NNE::FTensorBindingCPU>(Out, NumOut)) == UE::NNE::EResultStatus::Ok;
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

namespace
{
	double Dist(const double* A, const double* B, int32 N)	// as match.py dist(): plain sum, same rounding
	{
		double S = 0.0;
		for (int32 k = 0; k < N; ++k) S += (A[k] - B[k]) * (A[k] - B[k]);
		return std::sqrt(S);
	}

	double RoundTo(double X, int32 Digits)	// Python round(x, n): the double nearest the correctly rounded decimal
	{
		return FCString::Atod(*FString::Printf(TEXT("%.*f"), Digits, X));
	}

	FString Part(const FString& Geom)	// match.py: re.sub(r"^.*?(red|blue)_", "", name)
	{
		const int32 R = Geom.Find(TEXT("red_"), ESearchCase::CaseSensitive), B = Geom.Find(TEXT("blue_"), ESearchCase::CaseSensitive);
		if (R < 0 && B < 0) return Geom;
		return (B < 0 || (R >= 0 && R < B)) ? Geom.Mid(R + 4) : Geom.Mid(B + 5);
	}

	FString Num(double X, int32 Digits) { return FString::Printf(TEXT("%.*f"), Digits, X); }

	uint32 Mix(std::initializer_list<uint32> V)	// the match's dice: match.py mix(), Web/sim.js mix(), the same bits
	{
		uint32 H = 0x9E3779B9u;
		for (const uint32 X : V)
		{
			H ^= X;
			H *= 0x85EBCA6Bu;
			H ^= H >> 13;
			H *= 0xC2B2AE35u;
			H ^= H >> 16;
		}
		return H;
	}
	FString Str(const FString& S) { return TEXT("\"") + S + TEXT("\""); }
	FString Bool(bool B) { return B ? TEXT("true") : TEXT("false"); }
	FString CardsJson(const int32 (&C)[3][2]) { return FString::Printf(TEXT("[[%d, %d], [%d, %d], [%d, %d]]"), C[0][0], C[0][1], C[1][0], C[1][1], C[2][0], C[2][1]); }

	// an action in [-1, 1] -> joint target: +-1 reaches the range ends from the stand pose (each side its own scale), times Reach
	double Target(double Stand, double Lo, double Hi, double Act, double Reach = 1.0)
	{
		return FMath::Clamp(Stand + Reach * (Act > 0.0 ? Act * (Hi - Stand) : Act * (Stand - Lo)), Lo, Hi);
	}

	using Slap::StrikeClock, Slap::Recovery;
	constexpr double BoxX = 0.20, BoxY = 0.25;
	constexpr int64 KickSteps = 2;	// the slap's impact impulse lasts 10 ms (match.py KICK_STEPS)
	constexpr int32 Weights[3][4] = { { 2, 1, 1, 1 }, { 1, 2, 1, 1 }, { 1, 1, 2, 2 } };	// judges: speed, head, stagger, defence
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

FString USlapController::Down(const mjModel* m, const mjData* d, const FAthlete& A, bool bDefender) const
{
	for (int32 c = 0; c < d->ncon; ++c)
	{
		const mjContact& C = d->contact[c];
		const bool S0 = C.geom[0] == Floor || C.geom[0] == Podium, S1 = C.geom[1] == Floor || C.geom[1] == Podium;
		if (S0 == S1) continue;
		const int32 G = S0 ? C.geom[1] : C.geom[0];
		if (bDefender && (C.geom[0] == Podium || C.geom[1] == Podium) && A.Palms.Contains(G)) continue;	// his brace (match.py down())
		if (m->body_rootid[m->geom_bodyid[G]] == A.Pelvis && !A.Feet.Contains(G)) return Slap::Name(m, mjOBJ_GEOM, G);
	}
	return FString();
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
	bInTurn = false;
	HoldLeft = 0;
	WalkInLeft = 0;
	Round = Rounds + 1;	// StepPolicy starts a new match at once
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
	FMemory::Memzero(Cards);
	RoundTurns.Reset();
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
		}
		PlaceWalkers(m, d);
		StepWalkIn(m, d);	// sets the walk-in HUD state
	}
	else StartTurn(m, d);
	SetHud([&](FSlapHud& H)	// after StartTurn: a turn clears the last turn's call, the toss is this one's
	{
		H.Match = MatchNo; ++H.MatchSeq; H.Result.Reset(); H.ScoredRound = 0; FMemory::Memzero(H.Cards);
		H.Round = 1; H.Striker = Athletes[First].Name; H.Defender = Athletes[1 - First].Name;
		H.Event = Athletes[First].Name + TEXT(" wins the toss");
	});
}

void USlapController::HudBalance(const mjModel* m, mjData* d, FSlapHud& H)
{
	mj_subtreeVel(m, d);	// centre-of-mass velocities (sensor outputs only: the dynamics never read them)
	H.Pose.Reset();
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		const FAthlete& X = Athletes[a];
		H.Com[a] = MjUtils::MjToUEPosition(d->subtree_com + 3 * X.Pelvis);
		H.Feet[a] = 0.5 * (MjUtils::MjToUEPosition(d->geom_xpos + 3 * X.Feet[0]) + MjUtils::MjToUEPosition(d->geom_xpos + 3 * X.Feet[1]));
		double Cp[2];
		H.Danger[a] = DangerNow[a] = FMath::Clamp(Wobble(m, d, X, Cp) / 0.15, 0.0, 1.0);
		const double Cp3[3] = { Cp[0], Cp[1], 0.0 };
		H.Capture[a] = MjUtils::MjToUEPosition(Cp3);
		for (int32 k = 0; k < 7; ++k) H.Pose.Add((float)d->qpos[X.RootQ + k]);
		for (const int32 Q : X.Qadr) H.Pose.Add((float)d->qpos[Q]);
	}
}

double USlapController::Wobble(const mjModel* m, const mjData* d, const FAthlete& A, double (&Cp)[2]) const
{
	// KO danger (display only): the capture point - where the centre of mass would come to rest over a step, x + v / w0
	// with w0 = sqrt(g / height) - against the support polygon of the planted soles. Outside it, standing still is no
	// longer possible: he has to step or fall.
	const double* C = d->subtree_com + 3 * A.Pelvis;
	const double* V = d->subtree_linvel + 3 * A.Pelvis;
	const double W0 = std::sqrt(-m->opt.gravity[2] / FMath::Max(C[2], 0.2));
	Cp[0] = C[0] + V[0] / W0;
	Cp[1] = C[1] + V[1] / W0;
	TArray<FVector2D, TInlineAllocator<8>> P;	// the bottom corners of every planted sole box
	for (const int32 G : A.Feet)
	{
		const double* X = d->geom_xpos + 3 * G;
		const double* R = d->geom_xmat + 9 * G;
		const double* Sz = m->geom_size + 3 * G;
		bool bPlanted = false;	// a floor contact on this sole (a height test misses tall foot boxes)
		for (int32 c = 0; c < d->ncon && !bPlanted; ++c) bPlanted = (d->contact[c].geom[0] == G || d->contact[c].geom[1] == G) && (d->contact[c].geom[0] == Floor || d->contact[c].geom[1] == Floor);
		if (!bPlanted) continue;
		for (int32 k = 0; k < 4; ++k)
		{
			const double Lx = (k & 1) ? Sz[0] : -Sz[0], Ly = (k & 2) ? Sz[1] : -Sz[1];
			P.Emplace(X[0] + R[0] * Lx + R[1] * Ly, X[1] + R[3] * Lx + R[4] * Ly);
		}
	}
	if (P.Num() < 4) return 1.0;	// both feet off the floor
	// convex hull (Andrew's monotone chain), counter-clockwise
	P.Sort([](const FVector2D& L, const FVector2D& R) { return L.X < R.X || (L.X == R.X && L.Y < R.Y); });
	auto Cross = [](const FVector2D& O, const FVector2D& A2, const FVector2D& B) { return (A2.X - O.X) * (B.Y - O.Y) - (A2.Y - O.Y) * (B.X - O.X); };
	TArray<FVector2D, TInlineAllocator<16>> Hull;
	for (int32 Pass = 0; Pass < 2; ++Pass)
	{
		const int32 Start = Hull.Num();
		for (int32 i = 0; i < P.Num(); ++i)
		{
			const FVector2D& Q = P[Pass ? P.Num() - 1 - i : i];
			while (Hull.Num() >= Start + 2 && Cross(Hull[Hull.Num() - 2], Hull.Last(), Q) <= 0.0) Hull.Pop(EAllowShrinking::No);
			Hull.Add(Q);
		}
		Hull.Pop(EAllowShrinking::No);
	}
	const FVector2D X(Cp[0], Cp[1]);
	bool bInside = true;
	double Gap = TNumericLimits<double>::Max();
	for (int32 i = 0; i < Hull.Num(); ++i)
	{
		const FVector2D& E0 = Hull[i];
		const FVector2D& E1 = Hull[(i + 1) % Hull.Num()];
		bInside &= Cross(E0, E1, X) >= 0.0;
		const FVector2D E = E1 - E0;
		const double T = FMath::Clamp(FVector2D::DotProduct(X - E0, E) / FMath::Max(E.SizeSquared(), 1e-12), 0.0, 1.0);
		Gap = FMath::Min(Gap, FVector2D::Distance(X, E0 + T * E));
	}
	return bInside ? 0.0 : Gap;
}

void USlapController::PlaceWalkers(const mjModel* m, mjData* d)
{
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		FAthlete& A = Athletes[a];
		if (!A.WalkNet) continue;
		for (int32 k = 0; k < 7; ++k) d->qpos[A.RootQ + k] = m->qpos0[A.RootQ + k];
		d->qpos[A.RootQ] += a == 0 ? -Aisle : Aisle;	// red comes in from -X, blue from +X
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			d->qpos[A.Qadr[j]] = A.Stand[j];
			d->qvel[A.Vadr[j]] = 0.0;
		}
		for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0.0;
	}
	SettleMuscles(m, d);
	mj_forward(m, d);
}

void USlapController::StepWalkIn(const mjModel* m, mjData* d)
{
	// Pre-match walk-in. An athlete with a walking brain (S2) walks the aisle on it - MuJoCo moves him; the brain gets a
	// speed and a heading toward his foot box, as in training (envs/locomotion.py) - and his match brain takes over, on
	// guard, once he is there. Without one, the scripted stride: the root slides in from 1.6 m while the legs swing on a
	// clock. Display only either way: when the walk-in ends, StartTurn() resets mjData to the exact stand pose (parity).
	const double Prog = FMath::Clamp((WalkInTotal - WalkInLeft) * m->opt.timestep / FMath::Max(WalkInHold, 0.1), 0.0, 1.0);	// the scripted stride's
	const double S = FMath::Clamp(Prog / 0.85, 0.0, 1.0);
	const double U = S * S * (3.0 - 2.0 * S);
	const double Offset = 1.60 * (1.0 - U);
	const double Env = Prog < 0.85 ? FMath::Sin(PI * S) : 0.0;
	const double Phase = 2.0 * PI * 1.8 * Prog * WalkInHold;
	float Obs[NumObs];
	const bool bRunNet = StepCount % ControlEvery == 0;	// 50 Hz, also while WalkInLeft holds still (level load)
	const bool bReady = bHudReady.load(std::memory_order_relaxed);
	if (!bReady) PlaceWalkers(m, d);	// the level is still loading: the walkers wait at the top of the aisle
	FString Note;
	bool bDone = true;	// everybody at the box or down
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		FAthlete& A = Athletes[a];
		if (A.WalkNet)
		{
			bDone &= A.bArrived || A.bFell;
			if (!bReady || !bRunNet || A.bFell) continue;	// a fallen walker lies where he fell until the turn's reset
			const FString Fell = Down(m, d, A, false);
			if (!Fell.IsEmpty() || d->xpos[3 * A.Pelvis + 2] < 0.45)	// locomotion.py's fall
			{
				UE_LOG(LogSlap, Log, TEXT("walk-in: %s down after %d decisions (%s, pelvis %.2f m, %.2f m from the box)"), *A.Name, A.WalkT,
					Fell.IsEmpty() ? TEXT("pelvis low") : *Part(Fell), d->xpos[3 * A.Pelvis + 2], FMath::Abs(m->qpos0[A.RootQ] - d->xpos[3 * A.Pelvis]));
				A.bFell = true;
				Note = A.Name.ToUpper() + TEXT(" stumbles on the walk-in!");
				continue;
			}
			const double* P = d->xpos + 3 * A.Pelvis;
			const double Gx = m->qpos0[A.RootQ] - P[0], Gy = m->qpos0[A.RootQ + 1] - P[1];
			if (!A.bArrived && (a == 0 ? Gx : -Gx) < 0.15)
			{
				UE_LOG(LogSlap, Log, TEXT("walk-in: %s at the box after %.1f s"), *A.Name, A.WalkT * 0.02);
				A.bArrived = true;
			}
			float Value = 0.f;
			{	// the walking brain to the box and then standing in it (command 0, as S2 trains) until the first turn's reset:
				// a match brain taking over mid-stride dropped Kim while she waited for Grandpa (2026-10-29)
				mjtNum R[9];
				mju_quat2Mat(R, d->qpos + A.RootQ + 3);
				const double DYaw = std::atan2(Gy, Gx) - std::atan2(R[3], R[0]);
				// locomotion.py _command: full speed, slowing over the last 0.8 m, 0 inside 0.15 m (along the aisle)
				const double Rem = a == 0 ? Gx : -Gx;
				const double V = Rem < 0.15 ? 0.0 : A.WalkSpeed * FMath::Clamp(Rem / 0.8, 0.0, 1.0);
				const float Cmd[6] = { (float)(V * std::cos(DYaw)), (float)(V * std::sin(DYaw)), 0.f,
					(float)std::sin(2.0 * PI * A.WalkPhase), (float)std::cos(2.0 * PI * A.WalkPhase), (float)(V / 3.0) };
				BuildObs(m, d, A, 0.f, 0.f, nullptr, Obs, Cmd);
				Obs[3 * A.Act.Num() + 11] = (float)(1.0 - A.WalkT / 1000.0);	// locomotion.py feeds its episode clock at role 0 (BuildObs O[11 + N])
				if (!RunNet(*A.WalkNet, A.WalkIn, A.WalkOut, Obs, Value)) continue;
				A.WalkPhase = V > 0.1 ? FMath::Fmod(A.WalkPhase + 1.8 * 0.02, 1.0) : 0.0;	// locomotion.py: the clock rests at 0 below 0.1 m/s
				++A.WalkT;
			}
			for (int32 j = 0; j < A.Act.Num(); ++j)	// Decide's mapping and rate limit; the walking brain trained with an 0.85 EMA on top
			{
				const double Step = A.MaxStep[j] * A.Stamina;
				const double Act = FMath::Clamp((double)Action[j], -1.0, 1.0);
				const double Tgt = FMath::Clamp(Target(A.Stand[j], A.Lo[j], A.Hi[j], Act), A.Goal[j] - Step, A.Goal[j] + Step);
				A.Goal[j] = 0.85 * Tgt + 0.15 * A.Goal[j];
				A.PrevAction[j] = (float)Act;
			}
			continue;
		}
		bDone &= Prog >= 1.0;
		const double Sign = a == 0 ? -1.0 : 1.0;
		d->qpos[A.RootQ] = m->qpos0[A.RootQ] + Sign * Offset;
		d->qpos[A.RootQ + 1] = m->qpos0[A.RootQ + 1];
		d->qpos[A.RootQ + 2] = m->qpos0[A.RootQ + 2] + 0.015 * Env * FMath::Abs(FMath::Sin(Phase));
		for (int32 k = 3; k < 7; ++k) d->qpos[A.RootQ + k] = m->qpos0[A.RootQ + k];
		for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0.0;
		if (bRunNet && A.Net)
		{
			BuildObs(m, d, A, 0.f, 0.f, nullptr, Obs);
			float Value = 0.f;
			if (Infer(A, Obs, Value))
			{
				for (int32 j = 0; j < A.Act.Num(); ++j)
				{
					const double Act = FMath::Clamp((double)Action[j], -1.0, 1.0);
					const double Tgt = Target(A.Stand[j], A.Lo[j], A.Hi[j], Act, 0.25);
					d->qpos[A.Qadr[j]] = Tgt;
					A.Goal[j] = Tgt;
					A.PrevAction[j] = (float)Act;
				}
			}
		}
		const double SinP = FMath::Sin(Phase + (a == 1 ? PI * 0.5 : 0.0));
		const double CosP = FMath::Cos(Phase + (a == 1 ? PI * 0.5 : 0.0));
		auto SetJ = [&](int32 Idx, double Delta)
		{
			if (Idx < 0) return;
			const double Val = FMath::Clamp(A.Stand[Idx] + Env * Delta, A.Lo[Idx], A.Hi[Idx]);
			d->qpos[A.Qadr[Idx]] = Val;
			A.Goal[Idx] = Val;
		};
		SetJ(A.HipL, 0.38 * SinP);
		SetJ(A.HipR, -0.38 * SinP);
		SetJ(A.KneeL, 0.55 * FMath::Max(0.0, SinP));
		SetJ(A.KneeR, 0.55 * FMath::Max(0.0, -SinP));
		SetJ(A.AnkleL, 0.18 * CosP);
		SetJ(A.AnkleR, -0.18 * CosP);
		SetJ(A.ShSwingL, -0.30 * SinP);
		SetJ(A.ShSwingR, 0.30 * SinP);
	}
	if (!(Athletes[0].WalkNet && Athletes[1].WalkNet)) mj_forward(m, d);	// a scripted athlete was placed
	double Walked = 1.0;	// the HUD's progress: the slower of the two
	for (const FAthlete& A : Athletes)
	{
		const double Left = FMath::Abs(m->qpos0[A.RootQ] - d->xpos[3 * A.Pelvis]);
		Walked = FMath::Min(Walked, A.WalkNet ? (A.bArrived || A.bFell ? 1.0 : FMath::Clamp(1.0 - Left / Aisle, 0.0, 1.0)) : Prog);
	}
	if (bDone && bReady) WalkInLeft = 1;	// StepPolicy's decrement ends it: the first turn starts on the next step
	SetHud([&](FSlapHud& H)
	{
		H.bWalkIn = true;
		H.WalkInProgress = Walked;
		if (!Note.IsEmpty()) H.Event = Note;
		HudBalance(m, d, H);
	});
}

void USlapController::StartTurn(mjModel* m, mjData* d)
{
	Turn = FTurn();
	Turn.Striker = TurnInRound == 0 ? First : 1 - First;
	mj_resetData(m, d);	// as brain_cpu.stand(): reset, stand pose, forward (then match.py forwards once more)
	for (FAthlete& A : Athletes)
	{
		for (int32 j = 0; j < A.Act.Num(); ++j) d->qpos[A.Qadr[j]] = A.Stand[j];
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, A.Act.Num());
		A.bFresh = true;
		SetForce(m, A, 1.0);	// back from any faint; damage: a weaker neck (match.py: forcerange = own x neck strength)
	}
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
	SettleMuscles(m, d);
	mj_forward(m, d);
	mj_forward(m, d);
	for (int32 a = 0; a < 2 && Athletes.Num() == 2; ++a)	// eye contact: world gaze biases start on the eye line
	{
		FAthlete& W = Athletes[a];
		const FAthlete& Fo = Athletes[1 - a];
		double Dx = d->geom_xpos[3 * Fo.Head] - d->geom_xpos[3 * W.Head];
		double Dy = d->geom_xpos[3 * Fo.Head + 1] - d->geom_xpos[3 * W.Head + 1];
		double Dz = d->geom_xpos[3 * Fo.Head + 2] - d->geom_xpos[3 * W.Head + 2];
		const double L = FMath::Sqrt(Dx * Dx + Dy * Dy + Dz * Dz);
		if (L > 1e-9) { W.BiasWX = Dx / L; W.BiasWY = Dy / L; W.BiasWZ = Dz / L; }
	}
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
		for (const FAthlete* X : { &D, &S })
		{
			const FString Geom = Down(m, d, *X, X == &D);
			if (Geom.IsEmpty()) continue;
			Emit(FString::Printf(TEXT("{\"type\": \"knockdown\", \"t\": %s, \"athlete\": %s, \"part\": %s}"), *Num(T, 3), *Str(X->Name), *Str(Part(Geom))));
			UE_LOG(LogSlap, Log, TEXT("t=%.3f knockdown: %s %s"), T, *X->Name, *Part(Geom));
			if (X == &D && Turn.TouchStep >= 0)
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
			Round, *CardsJson(Cards)));
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
		Turn.Speed = RoundTo(Speed, 2);
		// the body behind the palm (match.py): the striker's card mass times the hand's velocity, over KickSteps
		const double* Ci = d->xipos + 3 * D.HeadBody;
		mjtNum R[3];
		mju_sub3(R, C.pos, Ci);
		double* K = Turn.Kick;
		for (int32 i = 0; i < 3; ++i) K[i] = Travel[i] / m->opt.timestep * (S.StrikeMass / (KickSteps * m->opt.timestep));
		mju_cross(K + 3, R, K);	// torque = R x force (same expression order as before: bit-identical)
		Emit(FString::Printf(TEXT("{\"type\": \"strike\", \"t\": %s, \"striker\": %s, \"part\": %s, \"palm\": %s, \"speed\": %s}"),
			*Num(T, 3), *Str(S.Name), *Str(Turn.Part), *Bool(Turn.bPalm), *Num(Turn.Speed, 2)));
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
	// fatigue and memory (match.py finish(): same order, same arithmetic)
	S.Stamina = FMath::Max(MinStamina, S.Stamina - S.StaminaPerStrike);
	if (Touched)
	{
		D.Mem[0] = RoundTo(Turn.TouchStep * m->opt.timestep, 3) / StrikeClock;
		D.Mem[1] = Turn.Speed / 10.0;
		if (!Turn.bKO) D.NeckStrength = FMath::Max(MinNeck, D.NeckStrength - D.NeckDamage * Turn.HeadPeak);
	}
	FString Cond;
	for (const FAthlete& A : Athletes) Cond += FString::Printf(TEXT(", %s: [%s, %s]"), *Str(A.Name), *Num(A.Stamina, 3), *Num(A.NeckStrength, 3));
	Emit(TEXT("{\"type\": \"condition\"") + Cond + TEXT("}"));
	const FString Call = Turn.bKO ? FString(TEXT("KNOCKOUT")) : Turn.Fouls.Num() ? TEXT("Foul: ") + Turn.Fouls.Last().Value : End;
	SetHud([&](FSlapHud& H)
	{
		H.HeadPeak = Turn.HeadPeak; H.Event = Call; H.Hic = Turn.Hic; H.Bric = Turn.Bric;
		for (int32 a = 0; a < 2; ++a) { H.Stamina[a] = Athletes[a].Stamina; H.Neck[a] = Athletes[a].NeckStrength; }
		if (Turn.bKO) ++H.KOSeq;
	});
	RoundTurns.Add(Turn);
	bInTurn = false;

	if (Turn.bKO)
	{
		Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": \"KO\", \"winner\": %s, \"round\": %d, \"cards\": %s}"), *Str(S.Name), Round, *CardsJson(Cards)));
		const FString Res = FString::Printf(TEXT("%s wins by KO, round %d"), *S.Name.ToUpper(), Round);
		SetHud([&](FSlapHud& H) { H.Result = Res; });
		Round = Rounds + 1;	// match over; the next turn starts a new one
		EndMatch(Turn.Striker);
		return;
	}
	if (++TurnInRound < 2) { HoldLeft = HoldSteps(TurnHold); return; }	// let the crowd see how the turn ended
	int32 Pts[3][2];
	Score(Pts);
	Emit(FString::Printf(TEXT("{\"type\": \"round\", \"round\": %d, \"cards\": %s}"), Round, *CardsJson(Cards)));
	SetHud([&](FSlapHud& H) { FMemory::Memcpy(H.Cards, Cards, sizeof(Cards)); FMemory::Memcpy(H.RoundCards, Pts, sizeof(Pts)); H.ScoredRound = Round; });
	RoundTurns.Reset();
	TurnInRound = 0;
	if (++Round <= Rounds) { HoldLeft = HoldSteps(TurnHold); return; }
	int32 Wins[2] = { 0, 0 };
	for (int32 j = 0; j < 3; ++j)
	{
		if (Cards[j][0] != Cards[j][1]) ++Wins[Cards[j][1] > Cards[j][0] ? 1 : 0];
	}
	const int32 Top = Wins[1] > Wins[0] ? 1 : 0;
	FString How = TEXT("draw");
	if (Wins[Top] == 3) How = TEXT("unanimous decision");
	else if (Wins[Top] == 2) How = Wins[1 - Top] == 1 ? TEXT("split decision") : TEXT("majority decision");
	const bool Draw = How == TEXT("draw");
	Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": %s, \"winner\": %s, \"round\": %d, \"cards\": %s}"), *Str(How), Draw ? TEXT("null") : *Str(Athletes[Top].Name), Rounds, *CardsJson(Cards)));
	const FString Res = Draw ? FString(TEXT("Draw")) : FString::Printf(TEXT("%s wins by %s"), *Athletes[Top].Name.ToUpper(), *How);
	SetHud([&](FSlapHud& H) { H.Result = Res; });
	EndMatch(Draw ? -1 : Top);
}

int64 USlapController::HoldSteps(double Seconds)
{
	return FMath::RoundToInt64(Seconds / ControlEvery / 0.005) * ControlEvery;	// whole brain steps (200 Hz physics)
}

void USlapController::EndMatch(int32 Winner)
{
	LastWinner = Winner;
	HoldLeft = HoldSteps(PostMatchHold);
	SetHud([&](FSlapHud& H) { Winner < 0 ? ++H.Draws : ++H.Wins[Winner]; });
}

void USlapController::Score(int32 (&Out)[3][2])
{
	for (int32 j = 0; j < 3; ++j)	// 10-point must, three judges weighing (speed, head, stagger, defence) differently
	{
		double Eff[2] = { 0.0, 0.0 };
		bool Stag[2] = { false, false };
		for (const FTurn& R : RoundTurns)
		{
			if (R.TouchStep < 0) continue;
			Eff[R.Striker] += Weights[j][0] * R.Speed + (Weights[j][1] * 5) * R.HeadPeak + (double)(Weights[j][2] * 10 * (R.bStagger ? 1 : 0));
			Stag[R.Striker] = R.bStagger;
			if (!R.bKO)	// the defender absorbed it: stayed put, small head snap
			{
				Eff[1 - R.Striker] += Weights[j][3] * ((double)(10 * (R.bStagger ? 0 : 1)) + FMath::Max(0.0, 10.0 - 5 * R.HeadPeak));
			}
		}
		const int32 Hi = Eff[1] > Eff[0] ? 1 : 0;
		int32 Pts[2] = { 10, 10 };
		if (FMath::Abs(Eff[0] - Eff[1]) > 0.05 * FMath::Max(Eff[0], Eff[1])) Pts[1 - Hi] = Stag[Hi] && Eff[Hi] >= 2 * Eff[1 - Hi] ? 8 : 9;
		for (const FTurn& R : RoundTurns)
		{
			for (const TPair<int32, FString>& F : R.Fouls) --Pts[F.Key];
		}
		Cards[j][0] += Pts[0];
		Cards[j][1] += Pts[1];
		Out[j][0] = Pts[0];
		Out[j][1] = Pts[1];
	}
}

// Eye contact + post-slap poise: pure maths, mirrors match.py gaze_desire/gaze_walk/gaze_apply/strike_hold.
// PRE-touch: the defender's neck goals walk to meet the striker's eyes (world-space bias converted to the
// chest frame's ZYX yaw/pitch - world space because the chest WHIPS during a follow-through); he regains
// the neck the instant the slap lands (he braces).
// POST-touch: the striker PLANTS - goals melt back to his balanced stand pose - and his eyes track the
// defender to see whether he goes down; this continues through the inter-turn hold (display-only).
void USlapController::ApplyGaze(const mjModel* m, mjData* d)
{
	if (!(bEyeContact && Athletes.Num() == 2)) return;
	// in-turn after the touch, OR through the inter-turn hold: the striker keeps planting and watching the
	// verdict (the hold is display-only - the next turn resets everybody - so event parity is untouched)
	const bool bHold = !bInTurn && HoldLeft > 0 && MatchNo > 0 && Turn.TouchStep >= 0;
	if (!(bInTurn || bHold)) return;
	const bool bPlanted = Turn.TouchStep >= 0;
	FAthlete& Watcher = Athletes[bPlanted ? Turn.Striker : 1 - Turn.Striker];	// defender pre-touch, striker after
	const FAthlete& Focus = Athletes[bPlanted ? 1 - Turn.Striker : Turn.Striker];
	if (Watcher.YawIdx < 0 || Watcher.PitchIdx < 0) return;
	// NB (2026-10-25 QA): an imposed post-slap POSE hold was tried and rejected - freezing any joints after
	// the touch fought the brain's own balance and tripled striker self-knockdowns. The striker's "stay
	// balanced" duty is served by leaving the brain in charge of the body; only the WATCH below remains.
	const double* Own = d->geom_xpos + 3 * Watcher.Head;
	const double* Op = d->geom_xpos + 3 * Focus.Head;
	// desire: unit world direction from the watcher's eyes to the focus's eyes
	double Des[3] = { Op[0] - Own[0], Op[1] - Own[1], Op[2] - Own[2] };
	const double DL = FMath::Sqrt(Des[0] * Des[0] + Des[1] * Des[1] + Des[2] * Des[2]);
	if (DL < 1e-9) return;
	Des[0] /= DL; Des[1] /= DL; Des[2] /= DL;
	// walk the WORLD-space bias toward the desire by at most Walk radians (great-circle, never a teleport)
	const double Walk = bPlanted ? WatchMaxStep : GazeMaxStep;	// snappier while watching a falling opponent
	double WB[3] = { Watcher.BiasWX, Watcher.BiasWY, Watcher.BiasWZ };
	const double CosT = FMath::Clamp(WB[0] * Des[0] + WB[1] * Des[1] + WB[2] * Des[2], -1.0, 1.0);
	const double Theta = FMath::Acos(CosT);
	if (Theta <= Walk)
	{
		WB[0] = Des[0]; WB[1] = Des[1]; WB[2] = Des[2];
	}
	else
	{
		const double Px = Des[0] - CosT * WB[0], Py = Des[1] - CosT * WB[1], Pz = Des[2] - CosT * WB[2];
		const double PN = FMath::Sqrt(Px * Px + Py * Py + Pz * Pz);
		if (PN < 1e-9)	// antipodal: snap (unreachable for two facing athletes)
		{
			WB[0] = Des[0]; WB[1] = Des[1]; WB[2] = Des[2];
		}
		else
		{
			const double Cs = FMath::Cos(Walk), Sn = FMath::Sin(Walk);
			double V[3] = { WB[0] * Cs + Px / PN * Sn, WB[1] * Cs + Py / PN * Sn, WB[2] * Cs + Pz / PN * Sn };
			const double VL = FMath::Sqrt(V[0] * V[0] + V[1] * V[1] + V[2] * V[2]);
			WB[0] = V[0] / VL; WB[1] = V[1] / VL; WB[2] = V[2] / VL;
		}
	}
	Watcher.BiasWX = WB[0]; Watcher.BiasWY = WB[1]; Watcher.BiasWZ = WB[2];
	// project the world bias into the chest frame (the neck hinges ARE its Z/Y/X) and clamp to the joint range
	const double* Xm = d->xmat + 9 * Watcher.Chest;
	mjtNum Rx[3];
	mju_mulMatTVec3(Rx, Xm, WB);	// R^T * bias
	const double Want[2] = { FMath::Atan2(Rx[1], Rx[0]),
							 -FMath::Asin(FMath::Clamp(Rx[2], -1.0, 1.0)) };
	// turn only (2026-10-29): the nod tilted Kim's face up toward a taller Grandpa, off the target his slap trained
	// on (training has no gaze servo); the nod stays the brain's (match.py gaze_apply)
	const int32 Idx[1] = { Watcher.YawIdx };
	for (int32 s = 0; s < 1; ++s)
	{
		const int32 j = Idx[s];
		const double Clamped = FMath::Clamp(Want[s], Watcher.Lo[j], Watcher.Hi[j]);
		const double Blend = (1.0 - GazeGain) * Watcher.Goal[j] + GazeGain * Clamped;
		Watcher.Goal[j] = FMath::Clamp(Blend, Watcher.Lo[j], Watcher.Hi[j]);
	}
}

void USlapController::Decide(const mjModel* m, mjData* d)
{
	mj_kinematics(m, d);	// positions for the current qpos (mj_step leaves xpos one step behind)
	const bool Match = bInTurn && Athletes.Num() == 2;
	const double T = Turn.Step * m->opt.timestep, Clock = 1.0 - T / StrikeClock;
	float Obs[NumObs];
	const bool bX = bXRay.load(std::memory_order_relaxed) && Athletes.Num() == 2;
	const bool bXTick = bX && XTick++ % 10 == 0;	// what they react to: 5 Hz (10 more brain runs each)
	float Focus[2][10] = {};
	for (int32 a = 0; a < Athletes.Num(); ++a)
	{
		FAthlete& A = Athletes[a];
		const float Role = !Match ? 0.f : a == Turn.Striker ? 1.f : (float)DefenderRole;
		BuildObs(m, d, A, Role, Clock, Match ? &Athletes[1 - a] : nullptr, Obs);
		if (!A.Net) continue;
		float Value = 0.f;	// trained brains also output the critic's value (the HUD's momentum bar)
		if (!Infer(A, Obs, Value)) continue;
		if (!RecordFile.IsEmpty() && StepCount == 0)	// live check diagnostics: first decision at full precision
		{
			FString S;
			for (int32 k = 0; k < A.NumIn; ++k) S += FString::Printf(TEXT("%.9g "), Obs[k]);
			UE_LOG(LogSlap, Log, TEXT("first obs %s: %s"), *A.Prefix, *S);
		}
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			const double Step = A.MaxStep[j] * A.Stamina;	// fatigue: a tired athlete moves slower
			const double Act = FMath::Clamp((double)Action[j], -1.0, 1.0);
			A.Goal[j] = FMath::Clamp(Target(A.Stand[j], A.Lo[j], A.Hi[j], Act), A.Goal[j] - Step, A.Goal[j] + Step);
			A.PrevAction[j] = (float)Act;
		}
		A.Value = A.NumOut > 1 ? Value : 0.f;
		A.Role = (int32)Role;
		if (bXTick && a < 2) XRay(A, Obs, Focus[a]);
	}
	ApplyGaze(m, d);	// eye contact: right after the decisions, as match.py does
	if (Match)	// broadcast telemetry at 50 Hz: striker's palm, defender's head, both athletes' balance
	{
		const FAthlete& S = Athletes[Turn.Striker];
		const FAthlete& D = Athletes[1 - Turn.Striker];
		const double HandDist = Dist(d->geom_xpos + 3 * S.Hand, d->geom_xpos + 3 * D.Head, 3);
		const double Closing = PrevHandDist >= 0.0 ? (PrevHandDist - HandDist) / (m->opt.timestep * ControlEvery) : 0.0;
		PrevHandDist = HandDist;
		Ttc = Turn.TouchStep < 0 && Closing > 0.5 ? FMath::Max(HandDist - 0.12, 0.0) / Closing : 99.0;	// palm to cheek, roughly
		const double HeadG = TelHeadG;
		TelHeadG = 0.0;
		TArray<FVector> Ghost[2];
		if (bX && GhostD)	// the brain X-ray's ghost: where each brain is steering the body (its joint targets, through MuJoCo's kinematics)
		{
			mju_copy(GhostD->qpos, d->qpos, m->nq);
			for (const FAthlete& A : Athletes)
			{
				for (int32 j = 0; j < A.Act.Num(); ++j) GhostD->qpos[A.Qadr[j]] = A.Goal[j];
			}
			mj_kinematics(m, GhostD);
			for (int32 a = 0; a < 2; ++a)
			{
				for (const int32 B : Athletes[a].Bodies) Ghost[a].Add(MjUtils::MjToUEPosition(GhostD->xpos + 3 * B));
			}
		}
		SetHud([&](FSlapHud& H)
		{
			H.HandDist = HandDist;
			H.HeadG = HeadG;
			H.Hic = Turn.Hic;
			H.Bric = Turn.Bric;
			H.ChainOrder = Turn.ChainOrder;
			FMemory::Memcpy(H.Chain, Turn.Chain, sizeof(H.Chain));
			if (bXTick) FMemory::Memcpy(H.Focus, Focus, sizeof(Focus));
			H.Ghost[0] = MoveTemp(Ghost[0]);
			H.Ghost[1] = MoveTemp(Ghost[1]);
			HudBalance(m, d, H);
			for (int32 a = 0; a < 2; ++a)
			{
				const FAthlete& X = Athletes[a];
				H.Stamina[a] = X.Stamina;
				H.Neck[a] = X.NeckStrength;
				H.Value[a] = X.Value;
				H.Role[a] = X.Role;
			}
		});
	}
	if (Match && Turn.Step % 20 == 0)	// gameplay log: both athletes' pelvis, head, actions at 10 Hz
	{
		TStringBuilder<2048> Line;	// on the stack: this runs on the physics thread
		Line.Appendf(TEXT("{\"type\": \"frame\", \"t\": %.3f"), T);
		for (const FAthlete& A : Athletes)
		{
			const double* P = d->xpos + 3 * A.Pelvis;
			const double* H = d->geom_xpos + 3 * A.Head;
			Line.Appendf(TEXT(", \"%s\": {\"pelvis\": [%.3f, %.3f, %.3f], \"head\": [%.3f, %.3f, %.3f], \"action\": ["), *A.Name, P[0], P[1], P[2], H[0], H[1], H[2]);
			for (int32 j = 0; j < A.PrevAction.Num(); ++j) Line.Appendf(TEXT("%s%.2f"), j ? TEXT(", ") : TEXT(""), (double)A.PrevAction[j]);
			Line << TEXT("]}");
		}
		Line << TEXT('}');
		Emit(Line);
		const double Left = Turn.TouchStep < 0 ? StrikeClock - T : Recovery - (Turn.Step - Turn.TouchStep) * m->opt.timestep;
		SetHud([&](FSlapHud& Hd) { Hd.Clock = Left; });
	}
}

void USlapController::Telemetry(const mjModel* m, const mjData* d, double HandSpeed)
{
	// every brain step (20 ms) of a turn: the defender's head acceleration, the striker's palm speed. Over 5 ms steps a
	// hard contact differentiates into thousands of g; the 20 ms average is what a mouthguard sensor would report.
	if (Turn.Step % ControlEvery) return;
	const double* Head = d->geom_xpos + 3 * Athletes[1 - Turn.Striker].Head;
	const double Dt = m->opt.timestep * ControlEvery;
	const double Vel[3] = { (Head[0] - TelHeadPrev[0]) / Dt, (Head[1] - TelHeadPrev[1]) / Dt, (Head[2] - TelHeadPrev[2]) / Dt };
	if (bTelReady) TelHeadG = Dist(Vel, TelHeadVel, 3) / Dt / 9.81;
	bTelReady = true;
	FMemory::Memcpy(TelHeadPrev, Head, sizeof(TelHeadPrev));
	FMemory::Memcpy(TelHeadVel, Vel, sizeof(TelHeadVel));
	SetHud([&](FSlapHud& H) { H.HandSpeed = HandSpeed; });
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
		if (WalkInLeft > 0)
		{
			StepWalkIn(m, d);
			if (bHudReady.load(std::memory_order_relaxed)) --WalkInLeft;
		}
		else
		{
			if (bInTurn) PostStep(m, d);
			for (;;)	// a turn that ends here starts the next at once (match.py's loop), checks included
			{
				if (!bInTurn)
				{
					if (MatchNo > 0 && HoldLeft > 0) break;	// a turn or match just ended: let the crowd see it (StartTurn resets after)
					if (MatchNo == 0 || Round > Rounds) { StartMatch(m, d); if (WalkInLeft > 0) break; }
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

void USlapController::Impact(const mjModel* m, const mjData* d)
{
	// The defender's head as a crash-test dummy's (display and the "impact" log line only). HIC15: the worst window of up
	// to 15 ms of (t2 - t1) * mean(a)^2.5, a in g (700 = the US crash-test limit, FMVSS 208). BrIC: his head's peak spin
	// about his own axes against NHTSA's critical rates (66.25, 56.45, 42.87 rad/s); 1.0 = critical.
	const FAthlete& D = Athletes[1 - Turn.Striker];
	mjtNum V[6];
	mj_objectVelocity(m, d, mjOBJ_BODY, D.HeadBody, V, 0);	// angular then linear, world axes, at the head's centre of mass
	const double Dt = m->opt.timestep;
	if (Turn.HeadSamples >= 0 && Turn.TouchStep >= 0)
	{
		Turn.HeadAcc[2] = Turn.HeadAcc[1];
		Turn.HeadAcc[1] = Turn.HeadAcc[0];
		Turn.HeadAcc[0] = Dist(V + 3, Turn.HeadVel, 3) / Dt / 9.81;
		Turn.HeadSamples = FMath::Min(Turn.HeadSamples + 1, 3);
		double Sum = 0.0;
		for (int32 n = 1; n <= Turn.HeadSamples; ++n)	// windows of 5, 10, 15 ms ending now
		{
			Sum += Turn.HeadAcc[n - 1];
			Turn.Hic = FMath::Max(Turn.Hic, n * Dt * FMath::Pow(Sum / n, 2.5));
		}
		mjtNum R[9];
		mju_quat2Mat(R, d->qpos + D.RootQ + 3);
		const double Yaw = std::atan2(R[3], R[0]), C = std::cos(Yaw), S = std::sin(Yaw);
		const double W[3] = { C * V[0] + S * V[1], -S * V[0] + C * V[1], V[2] };	// his heading frame: x forward, y left, z up
		constexpr double Crit[3] = { 66.25, 56.45, 42.87 };
		double B = 0.0;
		for (int32 k = 0; k < 3; ++k)
		{
			Turn.Spin[k] = FMath::Max(Turn.Spin[k], FMath::Abs(W[k]));
			B += FMath::Square(Turn.Spin[k] / Crit[k]);
		}
		Turn.Bric = FMath::Sqrt(B);
	}
	FMemory::Memcpy(Turn.HeadVel, V + 3, sizeof(Turn.HeadVel));
	Turn.HeadSamples = FMath::Max(Turn.HeadSamples, 0);
}

void USlapController::ChainSample(const mjData* d, double PalmSpeed)
{
	const FAthlete& S = Athletes[Turn.Striker];
	auto Joint = [&](int32 j) { return j >= 0 ? (float)FMath::Abs(d->qvel[S.Vadr[j]]) : 0.f; };
	float* Row = ChainBuf[ChainAt];
	Row[0] = (float)FMath::Abs(d->qvel[S.RootV + 5]);	// pelvis yaw rate (the root's angular velocity, its own z)
	Row[1] = Joint(S.WaistTwist);
	Row[2] = Joint(S.ShSwingR);
	Row[3] = Joint(S.ElbowR);
	Row[4] = (float)PalmSpeed;
	ChainAt = (ChainAt + 1) % UE_ARRAY_COUNT(ChainBuf);
	ChainNum = FMath::Min(ChainNum + 1, (int32)UE_ARRAY_COUNT(ChainBuf));
}

void USlapController::ChainAtTouch(double Dt)
{
	// kinetic chain: when each link peaked over the 0.6 s before the touch. A whip runs proximal to distal (hips, trunk,
	// shoulder, elbow, palm), each link peaking a little after the one before it
	const int32 Len = UE_ARRAY_COUNT(ChainBuf);
	for (int32 k = 0; k < 5; ++k)
	{
		int32 Best = 0;
		float Peak = -1.f;
		for (int32 i = 0; i < ChainNum; ++i)	// i steps back from the newest sample
		{
			const float V = ChainBuf[(ChainAt - 1 - i + Len) % Len][k];
			if (V > Peak) { Peak = V; Best = i; }
		}
		Turn.Chain[k] = -Best * Dt;
	}
	Turn.ChainOrder = 0;
	for (int32 k = 0; k < 4; ++k) Turn.ChainOrder += Turn.Chain[k] <= Turn.Chain[k + 1];
}

void USlapController::XRay(FAthlete& A, const float* Obs, float (&Out)[10])
{
	// Brain X-ray: what this decision reacted to. Each input group in turn is put back the way it was at the last X-ray
	// tick (0.2 s ago) and the brain is run again; how far its actions move is that group's share. Display only.
	const int32 N = A.Act.Num();
	struct FRange { int32 From = 0, To = 0; };
	const FRange Groups[10][2] = {
		{ { 18, 30 }, { N + 18, N + 30 } },	// own legs: angles, speeds
		{ { 6, 18 }, { N + 6, N + 18 } },	// own arms
		{ { 0, 6 }, { N, N + 6 } },			// trunk and neck
		{ { 2 * N, 2 * N + 10 }, {} },		// balance: pelvis height, tilt, speed, spin
		{ { 2 * N + 10, 3 * N + 10 }, {} },	// rhythm: its own last move
		{ { 3 * N + 12, 3 * N + 15 }, {} },	// the opponent's head (or its own foot box)
		{ { 3 * N + 15, 3 * N + 18 }, {} },	// the opponent's hand
		{ { 3 * N + 18, 3 * N + 38 }, {} },	// memory: 0.1-0.3 s ago, the last strike
		{ { 3 * N + 38, 3 * N + 40 }, {} },	// fatigue
		{ { 3 * N + 40, 3 * N + 46 }, {} } };	// own palm
	if (A.XPrev.Num() == A.NumIn && N == 30)
	{
		float Base[30], Probe[NumObs];
		FMemory::Memcpy(Base, Action.GetData(), N * sizeof(float));
		double Total = 0.0;
		for (int32 g = 0; g < 10; ++g)
		{
			FMemory::Memcpy(Probe, Obs, A.NumIn * sizeof(float));
			for (const FRange& R : Groups[g])
			{
				for (int32 k = R.From; k < FMath::Min(R.To, A.NumIn); ++k) Probe[k] = A.XPrev[k];
			}
			float Value = 0.f;
			double Moved = 0.0;
			if (Infer(A, Probe, Value))
			{
				for (int32 j = 0; j < N; ++j) Moved += FMath::Square(FMath::Clamp(Action[j], -1.f, 1.f) - FMath::Clamp(Base[j], -1.f, 1.f));
			}
			Out[g] = (float)FMath::Sqrt(Moved);
			Total += Out[g];
		}
		for (float& F : Out) F = Total > 1e-4 ? (float)(F / Total) : 0.f;
		FMemory::Memcpy(Action.GetData(), Base, N * sizeof(float));
	}
	A.XPrev = TArray<float>(Obs, A.NumIn);
}


void USlapController::BuildObs(const mjModel* m, const mjData* d, FAthlete& A, float Role, double Clock, const FAthlete* Opp, float* Obs, const float* Cmd) const
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
	O[10 + N] = Role;
	O[11 + N] = Role != 0.f ? (float)Clock : 0.f;	// turn clock remaining
	for (int32 k = 15 + N; k < 18 + N; ++k) O[k] = 0.f;	// opponent hand: the defender's input only
	if (Role == 0.f)	// own foot-box centre (start pelvis x, y = qpos0) - pelvis, heading frame
	{
		const double Box[3] = { m->qpos0[A.RootQ] - d->qpos[A.RootQ], m->qpos0[A.RootQ + 1] - d->qpos[A.RootQ + 1], 0.0 };
		Heading(Box, O + 12 + N);
	}
	else
	{
		const double* P = d->geom_xpos + 3 * Opp->Head;	// the opponent's head centre - pelvis
		const double Rel[3] = { P[0] - Pelvis[0], P[1] - Pelvis[1], P[2] - Pelvis[2] };
		Heading(Rel, O + 12 + N);
		if (Role < 0.f)	// defender: the striker's right hand as it was React decisions ago - pelvis
		{
			const double* H = d->geom_xpos + 3 * Opp->Hand;
			if (A.bFresh)
				for (double* Row : A.Seen) FMemory::Memcpy(Row, H, 3 * sizeof(double));
			else
			{
				FMemory::Memmove(A.Seen[0], A.Seen[1], React * 3 * sizeof(double));
				FMemory::Memcpy(A.Seen[React], H, 3 * sizeof(double));
			}
			const double Rh[3] = { A.Seen[0][0] - Pelvis[0], A.Seen[0][1] - Pelvis[1], A.Seen[0][2] - Pelvis[2] };
			Heading(Rh, O + 15 + N);
		}
	}
	if (Cmd) FMemory::Memcpy(O + 12 + N, Cmd, 6 * sizeof(float));	// the walking brain's command in place of the target

	// memory (108-127) and fatigue (128-129)
	const float* Cur = O + 12 + N;	// inputs 102-107
	if (A.bFresh)
	{
		for (float* Row : A.Hist) FMemory::Memcpy(Row, Cur, 6 * sizeof(float));
	}
	else
	{
		FMemory::Memmove(A.Hist[0], A.Hist[1], 15 * 6 * sizeof(float));
		FMemory::Memcpy(A.Hist[15], Cur, 6 * sizeof(float));
	}
	A.bFresh = false;
	for (int32 i = 0; i < 3; ++i) FMemory::Memcpy(O + 18 + N + 6 * i, A.Hist[15 - Lags[i]], 6 * sizeof(float));
	O[36 + N] = (float)A.Mem[0];
	O[37 + N] = (float)A.Mem[1];
	O[38 + N] = (float)A.Stamina;
	O[39 + N] = (float)A.NeckStrength;

	// own palm (130-135): centre - pelvis, facing (the hand box's -z), heading frame
	const double* H = d->geom_xpos + 3 * A.Hand;
	const double Rp[3] = { H[0] - Pelvis[0], H[1] - Pelvis[1], H[2] - Pelvis[2] };
	const double* X = d->geom_xmat + 9 * A.Hand;
	const double Palm[3] = { -X[2], -X[5], -X[8] };
	Heading(Rp, O + 40 + N);
	Heading(Palm, O + 43 + N);

	// extended proprioceptive foot-ground & phase awareness (136-147): sole_l/r rel pos, contact flags, load, gait phase clock
	if (A.NumIn >= 148 && A.Feet.Num() >= 2)
	{
		for (int32 f = 0; f < 2; ++f)
		{
			const double* Fp = d->geom_xpos + 3 * A.Feet[f];
			const double Rf[3] = { Fp[0] - Pelvis[0], Fp[1] - Pelvis[1], Fp[2] - Pelvis[2] };
			Heading(Rf, O + 46 + N + 3 * f);
			O[52 + N + f] = Fp[2] < 0.045 ? 1.f : 0.f;
			O[54 + N + f] = (float)FMath::Clamp((0.045 - Fp[2]) / 0.025, 0.0, 1.0);
		}
		const double GaitAngle = 2.0 * PI * 1.8 * d->time;
		O[56 + N] = (float)std::sin(GaitAngle);
		O[57 + N] = (float)std::cos(GaitAngle);
	}
}

void USlapController::SetForce(mjModel* m, const FAthlete& A, double Gain) const
{
	for (int32 j = 0; j < A.Act.Num(); ++j)
	{
		m->actuator_forcerange[2 * A.Act[j]] = A.FrAll[2 * j] * Gain;
		m->actuator_forcerange[2 * A.Act[j] + 1] = A.FrAll[2 * j + 1] * Gain;
	}
	if (Gain != 1.0) return;	// out cold: the neck as slack as the rest
	for (int32 k = 0; k < A.NeckAct.Num(); ++k)
	{
		m->actuator_forcerange[2 * A.NeckAct[k]] = A.Fr0[2 * k] * A.NeckStrength;
		m->actuator_forcerange[2 * A.NeckAct[k] + 1] = A.Fr0[2 * k + 1] * A.NeckStrength;
	}
}

void USlapController::Faint(mjModel* m)
{
	// Fainting (card faint_chance_per_turn; user 2026-10-26: Matt "passes out randomly on rare occasion"): every motor at
	// faint_strength of its force for faint_seconds, then back; the referee calls whatever follows, as in match.py
	if (!bInTurn) return;
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		const FAthlete& A = Athletes[a];
		if (Turn.Step == Turn.FaintAt[a])
		{
			SetForce(m, A, A.FaintStrength);
			Emit(FString::Printf(TEXT("{\"type\": \"faint\", \"t\": %s, \"athlete\": %s}"), *Num(Turn.Step * m->opt.timestep, 3), *Str(A.Name)));
			UE_LOG(LogSlap, Log, TEXT("t=%.3f %s passes out"), Turn.Step * m->opt.timestep, *A.Name);
			const FString Call = A.Name.ToUpper() + TEXT(" PASSES OUT!");
			SetHud([&](FSlapHud& H) { H.Event = Call; });
		}
		else if (Turn.Step == Turn.FaintEnd[a]) SetForce(m, A, 1.0);
	}
}
