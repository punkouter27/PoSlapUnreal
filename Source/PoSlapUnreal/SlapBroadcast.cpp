// Broadcast science, display only (nothing here feeds back into the match): balance and KO danger, the live
// telemetry, the head impact (HIC15 / BrIC) and the kinetic chain.

#include "SlapController.h"

#include "SlapControllerInternal.h"

using namespace SlapCtl;

void USlapController::HudBalance(const mjModel* m, mjData* d, FSlapHud& H)
{
	mj_subtreeVel(m, d);	// centre-of-mass velocities (sensor outputs only: the dynamics never read them)
	H.Pose.Reset();
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		const FAthlete& X = Athletes[a];
		H.Com[a] = MjUtils::MjToUEPosition(d->subtree_com + 3 * X.Pelvis);
		H.Feet[a] = 0.5 * (MjUtils::MjToUEPosition(d->geom_xpos + 3 * X.Feet[0]) + MjUtils::MjToUEPosition(d->geom_xpos + 3 * X.Feet[1]));
		double Cp[2];
		H.Danger[a] = DangerNow[a] = FMath::Clamp(Wobble(m, d, X, Cp) / 0.15, 0.0, 1.0);
		const double Cp3[3] = { Cp[0], Cp[1], 0.0 };
		H.Capture[a] = MjUtils::MjToUEPosition(Cp3);
		for (int32 k = 0; k < 7; ++k) H.Pose.Add((float)d->qpos[X.RootQ + k]);
		for (const int32 Q : X.Qadr) H.Pose.Add((float)d->qpos[Q]);
	}
}

double USlapController::Wobble(const mjModel* m, const mjData* d, const FAthlete& A, double (&Cp)[2]) const
{
	// KO danger (display only): the capture point - where the centre of mass would come to rest over a step, x + v / w0
	// with w0 = sqrt(g / height) - against the support polygon of the planted soles. Outside it, standing still is no
	// longer possible: he has to step or fall.
	const double* C = d->subtree_com + 3 * A.Pelvis;
	const double* V = d->subtree_linvel + 3 * A.Pelvis;
	const double W0 = std::sqrt(-m->opt.gravity[2] / FMath::Max(C[2], 0.2));
	Cp[0] = C[0] + V[0] / W0;
	Cp[1] = C[1] + V[1] / W0;
	TArray<FVector2D, TInlineAllocator<8>> P;	// the bottom corners of every planted sole box
	for (const int32 G : A.Feet)
	{
		const double* X = d->geom_xpos + 3 * G;
		const double* R = d->geom_xmat + 9 * G;
		const double* Sz = m->geom_size + 3 * G;
		bool bPlanted = false;	// a floor contact on this sole (a height test misses tall foot boxes)
		for (int32 c = 0; c < d->ncon && !bPlanted; ++c) bPlanted = (d->contact[c].geom[0] == G || d->contact[c].geom[1] == G) && (d->contact[c].geom[0] == Floor || d->contact[c].geom[1] == Floor);
		if (!bPlanted) continue;
		for (int32 k = 0; k < 4; ++k)
		{
			const double Lx = (k & 1) ? Sz[0] : -Sz[0], Ly = (k & 2) ? Sz[1] : -Sz[1];
			P.Emplace(X[0] + R[0] * Lx + R[1] * Ly, X[1] + R[3] * Lx + R[4] * Ly);
		}
	}
	if (P.Num() < 4) return 1.0;	// both feet off the floor
	// convex hull (Andrew's monotone chain), counter-clockwise
	P.Sort([](const FVector2D& L, const FVector2D& R) { return L.X < R.X || (L.X == R.X && L.Y < R.Y); });
	auto Cross = [](const FVector2D& O, const FVector2D& A2, const FVector2D& B) { return (A2.X - O.X) * (B.Y - O.Y) - (A2.Y - O.Y) * (B.X - O.X); };
	TArray<FVector2D, TInlineAllocator<16>> Hull;
	for (int32 Pass = 0; Pass < 2; ++Pass)
	{
		const int32 Start = Hull.Num();
		for (int32 i = 0; i < P.Num(); ++i)
		{
			const FVector2D& Q = P[Pass ? P.Num() - 1 - i : i];
			while (Hull.Num() >= Start + 2 && Cross(Hull[Hull.Num() - 2], Hull.Last(), Q) <= 0.0) Hull.Pop(EAllowShrinking::No);
			Hull.Add(Q);
		}
		Hull.Pop(EAllowShrinking::No);
	}
	const FVector2D X(Cp[0], Cp[1]);
	bool bInside = true;
	double Gap = TNumericLimits<double>::Max();
	for (int32 i = 0; i < Hull.Num(); ++i)
	{
		const FVector2D& E0 = Hull[i];
		const FVector2D& E1 = Hull[(i + 1) % Hull.Num()];
		bInside &= Cross(E0, E1, X) >= 0.0;
		const FVector2D E = E1 - E0;
		const double T = FMath::Clamp(FVector2D::DotProduct(X - E0, E) / FMath::Max(E.SizeSquared(), 1e-12), 0.0, 1.0);
		Gap = FMath::Min(Gap, FVector2D::Distance(X, E0 + T * E));
	}
	return bInside ? 0.0 : Gap;
}

void USlapController::Telemetry(const mjModel* m, const mjData* d, double HandSpeed)
{
	// every brain step (20 ms) of a turn: the defender's head acceleration, the striker's palm speed. Over 5 ms steps a
	// hard contact differentiates into thousands of g; the 20 ms average is what a mouthguard sensor would report.
	if (Turn.Step % ControlEvery) return;
	const double* Head = d->geom_xpos + 3 * Athletes[1 - Turn.Striker].Head;
	const double Dt = m->opt.timestep * ControlEvery;
	const double Vel[3] = { (Head[0] - TelHeadPrev[0]) / Dt, (Head[1] - TelHeadPrev[1]) / Dt, (Head[2] - TelHeadPrev[2]) / Dt };
	if (bTelReady) TelHeadG = Dist(Vel, TelHeadVel, 3) / Dt / 9.81;
	bTelReady = true;
	FMemory::Memcpy(TelHeadPrev, Head, sizeof(TelHeadPrev));
	FMemory::Memcpy(TelHeadVel, Vel, sizeof(TelHeadVel));
	const FAthlete& S = Athletes[Turn.Striker];
	const double Shoulder = S.ShSwingR >= 0 ? FMath::Abs(d->qvel[S.Vadr[S.ShSwingR]]) : 0.0;	// the wind-up's first link (the HUD's AI read)
	SetHud([&](FSlapHud& H) { H.HandSpeed = HandSpeed; H.ShoulderSpeed = Shoulder; });
}

void USlapController::Impact(const mjModel* m, const mjData* d)
{
	// The defender's head as a crash-test dummy's (display and the "impact" log line only). HIC15: the worst window of up
	// to 15 ms of (t2 - t1) * mean(a)^2.5, a in g (700 = the US crash-test limit, FMVSS 208). BrIC: his head's peak spin
	// about his own axes against NHTSA's critical rates (66.25, 56.45, 42.87 rad/s); 1.0 = critical.
	const FAthlete& D = Athletes[1 - Turn.Striker];
	mjtNum V[6];
	mj_objectVelocity(m, d, mjOBJ_BODY, D.HeadBody, V, 0);	// angular then linear, world axes, at the head's centre of mass
	const double Dt = m->opt.timestep;
	if (Turn.HeadSamples >= 0 && Turn.TouchStep >= 0)
	{
		Turn.HeadAcc[2] = Turn.HeadAcc[1];
		Turn.HeadAcc[1] = Turn.HeadAcc[0];
		Turn.HeadAcc[0] = Dist(V + 3, Turn.HeadVel, 3) / Dt / 9.81;
		Turn.HeadSamples = FMath::Min(Turn.HeadSamples + 1, 3);
		double Sum = 0.0;
		for (int32 n = 1; n <= Turn.HeadSamples; ++n)	// windows of 5, 10, 15 ms ending now
		{
			Sum += Turn.HeadAcc[n - 1];
			Turn.Hic = FMath::Max(Turn.Hic, n * Dt * FMath::Pow(Sum / n, 2.5));
		}
		mjtNum R[9];
		mju_quat2Mat(R, d->qpos + D.RootQ + 3);
		const double Yaw = std::atan2(R[3], R[0]), C = std::cos(Yaw), S = std::sin(Yaw);
		const double W[3] = { C * V[0] + S * V[1], -S * V[0] + C * V[1], V[2] };	// his heading frame: x forward, y left, z up
		constexpr double Crit[3] = { 66.25, 56.45, 42.87 };
		double B = 0.0;
		for (int32 k = 0; k < 3; ++k)
		{
			Turn.Spin[k] = FMath::Max(Turn.Spin[k], FMath::Abs(W[k]));
			B += FMath::Square(Turn.Spin[k] / Crit[k]);
		}
		Turn.Bric = FMath::Sqrt(B);
	}
	FMemory::Memcpy(Turn.HeadVel, V + 3, sizeof(Turn.HeadVel));
	Turn.HeadSamples = FMath::Max(Turn.HeadSamples, 0);
}

void USlapController::ChainSample(const mjData* d, double PalmSpeed)
{
	const FAthlete& S = Athletes[Turn.Striker];
	auto Joint = [&](int32 j) { return j >= 0 ? (float)FMath::Abs(d->qvel[S.Vadr[j]]) : 0.f; };
	float* Row = ChainBuf[ChainAt];
	Row[0] = (float)FMath::Abs(d->qvel[S.RootV + 5]);	// pelvis yaw rate (the root's angular velocity, its own z)
	Row[1] = Joint(S.WaistTwist);
	Row[2] = Joint(S.ShSwingR);
	Row[3] = Joint(S.ElbowR);
	Row[4] = (float)PalmSpeed;
	ChainAt = (ChainAt + 1) % UE_ARRAY_COUNT(ChainBuf);
	ChainNum = FMath::Min(ChainNum + 1, (int32)UE_ARRAY_COUNT(ChainBuf));
}

void USlapController::ChainAtTouch(double Dt)
{
	// kinetic chain: when each link peaked over the 0.6 s before the touch. A whip runs proximal to distal (hips, trunk,
	// shoulder, elbow, palm), each link peaking a little after the one before it
	const int32 Len = UE_ARRAY_COUNT(ChainBuf);
	for (int32 k = 0; k < 5; ++k)
	{
		int32 Best = 0;
		float Peak = -1.f;
		for (int32 i = 0; i < ChainNum; ++i)	// i steps back from the newest sample
		{
			const float V = ChainBuf[(ChainAt - 1 - i + Len) % Len][k];
			if (V > Peak) { Peak = V; Best = i; }
		}
		Turn.Chain[k] = -Best * Dt;
	}
	Turn.ChainOrder = 0;
	for (int32 k = 0; k < 4; ++k) Turn.ChainOrder += Turn.Chain[k] <= Turn.Chain[k + 1];
}

void USlapController::PublishFootsteps(const mjData* d)
{
	// Sole contacts (display only): a footstep when a sole touches the floor, straight from MuJoCo's own contact list,
	// not from a timer. The rising edge of each sole's contact is the step.
	for (int32 a = 0; a < 2; ++a)
	{
		FAthlete& A = Athletes[a];
		bool Touched[2] = { false, false };
		FVector Pos[2];
		for (int32 c = 0; c < d->ncon; ++c)
		{
			const int32 G = OnGround(d->contact[c], Floor, Podium);
			for (int32 f = 0; f < 2 && G >= 0; ++f)
			{
				if (A.Feet.Num() > f && A.Feet[f] == G) { Touched[f] = true; Pos[f] = MjUtils::MjToUEPosition(d->contact[c].pos); }
			}
		}
		for (int32 f = 0; f < 2; ++f)
		{
			if (Touched[f] && !A.bFootDown[f])
			{
				SetHud([&](FSlapHud& H) { H.StepPos[a] = Pos[f]; ++H.StepSeq[a]; });
			}
			A.bFootDown[f] = Touched[f];
		}
	}
}
