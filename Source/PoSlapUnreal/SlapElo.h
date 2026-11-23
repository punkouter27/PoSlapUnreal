// The Elo rule every completed 1v1 bout follows (menu START, watch, demo / attract, -SlapLoop, tournament): start 1000,
// K 32, a draw scores 0.5 each, a character never plays itself. ASlapHUD::RoundTrip applies it; SlapEloTests.cpp checks it.

#pragma once

#include "CoreMinimal.h"

namespace SlapElo
{
	inline constexpr double Start = 1000.0;
	inline constexpr double K = 32.0;

	inline double Expected(double A, double B) { return 1.0 / (1.0 + FMath::Pow(10.0, (B - A) / 400.0)); }

	/** One bout: ScoreA 1 (A won), 0.5 (a draw), 0 (B won). Moves both ratings; returns A's change (B's is minus it). */
	inline double Apply(double& A, double& B, double ScoreA)
	{
		const double D = K * (ScoreA - Expected(A, B));
		A += D;
		B -= D;
		return D;
	}

	/** Two characters are rated against each other: both known, and not the same character (a mirror match). */
	inline bool Rated(const FString& A, const FString& B) { return !A.IsEmpty() && !B.IsEmpty() && A != B; }

	enum class EOutcome : uint8 { Pending, NoContest, RedWins, BlueWins, Draw };

	/**
	 * A match's outcome from the controller's counters. Pending: no result yet (a match still running, or one aborted /
	 * reset / left before its end: those never get one), or the result line has landed a frame before its win / draw
	 * count (a KO). NoContest: a result with no series point ("No contest ..."): nothing is rated.
	 */
	inline EOutcome Outcome(const FString& Result, const int32 Wins[2], int32 Draws, const int32 SeenWins[2], int32 SeenDraws)
	{
		if (Result.IsEmpty()) return EOutcome::Pending;
		if (Wins[0] > SeenWins[0]) return EOutcome::RedWins;
		if (Wins[1] > SeenWins[1]) return EOutcome::BlueWins;
		if (Draws > SeenDraws) return EOutcome::Draw;
		return Result.StartsWith(TEXT("No contest")) ? EOutcome::NoContest : EOutcome::Pending;
	}
}
