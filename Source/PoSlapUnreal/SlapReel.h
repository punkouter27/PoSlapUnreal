#pragma once

// The highlight reel (display only). USlapController keeps every body's pose for the last ReelSteps physics steps;
// ASlapHUD saves that window after a knockout (Saved/PoSlap/reels/*.reel); a later launch with -SlapReel=<file> draws
// the athletes from the file, one recorded physics step per rendered frame, so a slow-motion render shows real 5 ms
// steps instead of interpolated ones (Scripts/highlight_reel.py renders and encodes it).

#include "CoreMinimal.h"

struct FSlapReel
{
	static constexpr int32 Steps = 1200;	// 6 s at 200 Hz
	static constexpr int32 Extra = 2;		// per step, after the poses: the striker's palm speed (m/s), the defender's head g
	static constexpr uint32 Magic = 0x4C525053;	// "SPRL"

	int32 NumBodies = 0, NumSteps = 0, Touch = -1;	// Touch: the step of the palm's contact, -1 = none in the window
	int32 Striker = 0;						// who struck at Touch (0 red, 1 blue)
	float Timestep = 0.005f;
	TArray<float> Data;	// NumSteps x (NumBodies x 7 + Extra): per body xpos (3), xquat (4: w x y z), MuJoCo's frame

	int32 Stride() const { return NumBodies * 7 + Extra; }
	bool IsValid() const { return NumBodies > 0 && NumSteps > 0 && Data.Num() == NumSteps * Stride(); }
	const float* Frame(int32 Step) const { return &Data[FMath::Clamp(Step, 0, NumSteps - 1) * Stride()]; }
	bool Save(const FString& Path) const;
	bool Load(const FString& Path);

	/** Playback (-SlapReel=<file>): the loaded reel and the step being drawn (ASlapHUD advances it, the athletes read it). */
	static FSlapReel& Playing();
	static int32 PlayStep;
};
