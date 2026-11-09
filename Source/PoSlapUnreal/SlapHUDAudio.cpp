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
	// the last strike: by the impulse it delivered (strike mass x palm speed) with the display pass, else by the palm's speed
	const float StrikeLift = bPass ? 0.5f * FMath::Min((float)ImpulseK, 1.8f) : 0.6f * (float)StrikeK;
	if (Now - StrikeAt < 3.0) Want = FMath::Max(Want, StrikeLift * FMath::Exp((StrikeAt - Now) / 1.2) * (bNearMiss ? 0.6f : 1.f));
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
	SLAP_HUD_LOG(1, TEXT("SlapHUD: voice %s -> %s"), *VoiceKey, Snd ? *Snd->GetName() : TEXT("none"));
	return Snd;
}

void ASlapHUD::Emote(int32 a, EEmote Kind, int32 Level)
{
	static const TCHAR* Kinds[] = { TEXT("HAPPY"), TEXT("ANGRY"), TEXT("HURT"), TEXT("INSULT") };
	if (a < 0 || a > 1 || Last.Card[a].IsEmpty() || !GetWorld()) return;
	const double Now = GetWorld()->GetTimeSeconds();
	if (Kind != EEmote::Hurt && Now - EmoteAt[a] < EmoteGap) return;	// one line at a time; the pain cuts in
	Level = FMath::Clamp(Level, 1, 5);
	int32 Take = FMath::RandHelper(5);
	if (Take == EmoteTake[a]) Take = (Take + 1) % 5;	// never the same take twice running
	const FString& Card = Last.Card[a];
	const FString Name = FString::Printf(TEXT("S_V_%s_%s_%d_%d"), *Card, Kinds[(int32)Kind], Take + 1, Level);
	USoundBase* Snd = nullptr;
	if (const TObjectPtr<USoundBase>* Found = EmoteSounds.Find(Name)) Snd = Found->Get();
	else
	{
		Snd = LoadNamed<USoundBase>(*(TEXT("/Game/Audio/Voice/") + Card), *Name);
		EmoteSounds.Add(Name, Snd);
		if (!Snd) UE_LOG(LogSlap, Warning, TEXT("SlapHUD: no voice %s (Scripts/voice_pack.py, then voice_import.py)"), *Name);
	}
	if (!Snd) return;
	EmoteAt[a] = Now;
	EmoteTake[a] = Take;
	if (Body[a].IsValid()) UGameplayStatics::PlaySoundAtLocation(this, Snd, Body[a]->GetBoneLocation(BoneHead), 0.9f, FMath::FRandRange(0.98f, 1.02f), 0.f, Ringside);
	else UGameplayStatics::PlaySound2D(this, Snd, 0.8f);
	SLAP_HUD_LOG(1, TEXT("SlapHUD: %s says %s"), *Card, *Name);
}

void ASlapHUD::EmoteCues(const FSlapHud& H)
{
	// the introductions: each fighter taunts the crowd as his card slides in
	const double Ti = GetWorld()->GetTimeSeconds() - TapeAt;
	if (TapeAt > 0.0 && Ti >= 0.0 && Ti < 2.0 * IntroEach)
	{
		const int32 Who = Ti < IntroEach ? 0 : 1;
		if (Who > IntroSaid) { IntroSaid = Who; Emote(Who, EEmote::Insult, 3); }
	}
	else if (Ti >= 2.0 * IntroEach || Ti < 0.0) IntroSaid = -1;
	// a new turn: the striker taunts; louder each round and once the defender has been down
	const FString TurnKey = FString::Printf(TEXT("%d/%d/%s"), H.MatchSeq, H.Round, *H.Striker);
	if (TurnKey != EmoteTurnKey && H.Round > 0 && !H.bWalkIn && H.Result.IsEmpty())
	{
		EmoteTurnKey = TurnKey;
		EmoteTurnStrike = H.StrikeSeq;
		EmoteTurnDef = Team(H.Defender);
		Emote(1 - EmoteTurnDef, EEmote::Insult, H.Round + FMath::Min(H.Downs[EmoteTurnDef], 1) + FMath::RandHelper(2));
	}
	// a strike: the defender's pain, by the palm's speed (a clean slap ~ level 4)
	if (H.StrikeSeq != EmoteSeenStrike)
	{
		EmoteSeenStrike = H.StrikeSeq;
		const double K = FMath::Clamp(H.Speed / CleanSpeed, 0.0, 1.25);
		Emote(FMath::Clamp(H.StrikeDefender, 0, 1), EEmote::Hurt, 1 + FMath::RoundToInt(3.2 * K));
	}
	// the turn ended with no strike: the defender enjoys it
	if (H.TurnSeq != EmoteSeenTurn)
	{
		EmoteSeenTurn = H.TurnSeq;
		if (H.StrikeSeq == EmoteTurnStrike && H.KOd < 0) Emote(EmoteTurnDef, EEmote::Happy, 2);
	}
	// a KO: the winner's big laugh
	if (H.KOSeq != EmoteSeenKO)
	{
		EmoteSeenKO = H.KOSeq;
		if (H.KOd >= 0) Emote(1 - H.KOd, EEmote::Happy, 5);
	}
	// a knockdown (not out cold): angry with himself, more with each one
	for (int32 a = 0; a < 2; ++a)
	{
		if (H.Downs[a] > EmoteDowns[a] && H.KOd != a) Emote(a, EEmote::Angry, 2 + H.Downs[a]);
		EmoteDowns[a] = H.Downs[a];	// a new match resets them to 0
	}
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

