// The voice matrix's fallback: a short voice-like sound synthesised at runtime when a card's recorded line
// (S_V_<card>_<EMOTION>_<take>_<level>) is not imported yet. Display only.

#pragma once

#include "CoreMinimal.h"
#include "Sound/SoundWaveProcedural.h"
#include "SlapVoiceSynth.generated.h"

/**
 * A synthesised voice line: a glottal buzz (pitch glide, jitter, breath noise) through three vowel formants, shaped by the
 * emotion and the level, pitched per card. The samples are made once and kept; every play gets its own generator, so the
 * same wave can sound twice at once.
 */
UCLASS(Transient)
class USlapVoiceWave : public USoundWaveProcedural
{
	GENERATED_BODY()

public:
	/** Emotion order = ASlapHUD::EEmote: 0 HAPPY, 1 ANGRY, 2 HURT, 3 INSULT, 4 CELEBRATE. Take 1..5, Level 1..5. */
	static USlapVoiceWave* Synthesize(UObject* Outer, const FString& Card, int32 Emotion, int32 Take, int32 Level);

	virtual ISoundGeneratorPtr CreateSoundGenerator(const FSoundGeneratorInitParams& InParams) override;

private:
	TSharedPtr<const TArray<float>, ESPMode::ThreadSafe> Pcm;	// mono
	int32 PcmRate = 24000;
};
