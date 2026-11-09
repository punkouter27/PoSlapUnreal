// ASlapHUD's effects: the cues on a strike, a KO and a new match, the skin (ripple, flush, bruise, print, the cheek
// dent), the lights, the scuffs and the dust, the quality tiers and the burst capture. Display only.

#include "SlapHUD.h"

#include "Components/AudioComponent.h"
#include "Components/DecalComponent.h"
#include "Components/LightComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Components/SpotLightComponent.h"
#include "DeviceProfiles/DeviceProfileManager.h"
#include "Engine/Engine.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformMemory.h"
#include "HAL/PlatformMisc.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "MuJoCo/Core/AMjManager.h"
#include "MuJoCo/Core/MjPhysicsEngine.h"
#include "NiagaraFunctionLibrary.h"
#include "NiagaraSystem.h"
#include "Scalability.h"
#include "SlapAthleteVisual.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "Sound/SoundAttenuation.h"
#include "Sound/SoundBase.h"
#include "UnrealClient.h"

using namespace SlapUi;

namespace
{
	constexpr double DentRise = 0.03, DentFall = 0.45;	// s: the cheek dent's attack and its spring back
	constexpr float BruisePerStrike = 0.35f, BruiseFade = 60.f;	// the redness a clean slap adds; its fade (s)
	constexpr double DefenderRim = 1.6;	// the defender's rim through a turn (x its level intensity)
	constexpr float SlowFps = 30.f;		// AUTO steps a tier down after SlowFor s under this
	constexpr double SlowFor = 3.0;
	const bool bMobile = PLATFORM_ANDROID || PLATFORM_IOS;	// one dynamic light on a phone: the follow spot
}

void ASlapHUD::Cues(const FSlapHud& H)
{
	const double Now = GetWorld()->GetTimeSeconds();
	const float Dt = GetWorld()->GetDeltaSeconds();
	HeadG.Add(H.HeadG);
	if (HeadG.Num() > 120) HeadG.RemoveAt(0);
	if (Now - StrikeAt <= SnapSeconds) Snap.Emplace((float)(Now - StrikeAt), (float)H.HeadG);	// the sparkline after the touch

	// the swing read (SlapSwingRead.h): a new turn starts it over; one log line per read, feint and verdict
	const int32 TurnKey = H.Match * 16 + H.Round * 2 + Team(H.Striker);
	if (TurnKey != SeenTurnKey) { SeenTurnKey = TurnKey; Read.NewTurn(); }
	if (!H.bWalkIn)
	{
		switch (Read.Update(Now, Dt, H.HandSpeed, H.HandDist, H.ShoulderSpeed, H.bStruck))
		{
		case FSlapSwingRead::EEvent::Feint:
			if (CrowdNear) UGameplayStatics::PlaySound2D(this, CrowdNear, 0.7f, 1.05f);
			SLAP_HUD_LOG(1, TEXT("SlapHUD: FEINT: the palm peaked at %.1f m/s and pulled back"), Read.Peak);
			if (bPass) Ticker(FString::Printf(TEXT("FEINT: %s's palm peaked at %.1f m/s and pulled back"), *H.Striker.ToUpper(), Read.Peak), Gold);
			break;
		case FSlapSwingRead::EEvent::Read:
			SLAP_HUD_LOG(1, TEXT("SlapHUD: AI read: %s winds up (shoulder %.1f rad/s, palm %.1f m/s, %.0f cm out)"), *H.Striker, Read.PeakShoulder, H.HandSpeed, 100.0 * Read.PeakDist);
			break;
		case FSlapSwingRead::EEvent::ReadWrong:
			SLAP_HUD_LOG(1, TEXT("SlapHUD: AI read: no strike within %.1f s (reads %d of %d right)"), FSlapSwingRead::ReadWindow, Read.ReadsRight, Read.Reads);
			break;
		default: break;
		}
	}
	// the tell: the palm already fast and closing on the head
	const double Want = (!H.bWalkIn && !H.bStruck && H.HandDist < 0.6 && H.HandSpeed > 3.0 && H.HandSpeed > 4.0 * FMath::Max(H.HandDist, 0.05))
		? FMath::Clamp((0.6 - H.HandDist) / 0.5, 0.0, 1.0) * FMath::Clamp(H.HandSpeed / 8.0, 0.0, 1.0) : 0.0;
	Tell = FMath::FInterpTo(Tell, (float)Want, Dt, 10.f);

	if (H.StrikeSeq != SeenStrike) OnStrike(H, Now);
	if (ChainRevealAt >= 0.0 && Now >= ChainRevealAt)	// the kinetic chain at a quarter speed, from the recorded poses
	{
		ChainRevealAt = -1.0;
		for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
			if (V.IsValid()) V->Replay(Now - 0.45, Now, 0.25);
		ReplayEnd = Now + 0.45 / 0.25;
		bAutoReplay = false;
		SLAP_HUD_LOG(1, TEXT("SlapHUD: the kinetic chain at a quarter speed for %.1f s"), ReplayEnd - Now);
	}
	if (H.TurnSeq != SeenTurn)	// the referee called the turn: his verdict on the stagger is the last word
	{
		SeenTurn = H.TurnSeq;
		if (H.bStruck) bNearMiss = !H.bStagger;
		// the per-strike instant replay, in the referee's hold (a KO has its own, longer one; a replay already running stays)
		const bool bDue = bPass && bStrikeReplays && H.bStruck && StrikeAt > 0.0 && KOAt < StrikeAt && H.KOd < 0 && Now - StrikeAt < 7.5;
		ReplayDue += bDue;
		if (bDue && ReplayAt < 0.0 && Now >= ReplayEnd) StrikeReplay(Now);
	}
	if (SweatAt >= 0.0 && Now >= SweatAt && Body[Struck].IsValid())	// sweat off the far side, as hard as the head snapped
	{
		SweatAt = -1.0;
		const FVector Head = Body[Struck]->GetBoneLocation(BoneHead);
		const double SnapV = FVector::Dist(Head, SweatHead0) / 100.0 / FMath::Max(Now - StrikeAt, 0.01);	// m/s (clean slaps: 2-7)
		const FVector Off = (Head - StrikePos).GetSafeNormal();
		// ponytail: the spray system again, smaller; a droplet emitter of its own if it reads as spray
		if (Spray && TierLive > 0) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, Head + Off * 8.f, FRotationMatrix::MakeFromZ(Off + H.StrikeDir).Rotator(), FVector(0.15f * FMath::Clamp(SnapV / 5.0, 0.2, 1.5) * MassK));
	}
	if (RoarAt >= 0.0 && Now >= RoarAt)
	{
		RoarAt = -1.0;
		// The near miss: the slap landed, but the defender's centre of mass did not move 5 cm (the referee's own stagger
		// rule). The crowd groans ("ooooh") instead of roaring: a different reaction for a different outcome.
		bNearMiss = FVector::Dist2D(H.Com[Struck], StrikeCom0) < 5.0;
		if (bNearMiss)
		{
			if (CrowdNear) UGameplayStatics::PlaySound2D(this, CrowdNear, RoarVolume * 0.9f);
			else if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, RoarVolume * 0.6f, 0.85f);
		}
		else if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, RoarVolume, bPass ? FMath::Clamp(0.92f + 0.08f * (float)ImpulseK, 0.92f, 1.08f) : 1.f);
	}
	Wear(H, Now);
	Dent(Now);
	if (H.KOSeq != SeenKO) OnKO(Now);
	if (ReplayAt >= 0.0 && Now >= ReplayAt)	// the KO again at half speed: its last 1.5 s, inside the post-match hold
	{
		ReplayAt = -1.0;
		const double Dur = FMath::Max(1.0, KOShot - 1.0), Window = FMath::Min(1.5, 0.5 * Dur);
		for (const TWeakObjectPtr<ASlapAthleteVisual>& V : Visual)
			if (V.IsValid()) V->Replay(KOAt - Window, KOAt, Window / Dur);
		ReplayEnd = Now + Dur;
		bAutoReplay = false;
	}
	if (H.Round != SeenTurnRound || H.Striker != SeenTurnStriker)	// a new turn: the last turn's commentary is stale
	{
		SeenTurnRound = H.Round;
		SeenTurnStriker = H.Striker;
		TurnAt = Now;
	}
	if (H.MatchSeq != SeenMatch) OnMatch(H, Now);
	if (H.ScoredRound != SeenRound)
	{
		SeenRound = H.ScoredRound;
		if (H.ScoredRound > 0) CardAt = Now;
	}
	EmoteCues(H);
	Commentate(H, Now);
	PollSidecars(Now);
	Stage(H, Now);
}

void ASlapHUD::OnStrike(const FSlapHud& H, double Now)
{
	// the touch: spray along the palm's path, sound, ripple, print and dent on the struck cheek
	SeenStrike = H.StrikeSeq;
	StrikeAt = ImpactAt = Now;
	if (ShotAfterStrike >= 0.f && ShotAt < 0.f && bSceneReady) ShotAt = Now + ShotAfterStrike;	// the impact close-up and the 120 fps burst start here
	StrikePos = H.StrikePos;
	StrikeSpeed = H.Speed;
	Struck = FMath::Clamp(H.StrikeDefender, 0, 1);
	StrikeK = FMath::Clamp(H.Speed / CleanSpeed, 0.25, 1.5);
	const int32 Striker = 1 - Struck;
	++TotalStrikes[Striker];
	PeakSpeed[Striker] = FMath::Max(PeakSpeed[Striker], (double)H.Speed);
	Snap.Reset();
	Read.OnStrike(Now);	// a landed slap is REAL; a pending read was right
	// the impulse delivered: the card's strike mass x the palm's speed (N s; 3 kg at a clean 8 m/s = 24): the crowd answers it
	Impulse = FMath::Max(H.StrikeMass[Striker], 0.0) * H.Speed;
	ImpulseK = FMath::Clamp(Impulse / (3.0 * CleanSpeed), 0.2, 2.0);
	if (bPass)
	{
		if (H.Part.EndsWith(TEXT("_l"))) ++SideHits[Striker][0];
		else if (H.Part.EndsWith(TEXT("_r"))) ++SideHits[Striker][1];
		const FString Part = H.Part.Replace(TEXT("_r"), TEXT(" (R)")).Replace(TEXT("_l"), TEXT(" (L)")).ToUpper();
		Ticker(FString::Printf(TEXT("STRIKE: %s lands %.1f m/s with the %s, impulse %.0f N s%s"), *H.Striker.ToUpper(), H.Speed, *Part, Impulse,
			bFlinched ? TEXT(", on a flinch") : TEXT("")), H.Speed >= CleanSpeed ? Gold : FColor::White);
	}
	// The card's strike mass: the body behind the palm. A heavy athlete's slap lands harder, so the burst, the sound and the
	// shake all scale with it (the card's 3 kg is the reference).
	MassK = FMath::Clamp(FMath::Sqrt(FMath::Max(H.StrikeMass[Striker], 0.5) / 3.0), 0.6, 1.6);
	const float K = StrikeK * MassK;
	// the contact burst, sized by the palm's speed and the mass behind it; a low tier sprays on every other strike only
	if (Spray && (TierLive > 0 || (SeenStrike & 1)))
	{
		UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, H.StrikePos, FRotationMatrix::MakeFromZ(H.StrikeDir).Rotator(), FVector(0.3f * K));
		if (SprayMul > 1.f)	// cinema: a second, wider burst sized by the strike's energy (mass x speed^2: K squared)
			UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, H.StrikePos, FRotationMatrix::MakeFromZ(-H.StrikeDir).Rotator(), FVector(0.15f * K * K * SprayMul));
	}
	if (SlapTakes.Num())	// one of four takes, deeper behind a heavier arm, duller for a slow palm
	{
		const float Pitch = FMath::Clamp(FMath::Sqrt(3.0 / FMath::Max(H.StrikeMass[Striker], 0.5)), 0.8, 1.25) * FMath::FRandRange(0.94f, 1.06f);
		if (UAudioComponent* A = UGameplayStatics::SpawnSoundAtLocation(this, SlapTakes[FMath::RandHelper(SlapTakes.Num())], H.StrikePos, FRotator::ZeroRotator, FMath::Clamp(K, 0.25f, 1.5f), Pitch, 0.f, Ringside))
		{
			A->SetLowPassFilterEnabled(true);
			A->SetLowPassFilterFrequency(2500.f + 17500.f * FMath::Min(StrikeK, 1.0));
		}
	}
	// the synthesised layer (MetaSound): the skin's crack brighter for a faster palm, the body's thump lower behind a heavier
	// arm and longer for a bigger impulse; it rides the same spatialisation as the recorded take
	if (bPass && ImpactSynth)
	{
		if (UAudioComponent* A = UGameplayStatics::SpawnSoundAtLocation(this, ImpactSynth, H.StrikePos, FRotator::ZeroRotator, 1.f, 1.f, 0.f, Ringside))
		{
			A->SetFloatParameter(TEXT("Energy"), 0.35f * FMath::Min((float)ImpulseK, 1.8f));
			A->SetFloatParameter(TEXT("Pitch"), FMath::Clamp(170.f * FMath::Sqrt(3.f / FMath::Max((float)H.StrikeMass[Striker], 0.5f)), 110.f, 260.f));
			A->SetFloatParameter(TEXT("Bright"), 2500.f + 5000.f * FMath::Min((float)StrikeK, 1.3f));
			A->SetFloatParameter(TEXT("SkinDecay"), 0.05f + 0.03f * (float)StrikeK);
			A->SetFloatParameter(TEXT("BodyDecay"), 0.15f + 0.12f * FMath::Min((float)ImpulseK, 1.5f));
		}
	}
	// the striker's grunt as it lands, in his own voice (Web/audio.js strike: above 4 m/s, louder with the speed)
	if (H.Speed > 4.0 && Body[Striker].IsValid())
	{
		if (USoundBase* G = Voice(TEXT("Grunt"), H.Card[Striker]))
			UGameplayStatics::PlaySoundAtLocation(this, G, Body[Striker]->GetBoneLocation(BoneHead), 0.25f + 0.25f * FMath::Min(1.f, (float)H.Speed / 10.f), FMath::FRandRange(0.97f, 1.03f), 0.f, Ringside);
	}
	if (Body[Struck].IsValid())
	{
		const FTransform HeadT = Body[Struck]->GetBoneTransformByName(BoneHead, EBoneSpaces::WorldSpace);
		FlushLocal[Struck] = HeadT.InverseTransformPosition(H.StrikePos);
		const FVector N = (H.StrikePos - HeadT.GetLocation()).GetSafeNormal();	// the print: fingers along the palm's travel, flat on the cheek
		const FVector U = (H.StrikeDir - N * (H.StrikeDir | N)).GetSafeNormal();
		PrintU[Struck] = HeadT.InverseTransformVectorNoScale(U);
		PrintV[Struck] = HeadT.InverseTransformVectorNoScale(N ^ U);
		Print[Struck] = FMath::Min(1.f, Print[Struck] + 0.4f * K);
		Bruise[Struck] = FMath::Min(1.f, Bruise[Struck] + BruisePerStrike * K);
		SweatHead0 = HeadT.GetLocation();
		SweatAt = Now + 0.08;
		// the cheek dent: the side facing the slap (the struck athlete's own left or right, from where he faces)
		if (bDentMorphs[Struck] && Body[Striker].IsValid())
		{
			const FVector Fwd = (Body[Striker]->GetBoneLocation(BoneHead) - HeadT.GetLocation()).GetSafeNormal2D();
			const FVector Right = FVector::UpVector ^ Fwd;	// Unreal is left-handed: up x forward = right
			DentSide[Struck] = ((H.StrikePos - HeadT.GetLocation()) | Right) > 0.f ? 1 : 0;
			DentAmount[Struck] = FMath::Clamp(K, 0.2f, 1.f);
			DentAt[Struck] = Now;
		}
	}
	// The near-miss test: the defender's centre of mass at the touch; 0.35 s later the crowd roars or groans
	StrikeCom0 = H.Com[Struck];
	bNearMiss = false;
	RoarAt = Now + 0.35;
	RoarVolume = bPass ? 0.2f + 0.55f * FMath::Min((float)ImpulseK, 1.8f) : 0.3f + 0.5f * K;	// the roar follows the impulse delivered
	if (Skin.IsValidIndex(Struck) && Skin[Struck])
	{
		Skin[Struck]->SetVectorParameterValue(PRipplePoint, FLinearColor(H.StrikePos));
		Skin[Struck]->SetScalarParameterValue(PRippleAmp, 0.2f * H.Speed);	// cm: 1.6 at a clean 8 m/s
		Skin[Struck]->SetVectorParameterValue(PRippleDir, FLinearColor(H.StrikeDir));	// the cheek is pushed along the slap
	}
	// a good strike (clean speed, the chain ran hips to hand) is revealed at a quarter speed from the recorded poses
	// (the display pass replays every landed strike at the turn's end instead: StrikeReplay)
	if (!bPass && H.Speed >= CleanSpeed && H.ChainOrder >= 3 && ReplayEnd < Now && ReplayAt < 0.0) ChainRevealAt = Now + 0.7;
}

void ASlapHUD::OnKO(double Now)
{
	SeenKO = Last.KOSeq;
	KOAt = Now;
	if (Bell) UGameplayStatics::PlaySound2D(this, Bell);
	if (CrowdReact) UGameplayStatics::PlaySound2D(this, CrowdReact, 1.4f, 0.9f);
	if (Spray) UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), Spray, StrikePos, FRotator(90, 0, 0), FVector(0.6f));
	if (Thud && Body[Struck].IsValid()) UGameplayStatics::PlaySoundAtLocation(this, Thud, Body[Struck]->GetBoneLocation(BonePelvis), 1.2f, 0.9f, 0.f, Ringside);
	ReplayAt = Now + 0.8;
}

void ASlapHUD::OnMatch(const FSlapHud& H, double Now)
{
	SeenMatch = H.MatchSeq;
	TapeAt = Now;
	if (PAChime) UGameplayStatics::PlaySound2D(this, PAChime, 0.55f);	// the PA before the bout (it carries its own hall)
	HeadG.Reset();
	Snap.Reset();
	LoadLeague();	// training/scouting.py may have run since
	for (int32 a = 0; a < 2; ++a)	// a fresh match: clean cheeks, a clean floor
	{
		Print[a] = Bruise[a] = 0.f;
		DentAt[a] = -100.0;
		TotalStrikes[a] = 0;
		PeakSpeed[a] = 0.0;
	}
	for (const TWeakObjectPtr<UDecalComponent>& M : Marks)
		if (M.IsValid()) M->DestroyComponent();
	Marks.Reset();
	DownAt = -100.0;
	CoinTossWinner = H.Striker;
	Read.Reset();
	SeenTurnKey = -1;
}

void ASlapHUD::Wear(const FSlapHud& H, double Now)
{
	// the flush, the bruise and the print follow the head; 1 at the neck-strength and stamina floors
	const float Dt = GetWorld()->GetDeltaSeconds();
	for (int32 a = 0; a < Skin.Num(); ++a)
	{
		if (!Skin[a]) continue;
		Skin[a]->SetScalarParameterValue(PRippleTime, a == Struck ? Now - StrikeAt : 100.f);
		if (Body[a].IsValid())
		{
			const FTransform HeadT = Body[a]->GetBoneTransformByName(BoneHead, EBoneSpaces::WorldSpace);
			Skin[a]->SetVectorParameterValue(PFlushPoint, FLinearColor(HeadT.TransformPosition(FlushLocal[a])));
			Skin[a]->SetVectorParameterValue(PPrintU, FLinearColor(HeadT.TransformVectorNoScale(PrintU[a])));
			Skin[a]->SetVectorParameterValue(PPrintV, FLinearColor(HeadT.TransformVectorNoScale(PrintV[a])));
		}
		Print[a] *= FMath::Exp(-Dt / 25.f);
		Bruise[a] *= FMath::Exp(-Dt / BruiseFade);
		Skin[a]->SetScalarParameterValue(PPrint, Print[a]);
		// the redness builds with the neck damage (the card's neck_damage_per_snap x each head snap, from the controller) and
		// with a bruise from every slap, plus the sting of the last one
		const double Sting = a == Struck ? 0.5 * FMath::Clamp(StrikeSpeed / CleanSpeed, 0.0, 1.5) * FMath::Exp((StrikeAt - Now) / 2.0) : 0.0;
		Skin[a]->SetScalarParameterValue(PFlush, FMath::Clamp(1.0 - NeckLeft(H.Neck[a]) + 0.5 * Bruise[a] + Sting, 0.0, 1.0));
		// the whole cheek (the assets stream's cheek mask x CheekFlush): the same damage, a little calmer, over the mask
		Skin[a]->SetScalarParameterValue(PCheekFlush, FMath::Clamp(0.8 * (1.0 - NeckLeft(H.Neck[a])) + 0.6 * Bruise[a] + 0.6 * Sting, 0.0, 1.0));
		Skin[a]->SetScalarParameterValue(PTired, 1.0 - StaminaLeft(H.Stamina[a]));
	}
}

void ASlapHUD::Dent(double Now)
{
	// the cheek dent: a fast push in, a slower spring back (display only; the morph targets come with the mesh)
	for (int32 a = 0; a < 2; ++a)
	{
		UPoseableMeshComponent* M = Body[a].Get();
		if (!M || !bDentMorphs[a] || DentSide[a] < 0) continue;
		const double T = Now - DentAt[a];
		const float W = T < 0.0 || T > DentRise + 4.0 * DentFall ? 0.f
			: DentAmount[a] * (T < DentRise ? (float)(T / DentRise) : (float)FMath::Exp(-(T - DentRise) / DentFall));
		USkeletalMesh* Mesh = Cast<USkeletalMesh>(M->GetSkinnedAsset());
		if (!Mesh) continue;
		M->MorphTargetWeights.SetNumZeroed(Mesh->GetMorphTargets().Num());
		for (int32 s = 0; s < 2; ++s)
		{
			int32 Idx = INDEX_NONE;
			const UMorphTarget* Morph = Mesh->FindMorphTargetAndIndex(DentMorph[s], Idx);
			if (!Morph || !M->MorphTargetWeights.IsValidIndex(Idx)) continue;
			const float V = s == DentSide[a] ? W : 0.f;
			M->MorphTargetWeights[Idx] = V;
			if (V > 0.f) M->ActiveMorphTargets.Add(Morph, Idx);
			else M->ActiveMorphTargets.Remove(Morph);
		}
		M->MarkRenderDynamicDataDirty();
		if (W <= 0.f) DentSide[a] = -1;	// sprung back: nothing to do until the next strike
	}
}

void ASlapHUD::Stage(const FSlapHud& H, double Now)
{
	// a strike flares the key and the striker's rim, scaled by the palm's speed; the defender's rim lifts through his turn;
	// a KO runs a chase round the truss. A phone keeps one dynamic light (the follow spot): the rig stays as it is
	if (bMobile || TierLive == 0) return;
	auto Set = [](FStageLight& Lit, double Gain) { if (ULightComponent* L = Lit.Light.Get()) L->SetIntensity(Lit.Base * Gain); };
	const double Ts = Now - StrikeAt, Tk = Now - KOAt;
	const double K = StrikeK;
	const int32 Def = Team(H.Defender);
	const float Dt = GetWorld()->GetDeltaSeconds();
	Set(Key, Ts < 1.0 ? 1.0 + 1.5 * K * FMath::Exp(-8.0 * Ts) : 1.0);
	for (int32 a = 0; a < 2; ++a)
	{
		const double Base = H.Match > 0 && !H.bWalkIn && H.Result.IsEmpty() && a == Def ? DefenderRim : 1.0;
		RimNow[a] = FMath::FInterpTo(RimNow[a], (float)Base, Dt, 3.f);
		Set(Rim[a], RimNow[a] * (a != Struck && Ts < 1.5 ? 1.0 + 4.0 * K * FMath::Exp(-4.0 * Ts) : 1.0));
	}
	for (int32 k = 0; k < Truss.Num(); ++k) Set(Truss[k], Tk < KOShot ? 1.0 + 3.0 * FMath::Pow(FMath::Max(0.0, FMath::Sin(10.0 * Tk - 0.8 * k)), 4.0) : 1.0);
}

void ASlapHUD::Spotlight(const FSlapHud& H)
{
	// a narrow spot from above: on the striker through the wind-up, snapping onto the struck head at the touch and a KO,
	// onto the winner after the result
	USpotLightComponent* L = Spot.Get();
	if (!L || H.Match == 0) return;
	const double Now = GetWorld()->GetTimeSeconds();
	const bool bOnStruck = Now - StrikeAt < 1.6 || Now - KOAt < KOShot;
	int32 Who = bOnStruck ? Struck : Team(H.Striker);
	if (!H.Result.IsEmpty() && H.Winner >= 0) Who = H.Winner;
	if (!Body[Who].IsValid()) return;
	const FVector Head = Body[Who]->GetBoneLocation(BoneHead);
	const FVector Want = bOnStruck ? Head : 0.5f * (Head + Body[Who]->GetBoneLocation(BonePelvis));
	SpotAim = SpotAim.IsZero() ? Want : FMath::VInterpTo(SpotAim, Want, GetWorld()->GetDeltaSeconds(), bOnStruck ? 14.f : 4.f);
	const FVector Pos = SpotAim + FVector::UpVector * 520.f - BaseRot.Vector() * 160.f;	// above, a little toward the camera
	L->SetWorldLocationAndRotation(Pos, (SpotAim - Pos).Rotation());
	L->SetIntensity(SpotBase * (Now - StrikeAt < 1.0 ? 1.f + 1.5f * StrikeK * FMath::Exp(-6.0 * (Now - StrikeAt)) : 1.f));
}

void ASlapHUD::Scuffs(const FSlapHud& H)
{
	// from the drawn bones (display only): a sole skidding along the floor squeaks and leaves a streak; a palm landing on the
	// podium leaves a mark where it braced
	const double Now = GetWorld()->GetTimeSeconds();
	const float Dt = GetWorld()->GetDeltaSeconds();
	if (!Podium.IsValid)
	{
		AAMjManager* Manager = AAMjManager::GetManager();
		const mjModel* m = Manager && Manager->PhysicsEngine ? Manager->PhysicsEngine->GetModel() : nullptr;
		const int32 g = m ? Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("podium")) : -1;
		if (g >= 0) Podium = FBox::BuildAABB(MjUtils::MjToUEPosition(m->geom_pos + 3 * g), 100.0 * FVector(m->geom_size[3 * g], m->geom_size[3 * g + 1], m->geom_size[3 * g + 2]));
	}
	const bool bMark = H.Match > 0 && !H.bWalkIn && Now >= ReplayEnd && Dt > 0.f;
	for (int32 a = 0; a < 2; ++a)
	{
		if (!Body[a].IsValid()) continue;
		for (int32 f = 0; f < 2; ++f)
		{
			const FVector P = Body[a]->GetBoneLocation(f ? BoneFootR : BoneFootL);
			const FVector V = (P - FootPrev[a][f]) / FMath::Max(Dt, 1e-4f);
			FootPrev[a][f] = P;
			FootLow[a][f] = FMath::Min(FootLow[a][f], (float)P.Z);	// the ankle's planted height
			const float Sp = V.Size2D();	// cm/s
			Slide[a][f] = bMark && P.Z < FootLow[a][f] + 3.f && Sp > 50.f && Sp < 400.f ? Slide[a][f] + 1 : 0;	// a reset's jump breaks the run
			if (Slide[a][f] < 4 || Now < FootNext[a][f]) continue;	// a skid, not a twitch
			FootNext[a][f] = Now + 0.5;
			Scuff(FVector(P.X, P.Y, 0.0), V.Rotation().Yaw, Sp > 150.f ? 1 : 0);
			USoundBase* Snd = Sp > 90.f && Squeaks.Num() ? Squeaks[FMath::RandHelper(Squeaks.Num())].Get() : Scrape.Get();
			if (Snd) UGameplayStatics::PlaySoundAtLocation(this, Snd, P, FMath::Clamp(Sp / 150.f, 0.3f, 1.2f), FMath::FRandRange(0.9f, 1.1f), 0.f, Ringside);
		}
		for (int32 h = 0; h < 2 && Podium.IsValid; ++h)
		{
			const FVector P = Body[a]->GetBoneLocation(h ? BoneHandR : BoneHandL);	// the wrist: the palm lies within ~12 cm
			const bool bOn = FMath::Abs(P.Z - Podium.Max.Z) < 12.f && P.X > Podium.Min.X - 6.f && P.X < Podium.Max.X + 6.f && P.Y > Podium.Min.Y - 4.f && P.Y < Podium.Max.Y + 4.f;
			if (bOn && !bPalmDown[a][h] && bMark) Scuff(FVector(P.X, P.Y, Podium.Max.Z), 0.f, 2);
			bPalmDown[a][h] = bOn;
		}
	}
}

void ASlapHUD::Scuff(const FVector& At, float Yaw, int32 Kind)
{
	// A deferred decal pointing down (its U runs along Yaw), the match's last 8 kept (4 on a low tier). Kind 0/1: a sole skid
	// (the assets stream's T_Decal_SoleSkid_01/02, 30 x 8 / 40 x 10 cm), 2: a palm smudge on the podium (20 x 20 cm); the
	// procedural M_Scuff when the textured decals are not imported. A phone has no DBuffer: no decals there.
	if (bMobile) return;
	static const FVector Size[3] = { FVector(10.f, 4.f, 15.f), FVector(10.f, 5.f, 20.f), FVector(10.f, 10.f, 10.f) };
	UMaterialInterface* Mat = ScuffMat;
	if (DecalMat && DecalTex.IsValidIndex(Kind) && DecalTex[Kind])
	{
		UMaterialInstanceDynamic* M = UMaterialInstanceDynamic::Create(DecalMat, this);
		M->SetTextureParameterValue(PDecal, DecalTex[Kind]);
		M->SetScalarParameterValue(PRoughness, Kind == 2 ? 0.12f : 0.85f);	// decals.json
		M->SetScalarParameterValue(POpacity, 0.85f);
		Mat = M;
	}
	if (!Mat) return;
	if (UDecalComponent* D = UGameplayStatics::SpawnDecalAtLocation(this, Mat, Size[FMath::Clamp(Kind, 0, 2)], At, FRotator(-90.f, Yaw, 0.f)))
	{
		D->SetFadeScreenSize(0.f);
		Marks.Add(D);
	}
	while (Marks.Num() > (TierLive == 0 ? 4 : 8))
	{
		if (Marks[0].IsValid()) Marks[0]->DestroyComponent();
		Marks.RemoveAt(0);
	}
}

void ASlapHUD::Dust(const FSlapHud& H)
{
	// A puff of dust where a body part (not a sole, not a palm) hit the floor or the podium: the controller publishes the
	// contact point with the knockdown (DownPos / DownSeq), from MuJoCo's own contact list
	if (H.DownSeq == SeenDown) return;
	SeenDown = H.DownSeq;
	DownAt = GetWorld()->GetTimeSeconds();
	if (!DustPuff || H.Match <= 0) return;
	UNiagaraFunctionLibrary::SpawnSystemAtLocation(GetWorld(), DustPuff, H.DownPos, FRotator::ZeroRotator, FVector(TierLive == 0 ? 0.35f : 0.5f));
	SLAP_HUD_LOG(1, TEXT("SlapHUD: dust at %s"), *H.DownPos.ToCompactString());
}

int32 ASlapHUD::DetectTier() const
{
	// What this device can afford: 0 low, 1 medium, 2 high. The device profile's name (Config/DefaultDeviceProfiles.ini names
	// the phone families), the core count and the memory decide it. Display only: the physics never changes.
	const FString Profile = UDeviceProfileManager::Get().GetActiveDeviceProfileName();
	const uint32 Cores = FPlatformMisc::NumberOfCoresIncludingHyperthreads();
	const uint64 GB = FPlatformMemory::GetConstants().TotalPhysicalGB;
	int32 T = 2;
	if (Cores <= 4 || GB <= 3) T = 0;
	else if (Cores <= 6 || GB <= 5) T = 1;
	static const TCHAR* Low[] = { TEXT("Android_Low"), TEXT("Android_Samsung_Galaxy_S"), TEXT("Android_Moto"), TEXT("Android_Xiaomi"), TEXT("Android_OnePlus"), TEXT("IOS_iPhoneSE") };
	for (const TCHAR* L : Low)
	{
		if (Profile.Contains(L)) { T = 0; break; }
	}
	return T;
}

void ASlapHUD::ApplyTier(int32 Live)
{
	// The tier in force: the spray's rate, how much of the crowd is drawn, the follow spot's shadow, the decal budget and,
	// below HIGH, the engine's scalability for shadows, post-processing and effects (HIGH leaves the project's own settings).
	// The VIEW tab can pin a tier; AUTO follows the device and steps down under load (AutoTier).
	TierLive = FMath::Clamp(Live, 0, bMobile ? 2 : 3);
	const bool bLow = TierLive == 0;
	CrowdKeep = bLow ? 50 : (TierLive == 1 ? 25 : 0);	// per cent of the seats hidden under the floor
	for (UMaterialInstanceDynamic* M : CrowdSkin)
		if (M) M->SetScalarParameterValue(PCrowdCut, static_cast<float>(CrowdKeep));
	if (USpotLightComponent* L = Spot.Get()) L->SetCastShadows(!bLow && !bMobile);	// a phone has no movable spot shadows
	Scalability::FQualityLevels Q = Scalability::GetQualityLevels();
	if (BaseQuality[0] < 0)
	{
		BaseQuality[0] = Q.ShadowQuality; BaseQuality[1] = Q.PostProcessQuality; BaseQuality[2] = Q.EffectsQuality;
		BaseQuality[3] = Q.FoliageQuality; BaseQuality[4] = Q.ShadingQuality;
	}
	auto Cap = [&](int32 Base) { return TierLive >= 2 ? Base : FMath::Min(Base, TierLive); };
	Q.ShadowQuality = Cap(BaseQuality[0]); Q.PostProcessQuality = Cap(BaseQuality[1]); Q.EffectsQuality = Cap(BaseQuality[2]);
	Q.FoliageQuality = Cap(BaseQuality[3]); Q.ShadingQuality = Cap(BaseQuality[4]);
	Scalability::SetQualityLevels(Q);
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: quality tier %d (%s), crowd cut %d %%, scalability shadows %d post %d effects %d"), TierLive, Tier >= 0 ? TEXT("pinned") : TEXT("auto"), CrowdKeep,
		Q.ShadowQuality, Q.PostProcessQuality, Q.EffectsQuality);
	ApplyCinema(TierLive == 3);
	RenderAudit();
}

void ASlapHUD::RenderAudit()
{
	// 23/28 (2026-11-04): HIGH and up keep contact shadows on the key and the rims (the palm on the cheek, the soles) and
	// screen-space reflections (the SSR pass itself when the reflection method is SSR, Lumen's screen traces when it is Lumen);
	// the one-shots stay spatialised (HRTF on desktop). CINEMA's "contact" word lengthens the ray (ApplyCinema).
	if (bMobile) return;
	auto Set = [](const TCHAR* Name, int32 V)
	{
		if (IConsoleVariable* C = IConsoleManager::Get().FindConsoleVariable(Name)) C->Set(V, ECVF_SetByCode);
	};
	auto Get = [](const TCHAR* Name) { IConsoleVariable* C = IConsoleManager::Get().FindConsoleVariable(Name); return C ? C->GetInt() : -1; };
	const bool bHigh = TierLive >= 2;
	const bool bCineContact = TierLive == 3 && CineFeatures.Contains(TEXT("contact"));
	if (!bCineContact)
	{
		const float Contact = bHigh ? 0.04f : 0.f;
		for (const FStageLight* L : { &Key, &Rim[0], &Rim[1] })
			if (ULightComponent* C = L->Light.Get()) { C->ContactShadowLength = Contact; C->MarkRenderStateDirty(); }
	}
	if (bHigh)
	{
		Set(TEXT("r.ContactShadows"), 1);
		Set(TEXT("r.SSR.Quality"), FMath::Max(3, Get(TEXT("r.SSR.Quality"))));
		Set(TEXT("r.Lumen.Reflections.ScreenTraces"), 1);
	}
	const ULightComponent* K = Key.Light.Get();
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: render audit tier %d: contact shadows %d (key %.2f), reflections method %d (Lumen screen traces %d, SSR quality %d), GI %d; audio spatialised %d (%s)"),
		TierLive, Get(TEXT("r.ContactShadows")), K ? K->ContactShadowLength : -1.f, Get(TEXT("r.ReflectionMethod")), Get(TEXT("r.Lumen.Reflections.ScreenTraces")), Get(TEXT("r.SSR.Quality")),
		Get(TEXT("r.DynamicGlobalIlluminationMethod")), Ringside ? (int32)Ringside->Attenuation.bSpatialize : -1,
		Ringside && Ringside->Attenuation.SpatializationAlgorithm == ESoundSpatializationAlgorithm::SPATIALIZATION_HRTF ? TEXT("HRTF") : TEXT("panning"));
}

void ASlapHUD::ApplyCinema(bool bOn)
{
	// Tier 3 CINEMA, desktop only (the user, 2026-11-04: push the desktop look to a 30 fps floor; phones untouched). Each
	// feature is a word in -SlapCine= so the fps ladder can add them one at a time: epic (scalability 3 across the board),
	// gi (Lumen GI, hardware ray tracing where the GPU has it: the project's r.Lumen.HardwareRayTracing), refl (Lumen
	// reflections on the floor and the podium), contact (contact shadows on the key and rim lights), sss (full-res skin
	// subsurface), spray (a denser impact burst). Off = the project's own settings back. Display only.
	static bool bWasOn = false;
	if (bMobile || (!bOn && !bWasOn)) return;
	bWasOn = bOn;
	auto Has = [&](const TCHAR* F) { return bOn && CineFeatures.Contains(F); };
	auto Set = [](const TCHAR* Name, int32 V)
	{
		if (IConsoleVariable* C = IConsoleManager::Get().FindConsoleVariable(Name)) C->Set(V, ECVF_SetByCode);
	};
	if (Has(TEXT("epic")))
	{
		Scalability::FQualityLevels Q = Scalability::GetQualityLevels();
		Q.SetFromSingleQualityLevel(3);
		Scalability::SetQualityLevels(Q);
	}
	Set(TEXT("r.DynamicGlobalIlluminationMethod"), !bOn || Has(TEXT("gi")) ? 1 : 0);	// the project's default is Lumen (1)
	Set(TEXT("r.ReflectionMethod"), !bOn || Has(TEXT("refl")) ? 1 : 2);			// Lumen reflections, else screen space
	Set(TEXT("r.Lumen.Reflections.HardwareRayTracing"), Has(TEXT("refl")) ? 1 : 0);
	Set(TEXT("r.SSS.Quality"), Has(TEXT("sss")) ? 1 : 0);
	Set(TEXT("r.SSS.HalfRes"), Has(TEXT("sss")) ? 0 : 1);
	const float Contact = Has(TEXT("contact")) ? 0.08f : 0.f;	// screen share of the contact-shadow ray: the palm on the cheek, the soles
	for (const FStageLight* L : { &Key, &Rim[0], &Rim[1] })
		if (ULightComponent* C = L->Light.Get()) { C->ContactShadowLength = Contact; C->MarkRenderStateDirty(); }
	SprayMul = Has(TEXT("spray")) ? 2.5f : 1.f;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: cinema %s (%s)"), bOn ? TEXT("on") : TEXT("off"), bOn ? *CineFeatures : TEXT("project settings"));
}

void ASlapHUD::AutoTier()
{
	// AUTO only: three seconds under 30 fps steps one tier down (never back up in a session: no flip-flopping)
	if (Tier >= 0 || !bSceneReady || TierLive == 0) { SlowSince = -1.0; return; }
	const double Now = FPlatformTime::Seconds();
	if (Fps >= SlowFps) { SlowSince = -1.0; return; }
	if (SlowSince < 0.0) { SlowSince = Now; return; }
	if (Now - SlowSince < SlowFor) return;
	SlowSince = -1.0;
	TierCap = TierLive - 1;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: %.0f fps for %.0f s: stepping the quality down"), Fps, SlowFor);
	ApplyTier(TierCap);
}

void ASlapHUD::Burst(double Now)
{
	// The 120 fps burst: RENDER side only. The physics step is never touched (no FApp::SetFixedDeltaTime, no time
	// dilation): the engine's frame cap is raised for the length of the impact window and a screenshot is requested every
	// frame, so the slap is captured at up to 120 fps. Off by default, and never on a phone.
	if (!bBurst) return;
	const bool bWindow = Now - ImpactAt >= 0.0 && Now - ImpactAt < 0.6;
	IConsoleVariable* CapVar = IConsoleManager::Get().FindConsoleVariable(TEXT("t.MaxFPS"));
	if (bWindow)
	{
		if (BurstCap <= 0.f && CapVar)
		{
			BurstCap = FMath::Max(CapVar->GetFloat(), 0.001f);	// 0 (uncapped) is restored as 0.001: still uncapped
			CapVar->Set(120.f, ECVF_SetByCode);
		}
		if (BurstFrames < 40)	// one frame per capture, at most 40
		{
			FScreenshotRequest::RequestScreenshot(FString::Printf(TEXT("SlapBurst_%03d"), BurstFrames), false, true, false, FIntRect(), true);
			++BurstFrames;
		}
	}
	else if (BurstCap > 0.f)
	{
		if (CapVar) CapVar->Set(BurstCap < 0.01f ? 0.f : BurstCap, ECVF_SetByCode);
		BurstCap = 0.f;
	}
}
