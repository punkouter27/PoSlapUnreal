#pragma once

#include "CoreMinimal.h"
#include <atomic>
#include "MuJoCo/Components/Controllers/MjArticulationController.h"
#include "SlapController.generated.h"

class FArchive;
class FJsonObject;
class UNNEModelData;
namespace UE::NNE { class IModelInstanceCPU; }

/** Match rules the HUD shows too (training/match.py, brain_cpu.py). */
namespace Slap
{
	inline constexpr int32 Rounds = 3;
	inline constexpr double MinStamina = 0.7, MinNeck = 0.5;	// fatigue floors
	inline constexpr double StrikeClock = 30.0, Recovery = 5.0;	// s: to land the strike; to stay up after it
	inline constexpr double CleanSpeed = 8.0;	// m/s: a clean strike (Blueprint L2)
}

/** What the HUD shows; copied from the physics thread under a lock (USlapController::GetHud). Index 0 = red, 1 = blue. */
struct FSlapHud
{
	int32 Match = 0, Round = 0;
	int64 Steps = 0;					// MuJoCo steps since the level bound (the top-centre step counter)
	double Timestep = 0.002;			// s per step
	FString Striker, Defender;
	double Clock = 0.0;					// strike clock left (s); recovery left once struck
	bool bStruck = false;
	double Speed = 0.0, HeadPeak = 0.0;	// last strike: palm speed at the touch, defender head peak speed (m/s)
	FString Part, Event;				// last strike's part; last referee call
	int32 Cards[3][2] = {};				// judges' running totals (red, blue)
	int32 RoundCards[3][2] = {};		// the last scored round's points per judge (the slide-in card)
	int32 ScoredRound = 0;				// which round RoundCards holds (0 = none yet this match)
	FString Result;						// last match's result
	bool bWalkIn = false;				// pre-match ring walk-in sequence active before Round 1 Turn 1
	double WalkInProgress = 1.0;		// 0.0 (aisle start) -> 1.0 (planted at podium footboxes)

	// live telemetry (50 Hz)
	double HandSpeed = 0.0, HandDist = 0.0;	// striker's palm speed (m/s), palm to the defender's head (m)
	double HeadG = 0.0;						// defender's head acceleration over the last 20 ms (g)
	FVector Com[2], Feet[2];				// centre of mass, midpoint of the soles (Unreal cm)
	double Stamina[2] = { 1.0, 1.0 }, Neck[2] = { 1.0, 1.0 };
	double StrikeMass[2] = {};				// character card: the body behind the palm (kg); the slap's pitch
	double Value[2] = {};					// each brain's critic: its own expected reward over the next ~2 s
	int32 Role[2] = {};						// the role each was playing then (+1 strike, -1 defend, 0 guard)

	// broadcast cues: a counter that changes once per event
	int32 StrikeSeq = 0, KOSeq = 0, MatchSeq = 0;
	FVector StrikePos, StrikeDir;			// contact point (Unreal cm), the palm's direction of travel
	int32 StrikeDefender = 0;

	// display-only publishes (read-only: nothing here reaches the physics). Every one of them is a counter the HUD
	// watches, so the effect fires exactly once per event.
	int32 TurnSeq = 0;					// a turn ended (the referee called it)
	bool bStagger = false;				// the last strike moved the defender's centre of mass over 5 cm
	FVector DownPos;					// where a body part (not a sole, not a palm) hit the floor or the podium (Unreal cm)
	int32 DownSeq = 0;					// a counter per knockdown: the dust puff's cue
	FVector StepPos[2];				// where each athlete's sole last touched the floor (Unreal cm)
	int32 StepSeq[2] = {};				// a counter per athlete: one per footfall

	// tale of the tape, series
	FString Name[2], Brain[2], Tape[2], Card[2];	// Card: the character card's name ("grandpa"): which brains fit
	int32 Wins[2] = {}, Draws = 0;

	// broadcast science, 50 Hz (display only: nothing here feeds back into the match)
	double Danger[2] = {};				// KO danger: the capture point outside the planted soles, 0 inside, 1 at 15 cm out
	FVector Capture[2];					// capture point on the floor (Unreal cm): where he would have to step to stop
	double Hic = 0.0, Bric = 0.0;		// the defender's head since the touch: HIC15, brain injury criterion (NHTSA)
	double Chain[5] = {};				// kinetic chain: when each link peaked before the touch (s): pelvis, waist, shoulder, elbow, palm
	int32 ChainOrder = -1;				// how many of the 4 hand-offs ran hips-to-hand; -1 = no touch this turn
	float SlowMo = 1.f;					// physics pace (1 = real time); the HUD slows the world clock with it
	float Focus[2][10] = {};			// brain X-ray: share of each input group in what each brain just reacted to (sums to 1)
	TArray<FVector> Ghost[2];			// brain X-ray: each athlete's target pose, body origins (Unreal cm)
	TArray<int32> GhostParent[2];		// per ghost body: its parent's index in Ghost[a], -1 = the pelvis
	TArray<float> Pose;					// second screen: per athlete root (7) + joints in fingerprint order, red then blue
	FString WalkNote;					// walk-in: which athletes walk on their own brain
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

	/** Seconds both athletes walk from the aisle to the podium before Round 1 begins (display only: match.py parity holds). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	double WalkInHold = 3.0;

	virtual void Bind(mjModel* m, mjData* d, const TMap<int32, UMjActuator*>& ActuatorIdMap) override;
	virtual void ComputeAndApply(mjModel* m, mjData* d, uint8 Source) override;
	virtual FString GetKindName() const override { return TEXT("poslap"); }
	virtual void BeginDestroy() override;

	/** Defender's role input: -1 = defend (brains from L3 on: sees the striker's head and hand), 0 = guard (earlier). */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	int32 DefenderRole = -1;

	/** Thread-safe copy of the match state for the HUD (into a kept FSlapHud: its strings reuse their buffers). */
	void GetHud(FSlapHud& Out) const { bHudReady.store(true, std::memory_order_relaxed); FScopeLock L(&HudLock); Out = Hud; Out.Steps = StepCount.load(std::memory_order_relaxed); Out.Timestep = Timestep; }

	/** Menu (game thread): these brains for red / blue (null = keep the current one) and a fresh match from the next
	 *  physics step; the match in progress is abandoned, unscored. */
	void RequestMatch(UNNEModelData* Red, UNNEModelData* Blue);

	/** HUD switches (game thread -> physics thread). */
	std::atomic<bool> bXRay = false;	// brain X-ray: occlusion runs and the target-pose ghost (doubles inference while on)
	std::atomic<bool> bSlowMo = true;	// live slow motion around the touch and a falling defender
	std::atomic<bool> bLiveFeed = false;	// second screen: keep match events for TakeEvents

	/** Second screen: the match events since the last call (JSONL lines, frames left out). */
	void TakeEvents(TArray<FString>& Out) { FScopeLock L(&HudLock); Out = MoveTemp(Outbox); Outbox.Reset(); }

private:
	struct FAthlete
	{
		FString Prefix, Name;	// "Arena_red_", "red"
		int32 Pelvis = -1, Chest = -1, RootQ = -1, RootV = -1, Head = -1, Hand = -1, HeadBody = -1;
		TArray<int32> Act, Qadr, Vadr, Feet, NeckAct, Palms;	// Palms: hand_l, hand_r (the defender's brace)
		TArray<double> Stand, Lo, Hi, Goal, Fr0;	// Fr0: the neck motors' own forcerange (lo, hi per motor)
		TArray<float> PrevAction;
		TSharedPtr<UE::NNE::IModelInstanceCPU> Net;
		int32 NumIn = 0;			// how many BRAIN_IO inputs this brain reads (108 before memory, 130 before the own palm, 136)
		int32 NumOut = 1;
		float Hist[16][6] = {};		// inputs 102-107 of this turn's last 16 decisions (memory)
		double Seen[7][3] = {};		// defender: the striker's hand at the last React + 1 decisions (React = 6, the .cpp)
		bool bFresh = true;
		double Mem[2] = {}, Stamina = 1.0, NeckStrength = 1.0;
		float Value = 0.f;		// the critic's last estimate of this athlete's next ~2 s of reward
		int32 Role = 0;			// the role it was playing then (+1 strike, -1 defend, 0 guard)
		double StaminaPerStrike = 0.0, NeckDamage = 0.0, StrikeMass = 0.0;	// character card
		double FaintChance = 0.0, FaintSeconds = 2.0, FaintStrength = 0.05;	// card: fainting (match.py faint_step)
		double WalkSpeed = USlapController::WalkSpeed;	// the walk-in command, m/s (card walk_speed_mps)
		TArray<double> FrAll;	// every motor's own forcerange (lo, hi per motor): a faint slackens them all
		TArray<double> MaxStep;	// joint speed limit per brain step, per joint (card speed)
		TArray<double> Vmax;	// the same per second: Hill's top speed (ApplyHill)
		TArray<float> Action;	// this athlete's own brain output buffer (a shared one would mix the two brains up)
		TArray<float> WalkAction;	// the same for the walking brain (the walk-in's S2 brain)
		int32 YawIdx = -1;	// index of neck_turn in the arrays above (eye contact turns the head; the nod stays the brain's)
		int32 HipL = -1, HipR = -1, KneeL = -1, KneeR = -1, AnkleL = -1, AnkleR = -1, ShSwingL = -1, ShSwingR = -1;
		int32 WaistTwist = -1, ElbowR = -1;	// kinetic chain links
		double BiasWX = 0.0, BiasWY = 0.0, BiasWZ = 1.0;	// WORLD-space gaze bias (unit); seeded at each turn start
		TArray<int32> Bodies, BodyParent;	// this athlete's bodies (id order) and each one's parent index in Bodies (-1 = root)
		bool bFootDown[2] = { false, false };	// each sole's last contact state (the footstep's rising edge)
		TArray<float> XPrev;				// brain X-ray: the observation at the last X-ray tick (empty = none yet)
		// walk-in on the walking brain (training/envs/locomotion.py S2): command along the aisle, its gait clock
		TSharedPtr<UE::NNE::IModelInstanceCPU> WalkNet;
		int32 WalkIn = 0, WalkOut = 1;
		double WalkPhase = 0.0;
		int32 WalkT = 0;
		bool bArrived = false, bFell = false;
	};

	// Eye contact + post-slap poise (match.py GAZE_/WATCH_/PLANT_/STAB_ constants): identical maths both sides -
	// the match log still compares event for event.
	static constexpr double GazeGain = 1.0;		// the bias OWNS the neck goal pre-touch
	static constexpr double GazeMaxStep = 0.08;	// rad per decision the bias may walk (deliberate head turn)
	static constexpr double WatchMaxStep = 0.25;// rad per decision while PLANTED (re-acquiring a snapping head)
	bool bEyeContact = true;					// -SlapEyeContact=0 disables (debug / A-B checks)
	void ApplyGaze(const mjModel* m, mjData* d);

	// ---- match (training/match.py, step for step: same rules, same order, same JSONL events)
	struct FTurn
	{
		int32 Striker = 0;
		int64 Step = 0, TouchStep = -1;			// physics steps into the turn
		FString Part;
		bool bPalm = false, bStagger = false, bKO = false;
		double Speed = 0.0, HeadPeak = 0.0, Com0[2] = {};
		TArray<TPair<int32, FString>> Fouls;	// (athlete, why)
		double Feet0[2][2][2] = {}, HandPrev[3] = {}, HeadPrev[3] = {};
		double Kick[6] = {};	// the slap's impact on the defender's head (force, torque) for KickSteps steps after the touch
		// broadcast science (display and the "impact" log line only)
		double HeadVel[3] = {}, HeadAcc[3] = {}, Hic = 0.0, Spin[3] = {}, Bric = 0.0;	// HeadAcc: the last 3 steps' g, newest first
		int32 HeadSamples = -1;	// -1: no head velocity yet; then the acceleration samples since the touch
		double Chain[5] = {};
		int32 ChainOrder = -1;
		int64 SlowSteps = 0;	// steps of pre-touch slow motion spent (capped: a slap that never lands)
		int64 FaintAt[2] = { -1, -1 }, FaintEnd[2] = { -1, -1 };	// per athlete: the steps a faint starts and ends, -1 = none
	};
	int32 Floor = -1, Podium = -1;
	int32 First = 0, Round = 0, TurnInRound = 0, MatchNo = 0, LastWinner = -1;
	uint32 Seed = 0;	// the match's dice (fainting), logged in match_start; -SlapSeed= fixes the first match's (checks)
	void SetForce(mjModel* m, const FAthlete& A, double Gain) const;	// every motor at Gain x its own force, the neck's damage on top
	void Faint(mjModel* m);	// a faint starting or ending this step (before the referee looks, as match.py)
	int64 HoldLeft = 0;	// physics steps of the hold after a turn or a match
	int64 WalkInLeft = 0, WalkInTotal = 0;	// physics steps of the pre-match aisle walk-in before Round 1 Turn 1
	mutable std::atomic<bool> bHudReady = false;	// set once ASlapHUD::DrawHUD begins rendering so walk-in isn't consumed during level load
	bool bWalkIn = true;	// -SlapWalkIn=0 disables pre-match walk-in
	static int64 HoldSteps(double Seconds);
	void StepWalkIn(const mjModel* m, mjData* d);
	void PlaceWalkers(const mjModel* m, mjData* d);	// brain walkers back at the top of the aisle, standing
	static constexpr double Aisle = 2.4;	// m behind the foot box where a brain walker starts
	static constexpr double WalkSpeed = 1.1;	// m/s (S2 trained 1.0-1.4)
	static constexpr double WalkTimeout = 8.0;	// s: whoever hasn't arrived is placed at the box by the first turn's reset
	bool bInTurn = false;
	FTurn Turn;
	int32 Cards[3][2] = {};
	TArray<FTurn> RoundTurns;
	FArchive* Log = nullptr;
	FString Down(const mjModel* m, const mjData* d, const FAthlete& A, bool bDefender, FVector* OutPos = nullptr) const;	// non-foot part on floor/podium, else "" (the defender's palms may rest on the podium)
	void StartMatch(mjModel* m, mjData* d);
	void StartTurn(mjModel* m, mjData* d);
	bool PreStep(const mjModel* m, mjData* d);	// referee before the brains decide; false = the turn just ended
	void PostStep(const mjModel* m, const mjData* d);
	void EndTurn(const mjModel* m, mjData* d, const FString& End);
	void EndMatch(int32 Winner);	// series tally, HUD, post-match hold; -1 = draw / no contest
	void Score(int32 (&Pts)[3][2]);
	void Emit(FStringView Json);
	void Decide(const mjModel* m, mjData* d);
	void Telemetry(const mjModel* m, const mjData* d, double HandSpeed);

	// brains the menu picked, handed to the physics thread (RequestMatch -> StepPolicy)
	struct FNet { TSharedPtr<UE::NNE::IModelInstanceCPU> Net; int32 NumIn = 0, NumOut = 1; FString Label; };	// NumOut 0: one output, actions then value (Android)
	static bool MakeNet(UNNEModelData* Data, FNet& Out);
	FCriticalSection PendingLock;
	TOptional<FNet> Pending[2];
	std::atomic<bool> bPendingMatch = false;
	void ApplyPending();

	mutable FCriticalSection HudLock;
	FSlapHud Hud;
	TArray<FString> Outbox;	// second screen: events not yet taken (HudLock)
	void SetHud(TFunctionRef<void(FSlapHud&)> Edit);
	void HudBalance(const mjModel* m, mjData* d, FSlapHud& H);	// balance, KO danger, the second screen's pose
	double DangerNow[2] = {};	// the last KO danger HudBalance worked out (slow motion follows the defender's)
	double Wobble(const mjModel* m, const mjData* d, const FAthlete& A, double (&Cp)[2]) const;	// capture point, its gap outside the soles (m)

	// live telemetry, physics thread
	double TelHeadPrev[3] = {}, TelHeadVel[3] = {}, TelHeadG = 0.0;
	bool bTelReady = false;

	// broadcast science, physics thread
	void Impact(const mjModel* m, const mjData* d);	// HIC15 / BrIC of the defender's head, every step after the touch
	float ChainBuf[120][5] = {};	// the striker's link speeds, last 0.6 s (ring): pelvis yaw, waist, shoulder, elbow, palm
	int32 ChainAt = 0, ChainNum = 0;
	void ChainSample(const mjData* d, double PalmSpeed);
	void ChainAtTouch(double Dt);
	double PrevHandDist = -1.0, Ttc = 99.0, Pace = 1.0;	// time to contact (s), the pace URLab runs at
	void SetPace(const mjModel* m);
	mjData* GhostD = nullptr;	// brain X-ray: scratch data for the target pose's kinematics
	int32 XTick = 0;
	void XRay(FAthlete& A, const float* Obs, float (&Out)[10]);	// what this decision reacted to, per input group

	void BindReplay(mjModel* m);
	void BindPolicy(mjModel* m, const FJsonObject* const (&Fp)[2]);	// red's, blue's
	void BindHillMotors(mjModel* m, const FJsonObject* const (&Fp)[2]);	// replay path: the Hill cap without the athletes
	void StartFromRecordedPose(const mjModel* m, mjData* d) const;
	void StepReplay(mjModel* m, mjData* d);
	void StepPolicy(mjModel* m, mjData* d);
	void ApplyHill(mjModel* m, const mjData* d);	// Hill's force-velocity cap for the coming step (FrSaved: the uncapped forceranges)
	/** One motor for the Hill cap: the actuator, the joint's velocity dof and the joint's top speed (rad/s). */
	struct FMotor { int32 Act = -1, Vadr = -1; double Vmax = 0; };
	TArray<FMotor> HillMotors;	// every motor of both athletes, in joint order (the policy path fills it from the athletes)
	TArray<mjtNum> FrSaved;
	void BuildObs(const mjModel* m, const mjData* d, FAthlete& A, float Role, double Clock, const FAthlete* Opp, float* Obs, const float* Cmd = nullptr) const;	// Cmd: inputs 102-107 (the walking brain's command)
	void WriteQposDump(const mjModel* m, const FString& RelPath);

	// live matching check (-SlapRecord=<file>): record qpos for RecordSteps physics steps from the stand pose
	FString RecordFile;
	int32 RecordSteps = 1000;

	std::atomic<int64> StepCount = 0;	// physics thread writes; the HUD's step counter reads it (GetHud)
	double Timestep = 0.002;	// the model's (fingerprint) timestep: the HUD's sim clock
	TArray<FAthlete> Athletes;
	bool Infer(FAthlete& A, const float* InObs, float& Value);	// A's brain on InObs: its actions into A.Action, its critic into Value
	bool RunNet(UE::NNE::IModelInstanceCPU& Net, int32 NumIn, int32 NumOut, const float* InObs, float& Value, TArray<float>& Out);

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
