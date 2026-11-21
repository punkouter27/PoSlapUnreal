#include "SlapRewardConfig.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(SlapRewardConfig)

USlapRewardConfig::USlapRewardConfig()
{
	// Same order and values as training/configs/reward_config.json "curriculum".
	AddStage(TEXT("L0"), 2400.0f, TEXT("mean episode return over 100 episodes (guard role): stand 30 s"));
	AddStage(TEXT("L1"), 2500.0f, TEXT("mean episode return over 100 episodes (guard role): guard hold in the foot box"));
	AddStage(TEXT("L2"), 100.0f, TEXT("mean episode return over 100 episodes (strike role): strike a fixed head target"));
	AddStage(TEXT("L3"), 800.0f, TEXT("mean episode return over 100 episodes (absorb role): absorb a scripted slap"));
	AddStage(TEXT("L4"), 100.0f, TEXT("mean episode return over 100 episodes (strike role): self-play duel"));
	AddStage(TEXT("S2"), 1800.0f, TEXT("mean episode return over 100 episodes: steady alternating steps"));
	AddStage(TEXT("S3"), 2400.0f, TEXT("mean episode return over 100 episodes: step up / down, around the podium"));
	AddStage(TEXT("S4"), 3000.0f, TEXT("mean episode return over 100 episodes: the aisle lap"));
	AddStage(TEXT("S5"), 2400.0f, TEXT("mean episode return over 100 episodes: S3 course on slopes"));
	AddStage(TEXT("G1"), 250.0f, TEXT("mean episode return over 100 episodes: get up from a fall"));
}

void USlapRewardConfig::AddStage(const TCHAR* Stage, float Threshold, const TCHAR* Metric)
{
	FSlapCurriculumStage& S = CurriculumStages.AddDefaulted_GetRef();
	S.Stage = Stage;
	S.Threshold = Threshold;
	S.Metric = Metric;
}

int32 USlapRewardConfig::FindStage(const FString& Stage) const
{
	return CurriculumStages.IndexOfByPredicate([&Stage](const FSlapCurriculumStage& S) { return S.Stage == Stage; });
}
