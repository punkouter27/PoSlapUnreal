#pragma once

#include "CoreMinimal.h"

/**
 * The match referee: the judges' cards, the turns and the decisions (training/match.py's rules, step for step).
 *
 * Pure bookkeeping: no MuJoCo, no physics, no HUD. USlapController owns one and calls it from the physics thread.
 * Everything here is a pure function of the turns it is given, so the scoring can be checked on its own.
 */
namespace Slap
{
	/** One turn's record: what the judges weigh, and what the broadcast shows. */
	struct FSlapTurn
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

	/** The three judges and the round they are scoring. */
	struct FSlapReferee
	{
		/** The judges' running totals (red, blue). */
		int32 Cards[3][2] = {};
		/** The turns of the round being scored. */
		TArray<FSlapTurn> RoundTurns;
		/** The hardest slap each athlete has landed this match (m/s): the decision's second tiebreak. */
		double Hardest[2] = {};

		/** A fresh match: no cards, no turns. */
		void Reset()
		{
			FMemory::Memzero(Cards, sizeof(Cards));
			FMemory::Memzero(Hardest, sizeof(Hardest));
			RoundTurns.Reset();
		}

		/**
		 * The round's points: Out[j] is the pair (red, blue) judge j awarded, and the judge's running total grows by
		 * it. The same arithmetic as training/match.py's score_round, in the same order: a 10-point must, and the
		 * three judges weigh speed, head snap, stagger and defence differently.
		 */
		void Score(int32 (&Out)[3][2]);

		/**
		 * The match decision: the wording and the winner (the corner's index). A judges' draw is broken as
		 * training/match.py's tiebreak does it (the higher total over the three cards; level, the harder slap landed;
		 * neither landed one, the athlete who struck second: First is the coin toss's winner), so there is always one.
		 */
		FString Decision(int32& Winner, int32 First) const;
	};
}
