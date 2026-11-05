#pragma once

// USlapController's own helpers, shared by its .cpp files (SlapController*.cpp, SlapBrainIO.cpp, SlapMatch.cpp, ...).
// Not a public header: nothing outside the controller includes it. Every number here is one of the three engines'
// shared constants (training/constants.py, brain_cpu.py, match.py; training/verify_constants.py checks the values).

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "SlapController.h"
#include "SlapNames.h"

#include <cmath>
#include <initializer_list>

namespace SlapCtl
{
	inline constexpr int32 ControlEvery = 4;	// 50 Hz brain over 200 Hz physics
	inline constexpr int32 NumObs = 140;		// DOCS/BRAIN_IO.md, interface 4: 136 + heel/toe contact per foot (136-139); a brain reads its first NumIn
	// the action low-pass (interface 4 only: brains >= LpfFromObs wide; training/constants.py LPF_*): before the rate limit,
	// target = goal + alpha (target - goal), alpha = 1 - exp(-2 pi LpfHz / 50). The same maths in brain_cpu, the envs and sim.js
	inline constexpr double LpfHz = 8.0;
	inline constexpr int32 LpfFromObs = 140;
	inline double LowPass(double Tgt, double Goal) { return Goal + (1.0 - std::exp(-2.0 * PI * LpfHz / 50.0)) * (Tgt - Goal); }
	inline constexpr int32 Lags[3] = { 5, 10, 15 };	// memory: inputs 102-107 as they were 0.1, 0.2, 0.3 s ago
	inline constexpr double HillK = 0.25;	// Hill's force-velocity curvature (brain_cpu.HILL_K)
	inline constexpr int32 React = 6;	// the defender sees the striker's hand 6 decisions (0.12 s) late (brain_cpu.REACT)
	inline constexpr double BoxX = 0.20, BoxY = 0.25;	// the foot box's half extents (match.py)
	inline constexpr int64 KickSteps = 2;	// the slap's impact impulse lasts 10 ms (match.py KICK_STEPS)
	using Slap::Rounds, Slap::MinStamina, Slap::MinNeck, Slap::StrikeClock, Slap::Recovery;

	/** brain_cpu.settle_muscles: each muscle's filter state at its joint's angle. */
	inline void SettleMuscles(const mjModel* m, mjData* d)
	{
		for (int32 a = 0; a < m->nu; ++a)
			if (m->actuator_actadr[a] >= 0) d->act[m->actuator_actadr[a]] = d->qpos[m->jnt_qposadr[m->actuator_trnid[2 * a]]];
	}

	/** Slap::LoadJson, with one error line when the file is missing. */
	inline TSharedPtr<FJsonObject> LoadJson(const FString& RelPath)
	{
		TSharedPtr<FJsonObject> Obj = Slap::LoadJson(RelPath);
		UE_CLOG(!Obj, LogSlap, Error, TEXT("Could not read %s"), *RelPath);
		return Obj;
	}

	/** A fingerprint's per-joint top speed (rad/s), joint order; older fingerprints carry one number for every joint. */
	inline double JointSpeed(const FJsonObject& Fp, int32 Joint)
	{
		const TArray<TSharedPtr<FJsonValue>>* Speeds;
		return Fp.TryGetArrayField(TEXT("max_joint_speed_rad_s"), Speeds) ? (*Speeds)[Joint]->AsNumber() : Fp.GetNumberField(TEXT("max_joint_speed_rad_s"));
	}

	/** One name per qpos slot; same as joint_names() in training/replay_check.py. */
	inline TArray<FString> QposNames(const mjModel* m)
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

	/** The geom of a contact that is not the floor or the podium, when exactly one side is ground; -1 otherwise. */
	inline int32 OnGround(const mjContact& C, int32 Floor, int32 Podium)
	{
		const bool S0 = C.geom[0] == Floor || C.geom[0] == Podium, S1 = C.geom[1] == Floor || C.geom[1] == Podium;
		return S0 == S1 ? -1 : S0 ? C.geom[1] : C.geom[0];
	}

	inline double Dist(const double* A, const double* B, int32 N)	// as match.py dist(): plain sum, same rounding
	{
		double S = 0.0;
		for (int32 k = 0; k < N; ++k) S += (A[k] - B[k]) * (A[k] - B[k]);
		return std::sqrt(S);
	}

	inline double RoundTo(double X, int32 Digits)	// Python round(x, n): the double nearest the correctly rounded decimal
	{
		return FCString::Atod(*FString::Printf(TEXT("%.*f"), Digits, X));
	}

	inline FString Part(const FString& Geom)	// match.py: re.sub(r"^.*?(red|blue)_", "", name)
	{
		const int32 R = Geom.Find(TEXT("red_"), ESearchCase::CaseSensitive), B = Geom.Find(TEXT("blue_"), ESearchCase::CaseSensitive);
		if (R < 0 && B < 0) return Geom;
		return (B < 0 || (R >= 0 && R < B)) ? Geom.Mid(R + 4) : Geom.Mid(B + 5);
	}

	inline FString Num(double X, int32 Digits) { return FString::Printf(TEXT("%.*f"), Digits, X); }

	inline uint32 Mix(std::initializer_list<uint32> V)	// the match's dice: match.py mix(), Web/sim.js mix(), the same bits
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
	inline FString Str(const FString& S) { return TEXT("\"") + S + TEXT("\""); }
	inline FString Bool(bool B) { return B ? TEXT("true") : TEXT("false"); }
	inline FString CardsJson(const int32 (&C)[3][2]) { return FString::Printf(TEXT("[[%d, %d], [%d, %d], [%d, %d]]"), C[0][0], C[0][1], C[1][0], C[1][1], C[2][0], C[2][1]); }

	/** An action in [-1, 1] -> joint target: +-1 reaches the range ends from the stand pose (each side its own scale), times Reach. */
	inline double Target(double Stand, double Lo, double Hi, double Act, double Reach = 1.0)
	{
		return FMath::Clamp(Stand + Reach * (Act > 0.0 ? Act * (Hi - Stand) : Act * (Stand - Lo)), Lo, Hi);
	}
}
