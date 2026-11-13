// Brain I/O (DOCS/BRAIN_IO.md): the observation, the brains (ORT on desktop, the basic CPU rebuild on Android), the
// 50 Hz decision and the brain X-ray.

#include "SlapController.h"

#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "NNE.h"
#include "NNEModelData.h"
#include "NNERuntimeBasicCpuBuilder.h"
#include "NNERuntimeCPU.h"
#include "SlapControllerInternal.h"

using namespace SlapCtl;

namespace
{
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
			int32 Nd = 0;
			int64 Num = 1;
			if (!Int(Nd) || Nd < 1 || Nd > 2) return nullptr;
			TArray<int32>& D = Dims.AddDefaulted_GetRef();
			for (int32 k = 0; k < Nd; ++k)
			{
				int32 V = 0;
				if (!Int(V) || V < 1 || V > (1 << 20)) return nullptr;	// a dimension is bounded: the product cannot overflow
				D.Add(V);
				Num *= V;
				if (Num > (1 << 24)) return nullptr;	// and the tensor itself is bounded (16 M floats)
			}
			if (At + 4 * Num > Raw.Num()) return nullptr;
			TArray<float>& Values = T.AddDefaulted_GetRef();
			Values.SetNumUninitialized((int32)Num);
			FMemory::Memcpy(Values.GetData(), Raw.GetData() + At, 4 * Num);
			At += 4 * Num;
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

bool USlapController::Infer(FAthlete& A, const float* InObs, float& Value)
{
	return RunNet(*A.Net, A.NumIn, A.NumOut, InObs, Value, A.Action);
}

bool USlapController::RunNet(UE::NNE::IModelInstanceCPU& Net, int32 NumIn, int32 NumOut, const float* InObs, float& Value, TArray<float>& Out)
{
	const UE::NNE::FTensorBindingCPU In{ (void*)InObs, (uint64)NumIn * sizeof(float) };
	if (NumOut == 0)	// the rebuilt brain: one output, the actions then the value
	{
		Out.SetNumUninitialized(Out.Num() + 1, EAllowShrinking::No);
		const bool bOk = Net.RunSync({ In }, { { Out.GetData(), (uint64)Out.Num() * sizeof(float) } }) == UE::NNE::EResultStatus::Ok;
		Value = Out.Pop(EAllowShrinking::No);
		return bOk;
	}
	const UE::NNE::FTensorBindingCPU OutB[] = { { Out.GetData(), (uint64)Out.Num() * sizeof(float) }, { &Value, sizeof(float) } };
	return Net.RunSync({ In }, TConstArrayView<UE::NNE::FTensorBindingCPU>(OutB, NumOut)) == UE::NNE::EResultStatus::Ok;
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
			UE_LOG(LogSlap, Verbose, TEXT("first obs %s: %s"), *A.Prefix, *S);
		}
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			const double Step = A.MaxStep[j] * A.Stamina;	// fatigue: a tired athlete moves slower
			const double Act = FMath::Clamp((double)A.Action[j], -1.0, 1.0);
			double Tgt = Target(A.Stand[j], A.Lo[j], A.Hi[j], Act);
			if (A.NumIn >= LpfFromObs) Tgt = LowPass(Tgt, A.Goal[j]);	// interface 4: the 8 Hz low-pass, then the rate limit
			A.Goal[j] = FMath::Clamp(Tgt, A.Goal[j] - Step, A.Goal[j] + Step);
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
		ReelG = HeadG;
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
			if (Turn.TouchStep >= 0) H.HeadPeak = Turn.HeadPeak;	// the snap so far (display; StartTurn clears it, the turn's end rounds it)
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
		FMemory::Memcpy(Base, A.Action.GetData(), N * sizeof(float));
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
				for (int32 j = 0; j < N; ++j) Moved += FMath::Square(FMath::Clamp(A.Action[j], -1.f, 1.f) - FMath::Clamp(Base[j], -1.f, 1.f));
			}
			Out[g] = (float)FMath::Sqrt(Moved);
			Total += Out[g];
		}
		for (float& F : Out) F = Total > 1e-4 ? (float)(F / Total) : 0.f;
		FMemory::Memcpy(A.Action.GetData(), Base, N * sizeof(float));
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

	// interface 4 (136-139): heel/toe contact per foot. Always written; a brain reads only its own width (shipped: 136)
	FootContacts(m, d, A, O + 46 + N);
}

void USlapController::FootContacts(const mjModel* m, const mjData* d, const FAthlete& A, float* Out)
{
	// brain_cpu.foot_contacts: heel_l, toe_l, heel_r, toe_r. A contact of the sole box with a world geom (body 0: floor,
	// podium), its point behind (heel) / at or ahead of (toe) the box centre along the box's local x axis (its length).
	// d->contact is the last physics step's list, as in training and the browser at decision time.
	for (int32 k = 0; k < 4; ++k) Out[k] = 0.f;
	if (A.Feet.Num() < 2) return;
	for (int32 i = 0; i < d->ncon; ++i)
	{
		const mjContact& C = d->contact[i];
		for (int32 f = 0; f < 2; ++f)
		{
			const int32 G = A.Feet[f];
			const int32 Other = C.geom[0] == G ? C.geom[1] : C.geom[1] == G ? C.geom[0] : -1;
			if (Other < 0 || m->geom_bodyid[Other] != 0) continue;
			const double* P = d->geom_xpos + 3 * G;
			const double* X = d->geom_xmat + 9 * G;	// row-major: the box's x axis is column 0
			const double Ax = (C.pos[0] - P[0]) * X[0] + (C.pos[1] - P[1]) * X[3] + (C.pos[2] - P[2]) * X[6];
			Out[2 * f + (Ax >= 0.0 ? 1 : 0)] = 1.f;
		}
	}
}

