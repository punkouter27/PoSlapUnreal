#include "SlapSwingRead.h"

void FSlapSwingRead::NewTurn()
{
	Score = 0.f;
	bReading = bReadPending = false;
	Peak = 0.0;
	bSwing = bTagged = false;
}

FSlapSwingRead::EEvent FSlapSwingRead::Update(double Now, float Dt, double HandSpeed, double HandDist, double ShoulderSpeed, bool bStruck)
{
	EEvent Out = EEvent::None;
	if (bReadPending && Now - ReadAt > ReadWindow)	// no strike in time: the read was wrong (a feint fooled it, or a pause)
	{
		bReadPending = false;
		Out = EEvent::ReadWrong;
	}
	if (bStruck) { Score = FMath::FInterpTo(Score, 0.f, Dt, ReadEase); return Out; }

	// FEINT: the palm peaked, then fell well under the peak without a touch
	if (HandSpeed > Peak) { Peak = HandSpeed; PeakAt = Now; bSwing = true; }
	if (bSwing && !bTagged && Now - PeakAt > FeintAfter && HandSpeed < FeintPullBack * Peak && Peak > FeintMinPeak)
	{
		bTagged = true;
		++Feints;
		Tag = TEXT("FEINT");
		TagAt = Now;
		Out = EEvent::Feint;
	}
	if (bTagged && HandSpeed > Peak * RearmShare && Now - PeakAt > RearmAfter) { bSwing = bTagged = false; Peak = 0.0; }	// a new swing

	// AI READ: the shoulder swinging and the palm speeding up, still out of reach
	const double Want = HandDist > ReadMinDist
		? FMath::Clamp(ShoulderSpeed / ReadShoulder, 0.0, 1.0) * FMath::Clamp(HandSpeed / ReadHand, 0.0, 1.0) : (bReading ? Score : 0.0);
	Score = FMath::FInterpTo(Score, (float)Want, Dt, ReadEase);
	if (!bReading && Score > ReadOn)
	{
		bReading = true;
		if (!bReadPending)	// one verdict per wind-up
		{
			bReadPending = true;
			ReadAt = Now;
			++Reads;
			PeakShoulder = ShoulderSpeed;
			PeakDist = HandDist;
			if (Out == EEvent::None) Out = EEvent::Read;
		}
	}
	else if (bReading && Score < ReadOff) bReading = false;
	return Out;
}

void FSlapSwingRead::OnStrike(double Now)
{
	++Reals;
	Tag = TEXT("REAL");
	TagAt = Now;
	bSwing = false;
	bTagged = true;	// the landed swing is classified; no feint until a new swing
	Peak = 0.0;
	if (bReadPending && Now - ReadAt <= ReadWindow) ++ReadsRight;
	bReadPending = false;
	bReading = false;
}
