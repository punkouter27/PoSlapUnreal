// The pre-match walk-in: the walking brain (S2) or the scripted stride, then the settle onto the stand pose.

#include "SlapController.h"

#include "SlapControllerInternal.h"

using namespace SlapCtl;

void USlapController::PlaceWalkers(const mjModel* m, mjData* d)
{
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		FAthlete& A = Athletes[a];
		if (!A.WalkNet) continue;
		for (int32 k = 0; k < 7; ++k) d->qpos[A.RootQ + k] = m->qpos0[A.RootQ + k];
		d->qpos[A.RootQ] += a == 0 ? -Aisle : Aisle;	// red comes in from -X, blue from +X
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			d->qpos[A.Qadr[j]] = A.Stand[j];
			d->qvel[A.Vadr[j]] = 0.0;
		}
		for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0.0;
	}
	SettleMuscles(m, d);
	mj_forward(m, d);
}

void USlapController::HoldWalkers(const mjModel* m, mjData* d)
{
	for (FAthlete& A : Athletes)
	{
		if (!A.WalkNet || A.WalkHold.Num() != 7 + A.Act.Num()) continue;
		for (int32 k = 0; k < 7; ++k) d->qpos[A.RootQ + k] = A.WalkHold[k];
		for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0.0;
		for (int32 j = 0; j < A.Act.Num(); ++j)
		{
			d->qpos[A.Qadr[j]] = A.WalkHold[7 + j];
			d->qvel[A.Vadr[j]] = 0.0;
			A.Goal[j] = A.WalkHold[7 + j];
		}
	}
	mj_forward(m, d);
}

void USlapController::BeginSettle(const mjModel* m, const mjData* d, FAthlete& A, bool bDown) const
{
	A.SettleFrom.SetNumUninitialized(7 + A.Act.Num());
	for (int32 k = 0; k < 7; ++k) A.SettleFrom[k] = d->qpos[A.RootQ + k];
	for (int32 j = 0; j < A.Act.Num(); ++j) A.SettleFrom[7 + j] = d->qpos[A.Qadr[j]];
	const double Off = FMath::Sqrt(FMath::Square(d->qpos[A.RootQ] - m->qpos0[A.RootQ]) + FMath::Square(d->qpos[A.RootQ + 1] - m->qpos0[A.RootQ + 1]));
	const double Seconds = FMath::Min(SettleMax, (bDown ? 1.2 : 0.6) + Off);	// longer from the floor or from farther off
	A.SettleK = 0;
	A.SettleN = FMath::Max<int64>(1, HoldSteps(Seconds));
}

void USlapController::StepSettle(const mjModel* m, mjData* d, FAthlete& A) const
{
	A.SettleK = FMath::Min(A.SettleK + 1, A.SettleN);
	const double S = (double)A.SettleK / (double)A.SettleN;
	const double U = S * S * (3.0 - 2.0 * S);
	for (int32 k = 0; k < 3; ++k) d->qpos[A.RootQ + k] = FMath::Lerp(A.SettleFrom[k], m->qpos0[A.RootQ + k], U);
	const double* Q0 = A.SettleFrom.GetData() + 3;	// the root's turn: nlerp on the near hemisphere
	const mjtNum* Q1 = m->qpos0 + A.RootQ + 3;
	const double Sgn = Q0[0] * Q1[0] + Q0[1] * Q1[1] + Q0[2] * Q1[2] + Q0[3] * Q1[3] < 0.0 ? -1.0 : 1.0;
	mjtNum Q[4];
	for (int32 k = 0; k < 4; ++k) Q[k] = FMath::Lerp(Sgn * Q0[k], Q1[k], U);
	mju_normalize4(Q);
	for (int32 k = 0; k < 4; ++k) d->qpos[A.RootQ + 3 + k] = Q[k];
	for (int32 j = 0; j < A.Act.Num(); ++j)
	{
		const double V = FMath::Lerp(A.SettleFrom[7 + j], A.Stand[j], U);
		d->qpos[A.Qadr[j]] = V;
		d->qvel[A.Vadr[j]] = 0.0;
		A.Goal[j] = V;
	}
	for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0.0;
}

void USlapController::StepWalkIn(const mjModel* m, mjData* d)
{
	// Pre-match walk-in. An athlete with a walking brain (S2) walks the aisle on it - MuJoCo moves him; the brain gets a
	// speed and a heading toward his foot box, as in training (envs/locomotion.py) - and his match brain takes over, on
	// guard, once he is there. Without one, the scripted stride: the root slides in from 1.6 m while the legs swing on a
	// clock. Display only either way: when the walk-in ends, StartTurn() resets mjData to the exact stand pose (parity).
	// A brain walker who arrives (or falls, or runs out of time) settles from where he is onto that stand pose and holds
	// it while the other finishes, so the reset moves nobody: no teleport at the podium (user 2026-10-30).
	const double Prog = FMath::Clamp((WalkInTotal - WalkInLeft) * m->opt.timestep / FMath::Max(WalkInHold, 0.1), 0.0, 1.0);	// the scripted stride's
	const double S = FMath::Clamp(Prog / 0.85, 0.0, 1.0);
	const double U = S * S * (3.0 - 2.0 * S);
	const double Offset = 1.60 * (1.0 - U);
	const double Env = Prog < 0.85 ? FMath::Sin(PI * S) : 0.0;
	const double Phase = 2.0 * PI * 1.8 * Prog * WalkInHold;
	float Obs[NumObs];
	const bool bRunNet = StepCount % ControlEvery == 0;	// 50 Hz, also while WalkInLeft holds still (level load)
	const bool bReady = HudLive();
	if (bRunNet && bReady) PublishFootsteps(d);	// display only: the walk-in's footfalls (the deck outside the mat: ASlapHUD::Steps)
	if (!bReady)	// nothing is being drawn (level load, a hitch): the walk waits for the screen
	{
		if (WalkInLeft == WalkInTotal) PlaceWalkers(m, d);	// not begun: at the top of the aisle
		else HoldWalkers(m, d);	// begun: frozen where the screen last showed them
	}
	FString Note;
	bool bDone = true;	// everybody on the stand pose
	bool bPlaced = false;	// a settling athlete was placed this step
	const bool bLate = bReady && WalkInLeft * m->opt.timestep <= SettleMax;	// the timeout is near: whoever still walks settles now
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		FAthlete& A = Athletes[a];
		if (A.WalkNet)
		{
			if (bReady && A.SettleN == 0 && bLate) BeginSettle(m, d, A, false);
			if (A.SettleN > 0)
			{
				if (bReady) { StepSettle(m, d, A); bPlaced = true; }
				bDone &= A.SettleK >= A.SettleN;
				continue;
			}
			bDone = false;
			if (!bReady || !bRunNet) continue;
			const FString Fell = Down(m, d, A, false);
			if (!Fell.IsEmpty() || d->xpos[3 * A.Pelvis + 2] < 0.45)	// locomotion.py's fall
			{
				UE_LOG(LogSlap, Log, TEXT("walk-in: %s down after %d decisions (%s, pelvis %.2f m, %.2f m from the box)"), *A.Name, A.WalkT,
					Fell.IsEmpty() ? TEXT("pelvis low") : *Part(Fell), d->xpos[3 * A.Pelvis + 2], FMath::Abs(m->qpos0[A.RootQ] - d->xpos[3 * A.Pelvis]));
				A.bFell = true;
				Note = A.Name.ToUpper() + TEXT(" stumbles on the walk-in!");
				BeginSettle(m, d, A, true);	// he gets up onto his box
				continue;
			}
			const double* P = d->xpos + 3 * A.Pelvis;
			const double Gx = m->qpos0[A.RootQ] - P[0], Gy = m->qpos0[A.RootQ + 1] - P[1];
			if (!A.bArrived && (a == 0 ? Gx : -Gx) < 0.15)
			{
				UE_LOG(LogSlap, Log, TEXT("walk-in: %s at the box after %.1f s"), *A.Name, A.WalkT * 0.02);
				A.bArrived = true;
				BeginSettle(m, d, A, false);	// the last few cm onto the stand pose; standing on the walking brain dropped Kim
				continue;
			}
			float Value = 0.f;
			{	// the walking brain to the box and then standing in it (command 0, as S2 trains) until the first turn's reset:
				// a match brain taking over mid-stride dropped Kim while she waited for Grandpa (2026-10-29)
				mjtNum R[9];
				mju_quat2Mat(R, d->qpos + A.RootQ + 3);
				const double DYaw = std::atan2(Gy, Gx) - std::atan2(R[3], R[0]);
				// locomotion.py _command: full speed, slowing over the last 0.8 m, 0 inside 0.15 m (along the aisle)
				const double Rem = a == 0 ? Gx : -Gx;
				const double V = Rem < 0.15 ? 0.0 : A.WalkSpeed * FMath::Clamp(Rem / 0.8, 0.0, 1.0);
				const float Cmd[6] = { (float)(V * std::cos(DYaw)), (float)(V * std::sin(DYaw)), 0.f,
					(float)std::sin(2.0 * PI * A.WalkPhase), (float)std::cos(2.0 * PI * A.WalkPhase), (float)(V / 3.0) };
				BuildObs(m, d, A, 0.f, 0.f, nullptr, Obs, Cmd);
				Obs[3 * A.Act.Num() + 11] = (float)(1.0 - A.WalkT / 1000.0);	// locomotion.py feeds its episode clock at role 0 (BuildObs O[11 + N])
				if (!RunNet(*A.WalkNet, A.WalkIn, A.WalkOut, Obs, Value, A.WalkAction)) continue;
				A.WalkPhase = V > 0.1 ? FMath::Fmod(A.WalkPhase + 1.8 * 0.02, 1.0) : 0.0;	// locomotion.py: the clock rests at 0 below 0.1 m/s
				++A.WalkT;
			}
			for (int32 j = 0; j < A.Act.Num(); ++j)	// Decide's mapping and rate limit; the walking brain trained with an 0.85 EMA on top
			{
				const double Step = A.MaxStep[j] * A.Stamina;
				const double Act = FMath::Clamp((double)A.WalkAction[j], -1.0, 1.0);
				double Raw = Target(A.Stand[j], A.Lo[j], A.Hi[j], Act);
				if (A.WalkIn >= LpfFromObs) Raw = LowPass(Raw, A.Goal[j]);	// interface 4 walking brains: the 8 Hz low-pass first
				const double Tgt = FMath::Clamp(Raw, A.Goal[j] - Step, A.Goal[j] + Step);
				A.Goal[j] = 0.85 * Tgt + 0.15 * A.Goal[j];
				A.PrevAction[j] = (float)Act;
			}
			continue;
		}
		bDone &= Prog >= 1.0;
		const double Sign = a == 0 ? -1.0 : 1.0;
		d->qpos[A.RootQ] = m->qpos0[A.RootQ] + Sign * Offset;
		d->qpos[A.RootQ + 1] = m->qpos0[A.RootQ + 1];
		d->qpos[A.RootQ + 2] = m->qpos0[A.RootQ + 2] + 0.015 * Env * FMath::Abs(FMath::Sin(Phase));
		for (int32 k = 3; k < 7; ++k) d->qpos[A.RootQ + k] = m->qpos0[A.RootQ + k];
		for (int32 k = 0; k < 6; ++k) d->qvel[A.RootV + k] = 0.0;
		if (bRunNet && A.Net)
		{
			BuildObs(m, d, A, 0.f, 0.f, nullptr, Obs);
			float Value = 0.f;
			if (Infer(A, Obs, Value))
			{
				for (int32 j = 0; j < A.Act.Num(); ++j)
				{
					const double Act = FMath::Clamp((double)A.Action[j], -1.0, 1.0);
					const double Tgt = Target(A.Stand[j], A.Lo[j], A.Hi[j], Act, 0.25);
					d->qpos[A.Qadr[j]] = Tgt;
					A.Goal[j] = Tgt;
					A.PrevAction[j] = (float)Act;
				}
			}
		}
		const double SinP = FMath::Sin(Phase + (a == 1 ? PI * 0.5 : 0.0));
		const double CosP = FMath::Cos(Phase + (a == 1 ? PI * 0.5 : 0.0));
		auto SetJ = [&](int32 Idx, double Delta)
		{
			if (Idx < 0) return;
			const double Val = FMath::Clamp(A.Stand[Idx] + Env * Delta, A.Lo[Idx], A.Hi[Idx]);
			d->qpos[A.Qadr[Idx]] = Val;
			A.Goal[Idx] = Val;
		};
		SetJ(A.HipL, 0.38 * SinP);
		SetJ(A.HipR, -0.38 * SinP);
		SetJ(A.KneeL, 0.55 * FMath::Max(0.0, SinP));
		SetJ(A.KneeR, 0.55 * FMath::Max(0.0, -SinP));
		SetJ(A.AnkleL, 0.18 * CosP);
		SetJ(A.AnkleR, -0.18 * CosP);
		SetJ(A.ShSwingL, -0.30 * SinP);
		SetJ(A.ShSwingR, 0.30 * SinP);
	}
	if (bPlaced || !(Athletes[0].WalkNet && Athletes[1].WalkNet)) mj_forward(m, d);	// a scripted or settling athlete was placed
	if (bReady)
		for (FAthlete& A : Athletes)
		{
			if (!A.WalkNet) continue;
			A.WalkHold.SetNumUninitialized(7 + A.Act.Num());
			for (int32 k = 0; k < 7; ++k) A.WalkHold[k] = d->qpos[A.RootQ + k];
			for (int32 j = 0; j < A.Act.Num(); ++j) A.WalkHold[7 + j] = d->qpos[A.Qadr[j]];
		}
	if (bReady && !bDone && WalkInLeft <= 2) WalkInLeft = 3;	// a settle still running at the timeout: wait for it
	double Walked = 1.0;	// the HUD's progress: the slower of the two
	for (const FAthlete& A : Athletes)
	{
		const double Left = FMath::Abs(m->qpos0[A.RootQ] - d->xpos[3 * A.Pelvis]);
		Walked = FMath::Min(Walked, A.WalkNet ? (A.bArrived || A.bFell ? 1.0 : FMath::Clamp(1.0 - Left / Aisle, 0.0, 1.0)) : Prog);
	}
	if (bDone && bReady) WalkInLeft = 1;	// StepPolicy's decrement ends it: the first turn starts on the next step
	SetHud([&](FSlapHud& H)
	{
		H.bWalkIn = true;
		H.WalkInProgress = Walked;
		if (!Note.IsEmpty()) H.Event = Note;
		HudBalance(m, d, H);
	});
}

