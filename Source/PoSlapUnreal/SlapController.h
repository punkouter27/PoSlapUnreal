#pragma once

#include "CoreMinimal.h"
#include <atomic>
#include "MuJoCo/Components/Controllers/MjArticulationController.h"
#include "SlapController.generated.h"

class UNNEModelData;
namespace UE::NNE { class IModelInstanceCPU; }

UENUM(BlueprintType)
enum class ESlapControlMode : uint8
{
	Replay,	// play a recorded control stream, dump qpos per step (matching check, Blueprint 8)
	Policy	// ONNX brain via NNE every 4th physics step (DOCS/BRAIN_IO.md)
};

/**
 * Drives every athlete in one URLab articulation, on the physics thread.
 * Athletes are found by body names ending in "pelvis" (prefix "red_", "blue_" or none).
 */
UCLASS(ClassGroup = (PoSlap), meta = (BlueprintSpawnableComponent))
class POSLAPUNREAL_API USlapController : public UMjArticulationController
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	ESlapControlMode Mode = ESlapControlMode::Policy;

	/** Replay: reference from training/replay_check.py, relative to the project dir. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	FString ReplayFile = TEXT("training/replays/grandpa_vs_grandpa_arena_5s.json");

	UPROPERTY(EditAnywhere, Category = "PoSlap")
	FString ReplayDump = TEXT("Saved/URLab/replay_ue.json");

	/** Policy: the brain (imported .onnx). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	TObjectPtr<UNNEModelData> Brain;

	/** Policy: joint order, ranges, stand pose, speed limit. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	FString Fingerprint = TEXT("training/models/grandpa_fingerprint.json");

	virtual void Bind(mjModel* m, mjData* d, const TMap<int32, UMjActuator*>& ActuatorIdMap) override;
	virtual void ComputeAndApply(mjModel* m, mjData* d, uint8 Source) override;
	virtual FString GetKindName() const override { return TEXT("poslap"); }

	/** Knockdowns + out-of-bounds seen so far (policy mode); each one resets both athletes in place. */
	std::atomic<int32> Resets{0};

private:
	struct FAthlete
	{
		FString Prefix;
		int32 Pelvis = -1, RootQ = -1, RootV = -1, Target = -1, TargetIsSite = 0, OppHand = -1;
		TArray<int32> Act, Qadr, Vadr, Feet;
		TArray<double> Stand, Lo, Hi, Goal, Root0;
		TArray<float> PrevAction;
	};

	/** Index of an athlete that is down (non-foot geom on floor/podium) or out of bounds, else -1. */
	int32 Fallen(const mjModel* m, const mjData* d, FString& Why) const;
	void ResetAthletes(const mjModel* m, mjData* d);
	int32 Floor = -1, Podium = -1;

	void BindReplay(mjModel* m);
	void BindPolicy(mjModel* m);
	void StepReplay(mjModel* m, mjData* d);
	void StepPolicy(mjModel* m, mjData* d);
	void BuildObs(const mjModel* m, const mjData* d, const FAthlete& A, float* Obs) const;

	int64 StepCount = 0;
	double MaxStep = 0.0;
	TArray<FAthlete> Athletes;
	TSharedPtr<UE::NNE::IModelInstanceCPU> Instance;

	// replay
	TArray<int32> ReplayAct;
	TArray<double> ReplayCtrl;
	TArray<TPair<int32, double>> ReplayQ0;
	TArray<double> Recorded;
	int32 ReplaySteps = 0;
	bool bReplayDone = false;
};
