// ASlapHUD: the crowd, the breathing, the footsteps and the reverb mix. Display only.

#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Components/AudioComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "SlapHUDInternal.h"
#include "Sound/ReverbEffect.h"
#include "Sound/SoundBase.h"

using namespace SlapUi;

void ASlapHUD::Crowd(const FSlapHud& H, double Now)
{
	// the crowd follows the drama: the walk-in, a defender's KO danger, the last strike, a KO, the tension close-up, chat
	const int32 Def = Team(H.Defender);
	float Want = H.bWalkIn ? 0.5f : 0.f;
	// F6: the crowd reads the danger of BOTH athletes: either one near a knockdown lifts the whole arena
	if (H.Match > 0 && !H.bWalkIn)
	{
		for (int32 a = 0; a < 2; ++a) Want = FMath::Max(Want, 0.9f * (float)H.Danger[a]);
	}
	if (Now - StrikeAt < 3.0) Want = FMath::Max(Want, 0.6f * (float)StrikeK * FMath::Exp((StrikeAt - Now) / 1.2) * (bNearMiss ? 0.6f : 1.f));
	if (Now - KOAt < KOShot) Want = 1.f;
	if (Shot == EShot::Tension) Want = FMath::Max(Want, 0.35f);
	Want = FMath::Max(Want, 0.6f * Hype);
	const float Dt = GetWorld()->GetDeltaSeconds();
	Tension = FMath::FInterpTo(Tension, Want, Dt, Want > Tension ? 6.f : 1.5f);	// up fast, down slowly
	for (UMaterialInstanceDynamic* M : CrowdSkin)
	{
		if (!M) continue;
		M->SetScalarParameterValue(PRise, Tension);
		M->SetScalarParameterValue(PCheer, Tension);
	}
	if (UAudioComponent* A = CrowdAudio.Get()) A->SetVolumeMultiplier(0.35f * (1.f + 1.6f * Tension) * (Now - StrikeAt < 0.2 ? 0.3f : 1.f));	// ducked for the slap
	// a gasp the moment a struck defender's capture point leaves his soles
	if (H.Danger[Def] > 0.7 && H.bStruck && !bGasped && CrowdReact)
	{
		bGasped = true;
		UGameplayStatics::PlaySound2D(this, CrowdReact, 0.5f, 1.25f);
		if (Thud && Body[Def].IsValid()) UGameplayStatics::PlaySoundAtLocation(this, Thud, Body[Def]->GetBoneLocation(BonePelvis), 0.6f, 1.1f, 0.f, Ringside);	// he stumbles
	}
	else if (H.Danger[Def] < 0.3) bGasped = false;
}

void ASlapHUD::Pant(const FSlapHud& H)
{
	// each athlete breathes at his head: louder and quicker as stamina drains; the striker blows for a moment after a swing
	const double Now = GetWorld()->GetTimeSeconds();
	for (int32 a = 0; a < 2; ++a)
	{
		UAudioComponent* A = Breath[a].Get();
		if (!A || !Body[a].IsValid()) continue;
		const double Tired = H.Match > 0 ? FMath::Clamp((1.0 - H.Stamina[a]) / (1.0 - MinStamina), 0.0, 1.0) : 0.0;
		const double Effort = a != Struck && Now - StrikeAt < 3.0 ? 0.4 * FMath::Exp(StrikeAt - Now) : 0.0;
		const float Need = FMath::Min(1.0, Tired + Effort);
		if (!H.Card[a].IsEmpty() && BreathCard[a] != H.Card[a])	// his own voice (Web/audio.js startBreaths)
		{
			BreathCard[a] = H.Card[a];
			if (USoundBase* Own = Voice(TEXT("Breath"), H.Card[a]))
			{
				A->SetSound(Own);
				A->Play();
			}
		}
		A->SetWorldLocation(Body[a]->GetBoneLocation(BoneHead));
		A->SetVolumeMultiplier(0.01f + 0.6f * Need * Need);
		A->SetPitchMultiplier(1.f + 0.45f * Need);
	}
}

void ASlapHUD::Steps(const FSlapHud& H)
{
	// A footstep when a sole really touches the floor: the controller publishes the contact from MuJoCo's own contact
	// list (StepSeq), not from a timer, so the sound lands with the foot.
	for (int32 a = 0; a < 2; ++a)
	{
		if (H.StepSeq[a] == SeenStep[a]) continue;
		SeenStep[a] = H.StepSeq[a];
		if (!Step || H.Match <= 0) continue;
		// the padded mat inside 2.7 m of the centre, the hard stage deck outside it (the walk-in; Web/audio.js MAT_RADIUS)
		const bool bDeck = StepDeck && FVector::Dist2D(H.StepPos[a], FVector::ZeroVector) > MatRadius;
		const float V = FMath::FRandRange(0.9f, 1.1f);
		UGameplayStatics::PlaySoundAtLocation(this, bDeck ? StepDeck.Get() : Step.Get(), H.StepPos[a], FMath::FRandRange(0.25f, 0.5f) * (bDeck ? 0.8f : 1.f), V, 0.f, Ringside);
	}
}

USoundBase* ASlapHUD::Voice(const FString& Kind, const FString& Card)
{
	// S_Grunt_<card>, S_Breath_<card> (Grandpa's breath is the original S_Breath); looked up once per card, a miss is kept
	const FString VoiceKey = Kind + TEXT("_") + Card;
	if (const TObjectPtr<USoundBase>* Found = Voices.Find(VoiceKey)) return Found->Get();
	USoundBase* Snd = LoadNamed<USoundBase>(TEXT("/Game/Audio"), *(TEXT("S_") + VoiceKey));
	if (!Snd && Kind == TEXT("Breath")) Snd = BreathLoop;
	Voices.Add(VoiceKey, Snd);
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: voice %s -> %s"), *VoiceKey, Snd ? *Snd->GetName() : TEXT("none"));
	return Snd;
}

void ASlapHUD::ReverbMix(double Now)
{
	// The hall vs the ringside: the closer the camera is to the contact, the drier it sounds. Both reverbs run; the
	// blend follows the camera's distance to the last contact point.
	if (FMath::Abs(Now - LastMix) < 0.2) return;	// 5 Hz (a member: a function static outlived the level and stalled the next one's mix)
	LastMix = Now;
	if (!HallVerb || !CloseVerb || !Camera.IsValid()) return;
	const FVector At = StrikeAt > 0.0 ? StrikePos : (Body[0].IsValid() ? Body[0]->GetBoneLocation(BoneHead) : FVector::ZeroVector);
	const float Dist = FVector::Dist(Camera->GetActorLocation(), At);	// cm
	const float Wide = FMath::Clamp((Dist - 150.f) / 350.f, 0.f, 1.f);	// 150 cm: dry, 500 cm: the hall rings out
	UGameplayStatics::ActivateReverbEffect(this, HallVerb, TEXT("Arena"), 0.f, Wide, 0.15f);
	UGameplayStatics::ActivateReverbEffect(this, CloseVerb, TEXT("Ringside"), 0.f, 1.f - Wide, 0.15f);
}

