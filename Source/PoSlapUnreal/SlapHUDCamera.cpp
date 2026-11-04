// ASlapHUD: the director. Render side only: the camera never touches the physics, the brains or the timing.
//
// AUTO cuts on what the match does: WIDE frames both athletes head to toe; SIDE (a two-shot from the striker's side) when
// the AI read sees a wind-up; TENSION (the defender's face) as the palm closes in; IMPACT (0.6 s tight on the contact) at
// the touch, then STRIKE; KO cranes up and around; REPLAY orbits the replayed head. Escalations cut in at once, the way
// back waits 1 s. A pinned mode (the VIEW tab, the C key, -SlapCam=) holds WIDE, SIDE or CLOSE; replays still show.

#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "SlapHUDInternal.h"

using namespace SlapUi;

namespace
{
	constexpr double ImpactShot = 0.6, StrikeShot = 1.6;	// s: the impact close-up, then the strike medium
	constexpr double ReadCut = 0.35;	// the AI read's score that cuts to the SIDE two-shot
	constexpr double HoldBack = 1.0;	// s before a calmer shot may replace an escalation
}

void ASlapHUD::SetCam(ECam Mode)
{
	if (CamMode == Mode) return;
	CamMode = Mode;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: camera %s"), CamName[(int32)Mode]);
}

ASlapHUD::EShot ASlapHUD::Pick(const FSlapHud& H, double Now) const
{
	if (Now < ReplayEnd) return EShot::Replay;
	if (H.bWalkIn) return EShot::WalkIn;
	switch (CamMode)
	{
	case ECam::Wide: return EShot::Wide;
	case ECam::Side: return EShot::Side;
	case ECam::Close: return EShot::Tension;
	default: break;
	}
	const bool bInPlay = H.Match > 0 && H.Result.IsEmpty();
	if (bInPlay && StrikeAt > 0.0 && Now - StrikeAt < ImpactShot) return EShot::Impact;
	if (Now - KOAt < KOShot) return EShot::KO;
	if (Now - StrikeAt < StrikeShot) return EShot::Strike;
	if (bInPlay && !H.bStruck && H.HandDist < 0.55 && H.HandSpeed > 1.5) return EShot::Tension;	// the palm closing in
	if (bInPlay && !H.bStruck && (Read.bReading || Read.Score > ReadCut)) return EShot::Side;		// the wind-up
	return EShot::Wide;
}

void ASlapHUD::Direct(ACameraActor* Cam, const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	if (!bHaveBase)
	{
		BaseRot = Cam->GetActorRotation();
		BaseSat = Cam->GetCameraComponent()->PostProcessSettings.ColorSaturation;
		BaseVignette = Cam->GetCameraComponent()->PostProcessSettings.VignetteIntensity;
		bHaveBase = true;
	}
	FBox Box(ForceInit);
	FVector Head[2] = {}, Pelvis[2] = {};
	for (int32 a = 0; a < 2; ++a)
	{
		if (!Body[a].IsValid()) continue;
		Head[a] = Body[a]->GetBoneLocation(BoneHead);
		Pelvis[a] = Body[a]->GetBoneLocation(BonePelvis);
		for (const FVector& P : { Head[a], Pelvis[a], Body[a]->GetBoneLocation(BoneFootL), Body[a]->GetBoneLocation(BoneFootR) })
		{
			if (!P.IsZero()) Box += P;
		}
	}
	if (!Box.IsValid) return;

	const EShot Want = Pick(H, Now);
	bool bCut = false;
	if (Want != Shot)
	{
		if (Want == EShot::Impact)
		{
			bCut = true;
			Shot = Want;
			ShotStart = Now;
		}
		else if (Shot == EShot::Impact)
		{
			Shot = Want;	// ease out of the close-up (no cut)
			ShotStart = Now - HoldBack;
		}
		else if (CamMode != ECam::Auto || Want > Shot || Want == EShot::WalkIn || Now - ShotStart > HoldBack)
		{
			bCut = Want == EShot::WalkIn || Want == EShot::Side || Want == EShot::Tension || Want == EShot::KO || Want == EShot::Replay || CamMode != ECam::Auto;
			Shot = Want;
			ShotStart = Now;
		}
	}

	const UCameraComponent* CC = Cam->GetCameraComponent();
	const float Aspect = FMath::Max(CC->AspectRatio, 0.1f);
	const float HalfV = FMath::Atan(FMath::Tan(FMath::DegreesToRadians(CC->FieldOfView) * 0.5f) / Aspect);
	const FVector Fwd = BaseRot.Vector(), Up = FVector::UpVector;
	const int32 Def = Team(H.Defender);
	FVector Pos, Look;
	float Speed = 3.f;
	switch (Shot)
	{
	case EShot::WalkIn:		// ring entrance dolly: angled low-hero sweep following both athletes walking to the podium
	{
		const float Wp = FMath::Clamp((float)H.WalkInProgress, 0.f, 1.f);
		const float YawSweep = 28.f * (1.f - Wp);
		Look = 0.5f * (Pelvis[0] + Pelvis[1]) + Up * 25.f;
		const float Apart = FVector::Dist2D(Pelvis[0], Pelvis[1]);	// brain walkers start 2.4 m down each aisle
		const float Back = FMath::Max(FMath::Lerp(340.f, 260.f, Wp), (0.5f * Apart + 70.f) / FMath::Tan(FMath::DegreesToRadians(CC->FieldOfView) * 0.5f));
		Pos = Look - Fwd.RotateAngleAxis(YawSweep, Up) * Back + Up * FMath::Lerp(-15.f, 20.f, Wp);
		Speed = 4.5f;
		break;
	}
	case EShot::Side:		// a two-shot from the striker's side, chest high: both heads, the swing arm toward the lens
	{
		Look = 0.5f * (Head[0] + Head[1]) - Up * 25.f;
		const float Apart = FVector::Dist2D(Head[0], Head[1]);
		const float Back = FMath::Max(230.f, (0.5f * Apart + 60.f) / FMath::Tan(FMath::DegreesToRadians(CC->FieldOfView) * 0.5f));
		Pos = Look - Fwd.RotateAngleAxis(Def == 1 ? -32.f : 32.f, Up) * Back + Up * 8.f;
		Speed = 5.f;
		break;
	}
	case EShot::Tension:	// the defender's face, close
		Look = Head[Def];
		Pos = Look - Fwd.RotateAngleAxis(Def == 1 ? 18.f : -18.f, Up) * 120.f + Up * 6.f;
		break;
	case EShot::Strike:		// the contact, medium, swung round toward the striker's back
		Look = StrikePos;
		Pos = Look - Fwd.RotateAngleAxis(Def == 1 ? -28.f : 28.f, Up) * 210.f + Up * 10.f;
		Speed = 10.f;
		break;
	case EShot::Impact:		// the palm-impact close-up, tight on the contact point
		Look = StrikePos;
		Pos = Look - Fwd.RotateAngleAxis(Def == 1 ? -16.f : 16.f, Up) * 58.f + Up * 3.f;
		Speed = 14.f;
		break;
	case EShot::KO:			// crane: up and around the fallen
	{
		const float T = Now - KOAt;
		Look = 0.5f * (Pelvis[0] + Pelvis[1]);
		Pos = Look - Fwd.RotateAngleAxis(12.f * T, Up) * 330.f + Up * (140.f + 45.f * T);
		Speed = 1.5f;
		break;
	}
	case EShot::Replay:		// close on the replayed defender's head, orbiting
	{
		const float T = Now - ShotStart;
		Look = Head[Struck];
		Pos = Look - Fwd.RotateAngleAxis((Struck == 1 ? -35.f : 35.f) + 20.f * T, Up) * 190.f + Up * 25.f;
		Speed = 6.f;
		break;
	}
	default:				// wide, side-on: both athletes framed (~60 % of the portrait height), below the HUD panel
	{
		const float Height = FMath::Max(Box.GetSize().Z, Box.GetSize().X / Aspect) + 40.f;
		const float Dist = Height * 0.5f / 0.55f / FMath::Tan(HalfV);
		Pos = Box.GetCenter() - Fwd * Dist + Up * (0.12f * 2.f * Dist * FMath::Tan(HalfV));
		Look = Pos + Fwd;
	}
	}
	const FRotator Rot = Shot == EShot::Wide ? BaseRot : (Look - Pos).Rotation();
	const float Dt = GetWorld()->GetDeltaSeconds();
	FVector L = bCut ? Pos : FMath::VInterpTo(Cam->GetActorLocation(), Pos, Dt, Speed);
	const FRotator R = bCut ? Rot : FMath::RInterpTo(Cam->GetActorRotation(), Rot, Dt, Speed * 1.5f);

	// impact: shake and colour fringe, scaled by the palm's speed and the mass behind it. The shake is dropped when the frame
	// budget is blown (a phone that cannot keep up would only get worse) and on the low tier
	UCameraComponent* Comp = Cam->GetCameraComponent();
	const double Ts = Now - StrikeAt;
	const float K = StrikeK * MassK;
	FPostProcessSettings& PP = Comp->PostProcessSettings;
	PP.bOverride_SceneFringeIntensity = true;
	PP.SceneFringeIntensity = Ts < 1.0 && TierLive > 0 ? 5.f * K * FMath::Exp(-6.0 * Ts) : 0.f;
	// KO grade: the picture drains and closes in while the crane runs
	const double Tk = Now - KOAt;
	const double W = Tk < KOShot ? FMath::Clamp(FMath::Min(Tk / 0.3, (KOShot - Tk) / 0.5), 0.0, 1.0) : 0.0;
	PP.bOverride_ColorSaturation = PP.bOverride_VignetteIntensity = true;
	PP.ColorSaturation = FMath::Lerp(BaseSat, FVector4(0.3, 0.3, 0.35, 1.0), W);
	PP.VignetteIntensity = FMath::Lerp(BaseVignette, 1.f, float(W));
	if (Ts < 0.6 && TierLive > 0)
	{
		const bool bOverBudget = Dt * 1000.f > 33.3f || WorstMs > 40.f || Fps < 30.f;	// the shake is the first thing to go
		if (!bOverBudget)
		{
			const float A = 3.f * K * FMath::Exp(-7.0 * Ts);
			L += FVector(FMath::PerlinNoise1D(Ts * 40.0), FMath::PerlinNoise1D(Ts * 40.0 + 17.0), FMath::PerlinNoise1D(Ts * 40.0 + 31.0)) * A;
		}
	}
	Cam->SetActorLocationAndRotation(L, R);
}
