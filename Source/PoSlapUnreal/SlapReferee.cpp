#include "SlapReferee.h"

namespace
{
	/** The judges: speed, head snap, stagger, defence. Each weighs them differently. */
	constexpr int32 Weights[3][4] = { { 2, 1, 1, 1 }, { 1, 2, 1, 1 }, { 1, 1, 2, 2 } };
}

namespace Slap
{
	void FSlapReferee::Score(int32 (&Out)[3][2])
	{
		for (const FSlapTurn& R : RoundTurns)	// the decision's tiebreak: the hardest slap landed
		{
			if (R.TouchStep >= 0 && (R.Striker == 0 || R.Striker == 1)) Hardest[R.Striker] = FMath::Max(Hardest[R.Striker], R.Speed);
		}
		for (int32 j = 0; j < 3; ++j)	// 10-point must, three judges weighing (speed, head, stagger, defence) differently
		{
			double Eff[2] = { 0.0, 0.0 };
			bool Stag[2] = { false, false };
			for (const FSlapTurn& R : RoundTurns)
			{
				if (R.TouchStep < 0) continue;
				Eff[R.Striker] += Weights[j][0] * R.Speed + (Weights[j][1] * 5) * R.HeadPeak + (double)(Weights[j][2] * 10 * (R.bStagger ? 1 : 0));
				Stag[R.Striker] = R.bStagger;
				if (!R.bKO)	// the defender absorbed it: stayed put, small head snap
				{
					Eff[1 - R.Striker] += Weights[j][3] * ((double)(10 * (R.bStagger ? 0 : 1)) + FMath::Max(0.0, 10.0 - 5 * R.HeadPeak));
				}
			}
			const int32 Hi = Eff[1] > Eff[0] ? 1 : 0;
			int32 Pts[2] = { 10, 10 };
			if (FMath::Abs(Eff[0] - Eff[1]) > 0.05 * FMath::Max(Eff[0], Eff[1])) Pts[1 - Hi] = Stag[Hi] && Eff[Hi] >= 2 * Eff[1 - Hi] ? 8 : 9;
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
