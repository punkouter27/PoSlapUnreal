// Fatigue, neck damage and fainting (card stamina_per_strike, neck_damage_per_snap, faint_*; match.py finish / faint_step).

#include "SlapController.h"

#include "SlapControllerInternal.h"

using namespace SlapCtl;

void USlapController::SetForce(mjModel* m, const FAthlete& A, double Gain) const
{
	for (int32 j = 0; j < A.Act.Num(); ++j)
	{
		m->actuator_forcerange[2 * A.Act[j]] = A.FrAll[2 * j] * Gain;
		m->actuator_forcerange[2 * A.Act[j] + 1] = A.FrAll[2 * j + 1] * Gain;
	}
	if (Gain != 1.0) return;	// out cold: the neck as slack as the rest
	for (int32 k = 0; k < A.NeckAct.Num(); ++k)
	{
		m->actuator_forcerange[2 * A.NeckAct[k]] = A.Fr0[2 * k] * A.NeckStrength;
		m->actuator_forcerange[2 * A.NeckAct[k] + 1] = A.Fr0[2 * k + 1] * A.NeckStrength;
	}
}

void USlapController::Faint(mjModel* m)
{
	// Fainting (card faint_chance_per_turn; user 2026-10-26: Matt "passes out randomly on rare occasion"): every motor at
	// faint_strength of its force for faint_seconds, then back; the referee calls whatever follows, as in match.py
	if (!bInTurn) return;
	for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
	{
		const FAthlete& A = Athletes[a];
		if (Turn.Step == Turn.FaintAt[a])
		{
			SetForce(m, A, A.FaintStrength);
			Emit(FString::Printf(TEXT("{\"type\": \"faint\", \"t\": %s, \"athlete\": %s}"), *Num(Turn.Step * m->opt.timestep, 3), *Str(A.Name)));
			UE_LOG(LogSlap, Log, TEXT("t=%.3f %s passes out"), Turn.Step * m->opt.timestep, *A.Name);
			const FString Call = A.Name.ToUpper() + TEXT(" PASSES OUT!");
			SetHud([&](FSlapHud& H) { H.Event = Call; });
		}
		else if (Turn.Step == Turn.FaintEnd[a]) SetForce(m, A, 1.0);
	}
}

void USlapController::Stun(mjModel* m)
{
	// Rules 2 (match.py turn(), rules.json): the defender's head speed peak over the window after the touch against
	// stun_speed x his card's chin. At or above it every motor of his drops to stun_strength of its force for
	// stun_s x severity seconds (severity at most stun_max), then comes back with the neck's damage on top.
	if (!bRules2 || !bInTurn || Turn.TouchStep < 0 || Athletes.Num() < 2) return;
	const FAthlete& D = Athletes[1 - Turn.Striker];
	const double Dt = m->opt.timestep;
	if (Turn.Step - Turn.TouchStep == (int64)std::nearbyint(SlapGen::StunWindowS / Dt))
	{
		double Sev = Turn.HeadPeak / (SlapGen::StunSpeed * D.Chin);
		if (Sev >= 1.0)
		{
			Sev = FMath::Min(Sev, SlapGen::StunMax);
			Turn.Stun = RoundTo(Sev, 2);
			Turn.StunEnd = Turn.Step + (int64)(SlapGen::StunS * Sev / Dt + 0.5);
			SetForce(m, D, SlapGen::StunStrength);
			Emit(FString::Printf(TEXT("{\"type\": \"stun\", \"t\": %s, \"athlete\": %s, \"severity\": %s}"), *Num(Turn.Step * Dt, 3), *Str(D.Name), *Num(Turn.Stun, 2)));
			UE_LOG(LogSlap, Log, TEXT("t=%.3f %s is stunned (severity %.2f)"), Turn.Step * Dt, *D.Name, Turn.Stun);
			SetHud([&](FSlapHud& H) { H.Event = TEXT("STUNNED"); });
		}
	}
	else if (Turn.Step == Turn.StunEnd) SetForce(m, D, 1.0);
}

void USlapController::ApplyFatigue(const mjModel* m, FAthlete& S, FAthlete& D) const
{
	// match.py finish(): the striker tires (joint speed x stamina, floor MinStamina); a struck defender remembers the
	// strike's timing and speed (inputs 126-127) and loses neck strength by the card's damage x the head snap
	// (forcerange x neck, floor MinNeck), unless he is out cold. Same order, same arithmetic as the reference.
	S.Stamina = FMath::Max(MinStamina, S.Stamina - S.StaminaPerStrike);
	if (Turn.TouchStep >= 0)
	{
		D.Mem[0] = RoundTo(Turn.TouchStep * m->opt.timestep, 3) / StrikeClock;
		D.Mem[1] = Turn.Speed / 10.0;
		if (!Turn.bKO) D.NeckStrength = FMath::Max(MinNeck, D.NeckStrength - D.NeckDamage * Turn.HeadPeak);
	}
}
