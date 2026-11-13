#include "SlapReferee.h"

#include "SlapShared.gen.h"

namespace
{
	/** The judges: speed, head snap, stagger, defence. Each weighs them differently (training/rules.json judge_weights). */
	constexpr auto& Weights = SlapGen::JudgeWeights;
}

namespace Slap
{
	void FSlapReferee::Score(int32 (&Out)[3][2], const double (*Noise)[4])
	{
		for (const FSlapTurn& R : RoundTurns)	// the decision's tiebreak: the hardest slap landed
		{
			if (R.TouchStep >= 0 && (R.Striker == 0 || R.Striker == 1)) Hardest[R.Striker] = FMath::Max(Hardest[R.Striker], R.Speed);
		}
		for (int32 j = 0; j < 3; ++j)	// 10-point must, three judges weighing (speed, head, stagger, defence) differently
		{
			// the judge's weights as doubles: the integers themselves (rules 1: every product below is the same double the
			// integer arithmetic gave), or scaled by this round's taste (rules 2, match.py: w[i] * (1 + noise ...))
			double W[4];
			for (int32 i = 0; i < 4; ++i) W[i] = Noise ? (double)Weights[j][i] * Noise[j][i] : (double)Weights[j][i];
			double Eff[2] = { 0.0, 0.0 };
			bool Stag[2] = { false, false };
			bool Kd[2] = { false, false };	// rules 2: he put the other down and the other beat the count
			for (const FSlapTurn& R : RoundTurns)
			{
				if (R.TouchStep < 0) continue;
				// match.py: w[0] * speed + w[1] * 5 * head_peak + w[2] * 10 * stagger, left to right
				Eff[R.Striker] += W[0] * R.Speed + (W[1] * 5.0) * R.HeadPeak + (W[2] * 10.0) * (R.bStagger ? 1.0 : 0.0);
				Stag[R.Striker] = R.bStagger;
				Kd[R.Striker] = R.bKd;
				if (!R.bKO)	// the defender absorbed it: stayed put, small head snap
				{
					Eff[1 - R.Striker] += W[3] * ((double)(10 * (R.bStagger ? 0 : 1)) + FMath::Max(0.0, 10.0 - 5 * R.HeadPeak));
				}
			}
			const int32 Hi = Eff[1] > Eff[0] ? 1 : 0;
			int32 Pts[2] = { 10, 10 };
			if (FMath::Abs(Eff[0] - Eff[1]) > 0.05 * FMath::Max(Eff[0], Eff[1])) Pts[1 - Hi] = (Stag[Hi] && Eff[Hi] >= 2 * Eff[1 - Hi]) || Kd[Hi] ? 8 : 9;
			for (const FSlapTurn& R : RoundTurns)
			{
				for (const TPair<int32, FString>& F : R.Fouls) --Pts[F.Key];
			}
			Out[j][0] = Pts[0];
			Out[j][1] = Pts[1];
			Cards[j][0] += Pts[0];
			Cards[j][1] += Pts[1];
		}
	}

	FString FSlapReferee::Decision(int32& Winner, int32 First) const
	{
		// each judge who did not score a draw has a winner; three to one side is a unanimous decision
		int32 Wins[2] = { 0, 0 };
		for (int32 j = 0; j < 3; ++j)
		{
			if (Cards[j][0] != Cards[j][1]) ++Wins[Cards[j][1] > Cards[j][0] ? 1 : 0];
		}
		const int32 Top = Wins[1] > Wins[0] ? 1 : 0;
		FString How = TEXT("draw");
		if (Wins[Top] == 3) How = TEXT("unanimous decision");
		else if (Wins[Top] == 2) How = Wins[1 - Top] == 1 ? TEXT("split decision") : TEXT("majority decision");
		Winner = Top;
		if (How != TEXT("draw")) return How;
		const int32 Tot[2] = { Cards[0][0] + Cards[1][0] + Cards[2][0], Cards[0][1] + Cards[1][1] + Cards[2][1] };
		if (Tot[0] != Tot[1]) { Winner = Tot[1] > Tot[0] ? 1 : 0; return TEXT("decision on total points"); }
		if (Hardest[0] != Hardest[1]) { Winner = Hardest[1] > Hardest[0] ? 1 : 0; return TEXT("decision on the harder slap"); }
		Winner = 1 - First;
		return TEXT("decision: struck second");
	}
}
