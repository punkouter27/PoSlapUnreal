#pragma once

#include "CoreMinimal.h"
#include "SlapShared.gen.h"

/**
 * The broadcast's read of the striker's swing, from the controller's 50 Hz telemetry (display only: it reads the
 * palm's speed, the palm-to-head distance and the shoulder's swing speed, and feeds nothing back).
 *
 * - FEINT / REAL: a swing that starts, peaks and pulls back without contact is a FEINT; a swing that lands is REAL.
 *   The rule is the HUD's original one (2026-10-29), only named.
 * - AI READ: the wind-up, before the palm is in reach: the shoulder already swinging and the palm accelerating while it
 *   is still out of reach. A read is scored right when a strike lands within ReadWindow, wrong otherwise.
 */
struct FSlapSwingRead
{
	// FEINT / REAL
	static constexpr double FeintMinPeak = SlapGen::FeintMinPeak;	// m/s: a palm peak below this is a twitch, not a swing
	static constexpr double FeintPullBack = SlapGen::FeintPullback;	// the palm drops under this share of its peak ...
	static constexpr double FeintAfter = SlapGen::FeintAfterS;		// ... this long (s) after the peak, with no contact: a feint
	static constexpr double RearmShare = SlapGen::FeintRearm;		// after a feint, a palm back at this share of the peak ...
	static constexpr double RearmAfter = SlapGen::FeintRearmS;		// ... this long (s) after it is a new swing
	static constexpr double TagSeconds = 1.4;		// how long a FEINT / REAL tag stays over the athlete

	// AI READ
	static constexpr double ReadShoulder = 3.0;		// rad/s of shoulder swing for a full read
	static constexpr double ReadHand = 2.5;			// m/s of palm speed for a full read
	static constexpr double ReadMinDist = 0.30;		// m: closer than this the palm is already striking, not winding up
	static constexpr double ReadOn = 0.55, ReadOff = 0.25;	// the read's hysteresis (score)
	static constexpr double ReadWindow = 1.0;		// s: a read is right when a strike lands within this
	static constexpr double ReadEase = 8.0;			// the score's smoothing rate (1/s)

	enum class EEvent : uint8 { None, Feint, Read, ReadWrong };

	/** A new turn (or match): the swing and the read start over; the tallies stay for the match. */
	void NewTurn();
	/** A new match: the tallies too. */
	void Reset() { *this = FSlapSwingRead(); }
	/** One frame before the touch. Now: game time (it follows the physics pace). Returns what happened this frame. */
	EEvent Update(double Now, float Dt, double HandSpeed, double HandDist, double ShoulderSpeed, bool bStruck);
	/** The touch: the swing is REAL, a pending read was right. */
	void OnStrike(double Now);

	float Score = 0.f;			// 0-1: how strongly this looks like a wind-up right now
	bool bReading = false;		// the read is on (Score crossed ReadOn and has not dropped under ReadOff)
	double ReadAt = -100.0;		// when the last read fired
	bool bReadPending = false;	// a read waiting for its verdict
	int32 Reads = 0, ReadsRight = 0;
	int32 Feints = 0, Reals = 0;
	FString Tag;				// "FEINT" / "REAL"
	double TagAt = -100.0;
	double Peak = 0.0, PeakAt = -100.0;	// the swing's palm peak and when it was
	double PeakShoulder = 0.0, PeakDist = 0.0;	// at the read: what it saw (the log line)

private:
	bool bSwing = false, bTagged = false;
};
