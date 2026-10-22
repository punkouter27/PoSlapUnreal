#pragma once

#include "CoreMinimal.h"
#include <atomic>
#include "MuJoCo/Components/Controllers/MjArticulationController.h"
#include "SlapController.generated.h"

class FArchive;
class FJsonObject;
class UNNEModelData;
namespace UE::NNE { class IModelInstanceCPU; }

/** What the HUD shows; copied from the physics thread under a lock (USlapController::GetHud). Index 0 = red, 1 = blue. */
struct FSlapHud
{
	int32 Match = 0, Round = 0;
	FString Striker, Defender;
	double Clock = 0.0;					// strike clock left (s); recovery left once struck
	bool bStruck = false;
	double Speed = 0.0, HeadPeak = 0.0;	// last strike: palm speed at the touch, defender head peak speed (m/s)
	FString Part, Event;				// last strike's part; last referee call
	int32 Cards[3][2] = {};				// judges' running totals (red, blue)
	int32 RoundCards[3][2] = {};		// the last scored round's points per judge (the slide-in card)
	int32 ScoredRound = 0;				// which round RoundCards holds (0 = none yet this match)
	FString Result;						// last match's result

	// live telemetry (50 Hz)
	double HandSpeed = 0.0, HandDist = 0.0;	// striker's palm speed (m/s), palm to the defender's head (m)
	double HeadG = 0.0;						// defender's head acceleration over the last 20 ms (g)
	FVector Com[2], Feet[2];				// centre of mass, midpoint of the soles (Unreal cm)
	double Stamina[2] = { 1.0, 1.0 }, Neck[2] = { 1.0, 1.0 };
	double Value[2] = {};					// each brain's critic: its own expected reward over the next ~2 s
	int32 Role[2] = {};						// the role each was playing then (+1 strike, -1 defend, 0 guard)

	// broadcast cues: a counter that changes once per event
	int32 StrikeSeq = 0, KOSeq = 0, MatchSeq = 0;
	FVector StrikePos, StrikeDir;			// contact point (Unreal cm), the palm's direction of travel
	int32 StrikeDefender = 0;

	// tale of the tape, series
	FString Name[2], Brain[2], Tape[2], Card[2];	// Card: the character card's name ("grandpa"): which brains fit
	int32 Wins[2] = {}, Draws = 0;
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

	/** Policy: the brain (imported .onnx); red's, and blue's too unless BlueBrain is set. `-SlapRedBrain=<.onnx>` overrides. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	TObjectPtr<UNNEModelData> Brain;

	/** Policy: blue's own brain (roster: every athlete its own). `-SlapBlueBrain=<.onnx>` overrides. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	TObjectPtr<UNNEModelData> BlueBrain;

	/** Policy: joint order, ranges, stand pose, speed limit; its card names the character card (fatigue, display name). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	FString Fingerprint = TEXT("training/models/grandpa_fingerprint.json");

	/** Policy: blue's own character (a mixed pairing, e.g. grandpa vs kim); empty = the same as red's. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	FString BlueFingerprint;

	/** Seconds both athletes stay where the match ended (the KO, the decision) before the next one starts. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	double PostMatchHold = 4.0;

	/** Seconds both athletes stand where a turn ended before the next turn's reset (display only: match.py parity holds). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	double TurnHold = 2.0;

	virtual void Bind(mjModel* m, mjData* d, const TMap<int32, UMjActuator*>& ActuatorIdMap) override;
	virtual void ComputeAndApply(mjModel* m, mjData* d, uint8 Source) override;
	virtual FString GetKindName() const override { return TEXT("poslap"); }
	virtual void BeginDestroy() override;

	/** Defender's role input: -1 = defend (brains from L3 on: sees the striker's head and hand), 0 = guard (earlier). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	int32 DefenderRole = -1;

	/** Thread-safe copy of the match state for the HUD. */
	FSlapHud GetHud() const { FScopeLock L(&HudLock); return Hud; }

	/** Menu (game thread): these brains for red / blue (null = keep the current one) and a fresh match from the next
	 *  physics step; the match in progress is abandoned, unscored. */
	void RequestMatch(UNNEModelData* Red, UNNEModelData* Blue);

private:
	struct FAthlete
	{
		FString Prefix, Name;	// "Arena_red_", "red"
		int32 Pelvis = -1, RootQ = -1, RootV = -1, Head = -1, Hand = -1;
		TArray<int32> Act, Qadr, Vadr, Feet, NeckAct, Palms;	// Palms: hand_l, hand_r (the defender's brace)
		TArray<double> Stand, Lo, Hi, Goal, Fr0;	// Fr0: the neck motors' own forcerange (lo, hi per motor)
		TArray<float> PrevAction;
		TSharedPtr<UE::NNE::IModelInstanceCPU> Net;
		int32 NumIn = 0;			// how many BRAIN_IO inputs this brain reads (108 before memory, 130)
		int32 NumOut = 1;
		float Hist[16][6] = {};		// inputs 102-107 of this turn's last 16 decisions (memory)
		bool bFresh = true;
		double Mem[2] = {}, Stamina = 1.0, NeckStrength = 1.0;
		float Value = 0.f;		// the critic's last estimate of this athlete's next ~2 s of reward
		int32 Role = 0;			// the role it was playing then (+1 strike, -1 defend, 0 guard)
		double StaminaPerStrike = 0.0, NeckDamage = 0.0;	// character card
		double MaxStep = 0.0;	// joint speed limit per brain step (card speed)
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
	int64 HoldLeft = 0;	// physics steps of the hold after a turn or a match
	static int64 HoldSteps(double Seconds);
	bool bInTurn = false;
	FTurn Turn;
	int32 Cards[3][2] = {};
	TArray<FTurn> RoundTurns;
	FArchive* Log = nullptr;
	FString Down(const mjModel* m, const mjData* d, const FAthlete& A, bool bDefender) const;	// non-foot part on floor/podium, else "" (the defender's palms may rest on the podium)
	void StartMatch(mjModel* m, mjData* d);
	void StartTurn(mjModel* m, mjData* d);
	bool PreStep(const mjModel* m, mjData* d);	// referee before the brains decide; false = the turn just ended
	void PostStep(const mjModel* m, const mjData* d);
	void EndTurn(const mjModel* m, mjData* d, const FString& End);
	void EndMatch(int32 Winner);	// series tally, HUD, post-match hold; -1 = draw / no contest
	void Score(int32 (&Pts)[3][2]);
	void Emit(const FString& Json);
	void Decide(const mjModel* m, mjData* d);
	void Telemetry(const mjModel* m, const mjData* d, double HandSpeed);

	// brains the menu picked, handed to the physics thread (RequestMatch -> StepPolicy)
	struct FNet { TSharedPtr<UE::NNE::IModelInstanceCPU> Net; int32 NumIn = 0, NumOut = 1; FString Label; };
	static bool MakeNet(UNNEModelData* Data, FNet& Out);
	FCriticalSection PendingLock;
	TOptional<FNet> Pending[2];
	std::atomic<bool> bPendingMatch = false;
	void ApplyPending();

	mutable FCriticalSection HudLock;
	FSlapHud Hud;
	void SetHud(TFunctionRef<void(FSlapHud&)> Edit);

	// live telemetry, physics thread
	double TelHeadPrev[3] = {}, TelHeadVel[3] = {}, TelHeadG = 0.0;
	bool bTelReady = false;

	void BindReplay(mjModel* m);
	void BindPolicy(mjModel* m, const FJsonObject* const (&Fp)[2]);	// red's, blue's
	void StartFromRecordedPose(const mjModel* m, mjData* d) const;
	void StepReplay(mjModel* m, mjData* d);
	void StepPolicy(mjModel* m, mjData* d);
	void BuildObs(const mjModel* m, const mjData* d, FAthlete& A, float Role, double Clock, const FAthlete* Opp, float* Obs) const;
	void WriteQposDump(const mjModel* m, const FString& RelPath);

	// live matching check (-SlapRecord=<file>): record qpos for RecordSteps physics steps from the stand pose
	FString RecordFile;
	int32 RecordSteps = 1000;

	int64 StepCount = 0;
	TArray<FAthlete> Athletes;
	TArray<float> Action;	// brain output scratch, sized once

	/** Runtime brains from -SlapRedBrain= / -SlapBlueBrain= (.onnx files), kept alive here. */
	UPROPERTY(Transient)
	TArray<TObjectPtr<UNNEModelData>> LoadedBrains;

	// replay
	TArray<int32> ReplayAct;
	TArray<double> ReplayCtrl;
	TArray<TPair<int32, double>> ReplayQ0;
	TArray<double> Recorded;
	int32 ReplaySteps = 0;
};
