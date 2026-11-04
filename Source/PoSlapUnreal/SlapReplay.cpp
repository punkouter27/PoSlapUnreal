// The matching check (Blueprint 8): a recorded control stream played back, qpos dumped every step.

#include "SlapController.h"

#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "SlapControllerInternal.h"

using namespace SlapCtl;

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

