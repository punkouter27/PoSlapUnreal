#include "SlapController.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/DateTime.h"
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
		A.Name = A.Prefix.LeftChop(1);
		A.Name = A.Name.Mid(A.Name.Find(TEXT("_"), ESearchCase::CaseSensitive, ESearchDir::FromEnd) + 1);	// "Arena_red" -> "red"
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
		auto Geom = [&](const TCHAR* N) { return mj_name2id(m, mjOBJ_GEOM, TCHAR_TO_UTF8(*(A.Prefix + N))); };
		A.Feet = { Geom(TEXT("foot_l")), Geom(TEXT("foot_r")) };	// the foot box is the sole (Blueprint 2.1)
		A.Head = Geom(TEXT("head"));
		A.Hand = Geom(TEXT("hand_r"));
	}
	Floor = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("floor"));
	Podium = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("podium"));
	bInTurn = false;
	MatchNo = 0;

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
	UE_LOG(LogSlap, Log, TEXT("Policy: %d athletes, %d joints each, brain %s, defender role %d"),
		Athletes.Num(), Order.Num(), Instance ? TEXT("loaded") : TEXT("MISSING (holding stand pose)"), DefenderRole);
}

void USlapController::BeginDestroy()
{
	delete Log;
	Log = nullptr;
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
	FString Str(const FString& S) { return TEXT("\"") + S + TEXT("\""); }
	FString Bool(bool B) { return B ? TEXT("true") : TEXT("false"); }

	constexpr int32 Rounds = 3;
	constexpr double StrikeClock = 30.0, Recovery = 5.0, BoxX = 0.20, BoxY = 0.25;
	constexpr int32 Weights[3][3] = { { 2, 1, 1 }, { 1, 2, 1 }, { 1, 1, 2 } };	// judges: speed, head, stagger
}

void USlapController::SetHud(TFunctionRef<void(FSlapHud&)> Edit)
{
	FScopeLock L(&HudLock);
	Edit(Hud);
}

void USlapController::Emit(const FString& Json)
{
	if (!Log) return;
	FTCHARToUTF8 U(*(Json + TEXT("\n")));
	Log->Serialize((void*)U.Get(), U.Length());
	Log->Flush();
}

FString USlapController::Down(const mjModel* m, const mjData* d, const FAthlete& A) const
{
	for (int32 c = 0; c < d->ncon; ++c)
	{
		const mjContact& C = d->contact[c];
		const bool S0 = C.geom[0] == Floor || C.geom[0] == Podium, S1 = C.geom[1] == Floor || C.geom[1] == Podium;
		if (S0 == S1) continue;
		const int32 G = S0 ? C.geom[1] : C.geom[0];
		if (m->body_rootid[m->geom_bodyid[G]] == A.Pelvis && !A.Feet.Contains(G)) return Slap::Name(m, mjOBJ_GEOM, G);
	}
	return FString();
}

void USlapController::StartMatch(const mjModel* m, mjData* d)
{
	++MatchNo;
	int32 Forced = -1;	// -SlapFirst=0|1: the first match's coin toss (match.py compare)
	const bool Force = MatchNo == 1 && FParse::Value(FCommandLine::Get(), TEXT("SlapFirst="), Forced) && (Forced == 0 || Forced == 1);
	First = Force ? Forced : FMath::RandRange(0, 1);
	Round = 1;
	TurnInRound = 0;
	FMemory::Memzero(Cards);
	RoundTurns.Reset();
	delete Log;
	const FString Dir = FPaths::Combine(FPaths::ProjectDir(), TEXT("Logs/Gameplay"), FDateTime::Now().ToString(TEXT("%y%m%d")));
	IFileManager::Get().MakeDirectory(*Dir, true);
	Log = IFileManager::Get().CreateFileWriter(*FPaths::Combine(Dir, FString::Printf(TEXT("match_%s_%d_ue.jsonl"), *FDateTime::Now().ToString(TEXT("%H%M%S")), First)));
	Emit(FString::Printf(TEXT("{\"type\": \"match_start\", \"first\": %s, \"rules\": {\"rounds\": %d, \"strike_clock\": 30.0, \"recovery\": 5.0}}"), *Str(Athletes[First].Name), Rounds));
	UE_LOG(LogSlap, Log, TEXT("match %d: coin toss -> %s strikes first"), MatchNo, *Athletes[First].Name);
	const FString Toss = Athletes[First].Name + TEXT(" wins the toss");
	SetHud([&](FSlapHud& H) { H.Match = MatchNo; FMemory::Memzero(H.Cards); H.Event = Toss; H.Speed = H.HeadPeak = 0.0; H.Part.Reset(); });
	StartTurn(m, d);
}

void USlapController::StartTurn(const mjModel* m, mjData* d)
{
	Turn = FTurn();
	Turn.Round = Round;
	Turn.Striker = TurnInRound == 0 ? First : 1 - First;
	mj_resetData(m, d);	// as brain_cpu.stand(): reset, stand pose, forward (then match.py forwards once more)
	for (FAthlete& A : Athletes)
	{
		for (int32 j = 0; j < A.Act.Num(); ++j) d->qpos[A.Qadr[j]] = A.Stand[j];
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, A.Act.Num());
	}
	mj_forward(m, d);
	mj_forward(m, d);
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
	SetHud([&](FSlapHud& H) { H.Round = Round; H.Striker = S.Name; H.Defender = D.Name; H.Clock = StrikeClock; H.bStruck = false; });
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
			const FString Geom = Down(m, d, *X);
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
		Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": \"blow-up\", \"winner\": null, \"round\": %d, \"cards\": [[%d, %d], [%d, %d], [%d, %d]]}"),
			Round, Cards[0][0], Cards[0][1], Cards[1][0], Cards[1][1], Cards[2][0], Cards[2][1]));
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
	FMemory::Memcpy(Turn.HandPrev, Hand, sizeof(Turn.HandPrev));
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
		Turn.Com0[0] = d->subtree_com[3 * D.Pelvis];
		Turn.Com0[1] = d->subtree_com[3 * D.Pelvis + 1];
		Turn.Part = Part(Slap::Name(m, mjOBJ_GEOM, Other));
		Turn.Speed = RoundTo(Speed, 2);
		Emit(FString::Printf(TEXT("{\"type\": \"strike\", \"t\": %s, \"striker\": %s, \"part\": %s, \"palm\": %s, \"speed\": %s}"),
			*Num(T, 3), *Str(S.Name), *Str(Turn.Part), *Bool(Turn.bPalm), *Num(Turn.Speed, 2)));
		UE_LOG(LogSlap, Log, TEXT("t=%.3f %s strikes with the %s at %.2f m/s%s"), T, *S.Name, *Turn.Part, Turn.Speed, Turn.bPalm ? TEXT("") : TEXT(" (not the palm: foul)"));
		if (!Turn.bPalm) Turn.Fouls.Emplace(Turn.Striker, TEXT("clubbing (not the palm)"));
		SetHud([&](FSlapHud& H) { H.bStruck = true; H.Speed = Turn.Speed; H.Part = Turn.Part; H.HeadPeak = 0.0; H.Event = Turn.bPalm ? TEXT("Clean palm") : TEXT("Foul: not the palm"); });
		break;
	}
}

void USlapController::EndTurn(const mjModel* m, mjData* d, const FString& End)
{
	Turn.End = End;
	const FAthlete& S = Athletes[Turn.Striker];
	const FAthlete& D = Athletes[1 - Turn.Striker];
	const bool Touched = Turn.TouchStep >= 0;
	if (Touched) Turn.bStagger = Dist(d->subtree_com + 3 * D.Pelvis, Turn.Com0, 2) > 0.05;
	Turn.HeadPeak = RoundTo(Turn.HeadPeak, 2);
	FString Fouls;
	for (const TPair<int32, FString>& F : Turn.Fouls) Fouls += (Fouls.IsEmpty() ? TEXT("") : TEXT(", ")) + Str(Athletes[F.Key].Name + TEXT(": ") + F.Value);
	Emit(FString::Printf(TEXT("{\"type\": \"turn_end\", \"round\": %d, \"striker\": %s, \"touch\": %s, \"part\": %s, \"palm\": %s, \"speed\": %s, \"head_peak\": %s, \"stagger\": %s, \"ko\": %s, \"end\": %s, \"fouls\": [%s]}"),
		Turn.Round, *Str(S.Name), Touched ? *Num(Turn.TouchStep * m->opt.timestep, 3) : TEXT("null"), Touched ? *Str(Turn.Part) : TEXT("null"),
		*Bool(Turn.bPalm), *Num(Turn.Speed, 2), *Num(Turn.HeadPeak, 2), *Bool(Turn.bStagger), *Bool(Turn.bKO), *Str(End), *Fouls));
	UE_LOG(LogSlap, Log, TEXT("round %d, %s's turn over: %s"), Turn.Round, *S.Name, *End);
	const FString Call = Turn.bKO ? FString(TEXT("KNOCKOUT")) : Turn.Fouls.Num() ? TEXT("Foul: ") + Turn.Fouls.Last().Value : End;
	SetHud([&](FSlapHud& H) { H.HeadPeak = Turn.HeadPeak; H.Event = Call; });
	RoundTurns.Add(Turn);
	bInTurn = false;

	auto CardsJson = [&] { return FString::Printf(TEXT("[[%d, %d], [%d, %d], [%d, %d]]"), Cards[0][0], Cards[0][1], Cards[1][0], Cards[1][1], Cards[2][0], Cards[2][1]); };
	if (Turn.bKO)
	{
		Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": \"KO\", \"winner\": %s, \"round\": %d, \"cards\": %s}"), *Str(S.Name), Round, *CardsJson()));
		const FString Res = FString::Printf(TEXT("%s wins by KO, round %d"), *S.Name.ToUpper(), Round);
		SetHud([&](FSlapHud& H) { H.Result = Res; });
		Round = Rounds + 1;	// match over; the next turn starts a new one
		return;
	}
	if (++TurnInRound < 2) return;
	Score();
	Emit(FString::Printf(TEXT("{\"type\": \"round\", \"round\": %d, \"cards\": %s}"), Round, *CardsJson()));
	SetHud([&](FSlapHud& H) { FMemory::Memcpy(H.Cards, Cards, sizeof(Cards)); });
	RoundTurns.Reset();
	TurnInRound = 0;
	if (++Round <= Rounds) return;
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
	Emit(FString::Printf(TEXT("{\"type\": \"result\", \"how\": %s, \"winner\": %s, \"round\": %d, \"cards\": %s}"), *Str(How), Draw ? TEXT("null") : *Str(Athletes[Top].Name), Rounds, *CardsJson()));
	const FString Res = Draw ? FString(TEXT("Draw")) : FString::Printf(TEXT("%s wins by %s"), *Athletes[Top].Name.ToUpper(), *How);
	SetHud([&](FSlapHud& H) { H.Result = Res; });
}

void USlapController::Score()
{
	for (int32 j = 0; j < 3; ++j)	// 10-point must, three judges weighing (speed, head, stagger) differently
	{
		double Eff[2] = { 0.0, 0.0 };
		bool Stag[2] = { false, false };
		for (const FTurn& R : RoundTurns)
		{
			if (R.TouchStep < 0 || !R.bPalm) continue;
			Eff[R.Striker] = Weights[j][0] * R.Speed + (Weights[j][1] * 5) * R.HeadPeak + (double)(Weights[j][2] * 10 * (R.bStagger ? 1 : 0));
			Stag[R.Striker] = R.bStagger;
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
	}
}

void USlapController::Decide(const mjModel* m, mjData* d)
{
	mj_kinematics(m, d);	// positions for the current qpos (mj_step leaves xpos one step behind)
	const bool Match = bInTurn && Athletes.Num() == 2;
	const double T = Turn.Step * m->opt.timestep, Clock = 1.0 - T / StrikeClock;
	float Obs[NumObs];
	for (int32 a = 0; a < Athletes.Num(); ++a)
	{
		FAthlete& A = Athletes[a];
		const float Role = !Match ? 0.f : a == Turn.Striker ? 1.f : (float)DefenderRole;
		BuildObs(m, d, A, Role, Clock, Match ? &Athletes[1 - a] : nullptr, Obs);
		float Value = 0.f;	// trained brains also output the critic's value (later: the HUD's win bar)
		const UE::NNE::FTensorBindingCPU In{ Obs, sizeof(Obs) };
		const UE::NNE::FTensorBindingCPU Out[] = { { Action.GetData(), (uint64)Action.Num() * sizeof(float) }, { &Value, sizeof(float) } };
		const int32 NumOut = Instance->GetOutputTensorDescs().Num() > 1 ? 2 : 1;
		if (Instance->RunSync({ In }, TConstArrayView<UE::NNE::FTensorBindingCPU>(Out, NumOut)) != UE::NNE::EResultStatus::Ok) continue;
		if (!RecordFile.IsEmpty() && StepCount == 0)	// live check diagnostics: first decision at full precision
		{
			FString S;
			for (int32 k = 0; k < NumObs; ++k) S += FString::Printf(TEXT("%.9g "), Obs[k]);
			UE_LOG(LogSlap, Log, TEXT("first obs %s: %s"), *A.Prefix, *S);
		}
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			const double Act = FMath::Clamp((double)Action[j], -1.0, 1.0);
			const double Tgt = FMath::Clamp(A.Stand[j] + Act * 0.5 * (A.Hi[j] - A.Lo[j]), A.Lo[j], A.Hi[j]);
			A.Goal[j] = FMath::Clamp(Tgt, A.Goal[j] - MaxStep, A.Goal[j] + MaxStep);
			A.PrevAction[j] = (float)Act;
		}
	}
	if (Match && Turn.Step % 20 == 0)	// gameplay log: both athletes' pelvis, head, actions at 10 Hz
	{
		FString Line = FString::Printf(TEXT("{\"type\": \"frame\", \"t\": %s"), *Num(T, 3));
		for (const FAthlete& A : Athletes)
		{
			const double* P = d->xpos + 3 * A.Pelvis;
			const double* H = d->geom_xpos + 3 * A.Head;
			FString Acts;
			for (float V : A.PrevAction) Acts += (Acts.IsEmpty() ? TEXT("") : TEXT(", ")) + Num(V, 2);
			Line += FString::Printf(TEXT(", %s: {\"pelvis\": [%s, %s, %s], \"head\": [%s, %s, %s], \"action\": [%s]}"), *Str(A.Name),
				*Num(P[0], 3), *Num(P[1], 3), *Num(P[2], 3), *Num(H[0], 3), *Num(H[1], 3), *Num(H[2], 3), *Acts);
		}
		Emit(Line + TEXT("}"));
		const double Left = Turn.TouchStep < 0 ? StrikeClock - T : Recovery - (Turn.Step - Turn.TouchStep) * m->opt.timestep;
		SetHud([&](FSlapHud& Hd) { Hd.Clock = Left; });
	}
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
		if (Instance && StepCount % ControlEvery == 0) Decide(m, d);
	}
	else if (Athletes.Num() == 2)	// the match (training/match.py)
	{
		if (bInTurn) PostStep(m, d);
		if (!bInTurn)
		{
			if (MatchNo == 0 || Round > Rounds) StartMatch(m, d);
			else StartTurn(m, d);
		}
		while (!PreStep(m, d))	// a turn that ends here starts the next at once (match.py's loop), checks included
		{
			if (Round > Rounds) StartMatch(m, d);
			else StartTurn(m, d);
		}
		if (Instance && Turn.Step % ControlEvery == 0) Decide(m, d);
	}
	else if (Instance && StepCount % ControlEvery == 0) Decide(m, d);
	for (const FAthlete& A : Athletes)
	{
		for (int32 j = 0; j < A.Act.Num(); ++j) d->ctrl[A.Act[j]] = A.Goal[j];
	}
}

void USlapController::BuildObs(const mjModel* m, const mjData* d, const FAthlete& A, float Role, double Clock, const FAthlete* Opp, float* Obs) const
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
		return;
	}
	const double* P = d->geom_xpos + 3 * Opp->Head;	// the opponent's head centre - pelvis
	const double Rel[3] = { P[0] - Pelvis[0], P[1] - Pelvis[1], P[2] - Pelvis[2] };
	Heading(Rel, O + 12 + N);
	if (Role < 0.f)	// defender: the striker's right hand - pelvis
	{
		const double* H = d->geom_xpos + 3 * Opp->Hand;
		const double Rh[3] = { H[0] - Pelvis[0], H[1] - Pelvis[1], H[2] - Pelvis[2] };
		Heading(Rh, O + 15 + N);
	}
}
