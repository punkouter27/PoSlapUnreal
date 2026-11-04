// Eye contact (match.py GAZE_/WATCH_ constants; training/constants.py holds the numbers for every engine).

#include "SlapController.h"

#include "SlapControllerInternal.h"

using namespace SlapCtl;

void USlapController::SeedGaze(const mjData* d)
{
	for (int32 a = 0; a < 2 && Athletes.Num() == 2; ++a)	// each athlete's world gaze bias starts on the eye line
	{
		FAthlete& W = Athletes[a];
		const FAthlete& Fo = Athletes[1 - a];
		double Dx = d->geom_xpos[3 * Fo.Head] - d->geom_xpos[3 * W.Head];
		double Dy = d->geom_xpos[3 * Fo.Head + 1] - d->geom_xpos[3 * W.Head + 1];
		double Dz = d->geom_xpos[3 * Fo.Head + 2] - d->geom_xpos[3 * W.Head + 2];
		const double L = FMath::Sqrt(Dx * Dx + Dy * Dy + Dz * Dz);
		if (L > 1e-9) { W.BiasWX = Dx / L; W.BiasWY = Dy / L; W.BiasWZ = Dz / L; }
	}
}

// Eye contact: pure maths, mirrors match.py gaze_desire/gaze_walk/gaze_apply.
// PRE-touch: the defender's neck goals walk to meet the striker's eyes (world-space bias converted to the chest frame's
// yaw (the turn only: the nod stays the brain's) - world space because the chest WHIPS during a follow-through); he
// regains the neck the instant the slap lands (he braces).
// POST-touch: the striker's eyes track the defender to see whether he goes down, through the inter-turn hold too (the
// watch only: his body stays the brain's).
void USlapController::ApplyGaze(const mjModel* m, mjData* d)
{
	if (!(bEyeContact && Athletes.Num() == 2)) return;
	// in-turn after the touch, OR through the inter-turn hold: the striker keeps planting and watching the
	// verdict (the hold is display-only - the next turn resets everybody - so event parity is untouched)
	const bool bHold = !bInTurn && HoldLeft > 0 && MatchNo > 0 && Turn.TouchStep >= 0;
	if (!(bInTurn || bHold)) return;
	const bool bPlanted = Turn.TouchStep >= 0;
	FAthlete& Watcher = Athletes[bPlanted ? Turn.Striker : 1 - Turn.Striker];	// defender pre-touch, striker after
	const FAthlete& Focus = Athletes[bPlanted ? 1 - Turn.Striker : Turn.Striker];
	if (Watcher.YawIdx < 0) return;
	// NB (2026-10-25 QA): an imposed post-slap POSE hold was tried and rejected - freezing any joints after
	// the touch fought the brain's own balance and tripled striker self-knockdowns. The striker's "stay
	// balanced" duty is served by leaving the brain in charge of the body; only the WATCH below remains.
	const double* Own = d->geom_xpos + 3 * Watcher.Head;
	const double* Op = d->geom_xpos + 3 * Focus.Head;
	// desire: unit world direction from the watcher's eyes to the focus's eyes
	double Des[3] = { Op[0] - Own[0], Op[1] - Own[1], Op[2] - Own[2] };
	const double DL = FMath::Sqrt(Des[0] * Des[0] + Des[1] * Des[1] + Des[2] * Des[2]);
	if (DL < 1e-9) return;
	Des[0] /= DL; Des[1] /= DL; Des[2] /= DL;
	// walk the WORLD-space bias toward the desire by at most Walk radians (great-circle, never a teleport)
	const double Walk = bPlanted ? WatchMaxStep : GazeMaxStep;	// snappier while watching a falling opponent
	double WB[3] = { Watcher.BiasWX, Watcher.BiasWY, Watcher.BiasWZ };
	const double CosT = FMath::Clamp(WB[0] * Des[0] + WB[1] * Des[1] + WB[2] * Des[2], -1.0, 1.0);
	const double Theta = FMath::Acos(CosT);
	if (Theta <= Walk)
	{
		WB[0] = Des[0]; WB[1] = Des[1]; WB[2] = Des[2];
	}
	else
	{
		const double Px = Des[0] - CosT * WB[0], Py = Des[1] - CosT * WB[1], Pz = Des[2] - CosT * WB[2];
		const double PN = FMath::Sqrt(Px * Px + Py * Py + Pz * Pz);
		if (PN < 1e-9)	// antipodal: snap (unreachable for two facing athletes)
		{
			WB[0] = Des[0]; WB[1] = Des[1]; WB[2] = Des[2];
		}
		else
		{
			const double Cs = FMath::Cos(Walk), Sn = FMath::Sin(Walk);
			double V[3] = { WB[0] * Cs + Px / PN * Sn, WB[1] * Cs + Py / PN * Sn, WB[2] * Cs + Pz / PN * Sn };
			const double VL = FMath::Sqrt(V[0] * V[0] + V[1] * V[1] + V[2] * V[2]);
			WB[0] = V[0] / VL; WB[1] = V[1] / VL; WB[2] = V[2] / VL;
		}
	}
	Watcher.BiasWX = WB[0]; Watcher.BiasWY = WB[1]; Watcher.BiasWZ = WB[2];
	// project the world bias into the chest frame (the neck hinges ARE its Z/Y/X) and clamp to the joint range
	const double* Xm = d->xmat + 9 * Watcher.Chest;
	mjtNum Rx[3];
	mju_mulMatTVec3(Rx, Xm, WB);	// R^T * bias
	// turn only (2026-10-29): the nod tilted Kim's face up toward a taller Grandpa, off where his slap trained to
	// land; the nod stays the brain's in every engine (training/constants.py GAZE_* block: match.py, arena.py, sim.js)
	const int32 j = Watcher.YawIdx;
	const double Want = FMath::Clamp(FMath::Atan2(Rx[1], Rx[0]), Watcher.Lo[j], Watcher.Hi[j]);
	const double Blend = (1.0 - GazeGain) * Watcher.Goal[j] + GazeGain * Want;
	Watcher.Goal[j] = FMath::Clamp(Blend, Watcher.Lo[j], Watcher.Hi[j]);
}

