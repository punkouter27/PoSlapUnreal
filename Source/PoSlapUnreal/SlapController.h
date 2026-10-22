#pragma once

#include "CoreMinimal.h"
#include "MuJoCo/Components/Controllers/MjArticulationController.h"
#include "SlapController.generated.h"

class FArchive;
class FJsonObject;
class UNNEModelData;
namespace UE::NNE { class IModelInstanceCPU; }

/** What the HUD shows; copied from the physics thread under a lock (USlapController::GetHud). */
struct FSlapHud
{
	int32 Match = 0, Round = 0;
	FString Striker, Defender;
	double Clock = 0.0;					// strike clock left (s); recovery left once struck
	bool bStruck = false;
	double Speed = 0.0, HeadPeak = 0.0;	// last strike: palm speed at the touch, defender head peak speed (m/s)
	FString Part, Event;				// last strike's part; last referee call
	int32 Cards[3][2] = {};				// judges' running totals (red, blue)
	FString Result;						// last match's result
};

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
	virtual void BeginDestroy() override;

	/** Defender's role input: -1 = defend (brains from L3 on: sees the striker's head and hand), 0 = guard (earlier). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	int32 DefenderRole = -1;

	/** Thread-safe copy of the match state for the HUD. */
	FSlapHud GetHud() const { FScopeLock L(&HudLock); return Hud; }

private:
	struct FAthlete
	{
		FString Prefix, Name;	// "Arena_red_", "red"
		int32 Pelvis = -1, RootQ = -1, RootV = -1, Head = -1, Hand = -1;
		TArray<int32> Act, Qadr, Vadr, Feet;
		TArray<double> Stand, Lo, Hi, Goal;
		TArray<float> PrevAction;
	};

	// ---- match (training/match.py, step for step: same rules, same order, same JSONL events)
	struct FTurn
	{
		int32 Round = 0, Striker = 0;
		int64 Step = 0, TouchStep = -1;			// physics steps into the turn
		FString Part, End;
		bool bPalm = false, bStagger = false, bKO = false;
		double Speed = 0.0, HeadPeak = 0.0, Com0[2] = {};
		TArray<TPair<int32, FString>> Fouls;	// (athlete, why)
		double Feet0[2][2][2] = {}, HandPrev[3] = {}, HeadPrev[3] = {};
	};
	int32 Floor = -1, Podium = -1;
	int32 First = 0, Round = 0, TurnInRound = 0, MatchNo = 0;
	bool bInTurn = false;
	FTurn Turn;
	int32 Cards[3][2] = {};
	TArray<FTurn> RoundTurns;
	FArchive* Log = nullptr;
	FString Down(const mjModel* m, const mjData* d, const FAthlete& A) const;	// non-foot part on floor/podium, else ""
	void StartMatch(const mjModel* m, mjData* d);
	void StartTurn(const mjModel* m, mjData* d);
	bool PreStep(const mjModel* m, mjData* d);	// referee before the brains decide; false = the turn just ended
	void PostStep(const mjModel* m, const mjData* d);
	void EndTurn(const mjModel* m, mjData* d, const FString& End);
	void Score();
	void Emit(const FString& Json);
	void Decide(const mjModel* m, mjData* d);

	mutable FCriticalSection HudLock;
	FSlapHud Hud;
	void SetHud(TFunctionRef<void(FSlapHud&)> Edit);

	void BindReplay(mjModel* m);
	void BindPolicy(mjModel* m, const FJsonObject& Fp);
	void StartFromRecordedPose(const mjModel* m, mjData* d) const;
	void StepReplay(mjModel* m, mjData* d);
	void StepPolicy(mjModel* m, mjData* d);
	void BuildObs(const mjModel* m, const mjData* d, const FAthlete& A, float Role, double Clock, const FAthlete* Opp, float* Obs) const;
	void WriteQposDump(const mjModel* m, const FString& RelPath);

	// live matching check (-SlapRecord=<file>): record qpos for RecordSteps physics steps from the stand pose
	FString RecordFile;
	int32 RecordSteps = 1000;

	int64 StepCount = 0;
	double MaxStep = 0.0;
	TArray<FAthlete> Athletes;
	TSharedPtr<UE::NNE::IModelInstanceCPU> Instance;
	TArray<float> Action;	// brain output scratch, sized once

	// replay
	TArray<int32> ReplayAct;
	TArray<double> ReplayCtrl;
	TArray<TPair<int32, double>> ReplayQ0;
	TArray<double> Recorded;
	int32 ReplaySteps = 0;
};
