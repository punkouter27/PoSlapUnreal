// Reward penalty weights and curriculum thresholds, editable in the editor (Po Game Standard C9 / C10).
// Mirrors training/configs/reward_config.json (what the Python trainer reads); tests/test_unreal_reward_mirror.py fails when the two differ.
// Changing a value here or there does NOT retrain the shipped policies: they keep the weights they were trained with.
#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "SlapRewardConfig.generated.h"

USTRUCT(BlueprintType)
struct POSLAPUNREAL_API FSlapCurriculumStage
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Curriculum") FString Stage;
	/** Gate value that promotes a policy past this stage (unit in Metric). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Curriculum") float Threshold = 0.f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Curriculum") FString Metric;
};

/** Reward weights (added as weight * term, negative = penalty) and curriculum gates. */
UCLASS(BlueprintType)
class POSLAPUNREAL_API USlapRewardConfig : public UPrimaryDataAsset
{
	GENERATED_BODY()

public:
	USlapRewardConfig();

	/** Joint torque squared: sum of actuator force^2. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Reward|Penalties") float TorqueSqWeight = -2e-06f;
	/** Joint angular velocity squared: sum of joint qvel^2. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Reward|Penalties") float JointAngVelSqWeight = -0.0001f;
	/** Action rate: |a_t - a_(t-1)|^2. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Reward|Penalties") float ActionRateSqWeight = -0.01f;
	/** Foot slip: planar foot speed while the foot is in contact. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Reward|Penalties") float FootSlipWeight = -0.3f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Curriculum") TArray<FSlapCurriculumStage> CurriculumStages;

	/** Index of Stage in CurriculumStages, or INDEX_NONE. */
	UFUNCTION(BlueprintPure, Category = "Curriculum") int32 FindStage(const FString& Stage) const;

private:
	void AddStage(const TCHAR* Stage, float Threshold, const TCHAR* Metric);
};
