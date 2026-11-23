// The voice matrix at runtime: voices.json (scripts/voice_manifest.py) says which clip is an imported asset; every other
// clip is an advanced procedural imitation in that card's voice, synthesised once and kept. Display only.

#pragma once

#include "CoreMinimal.h"
#include "Sound/SoundWaveProcedural.h"
#include "SlapVoiceSynth.generated.h"

/** A card's procedural voice (voices.json "voice"). */
struct FSlapVoiceProfile
{
	float PitchHz = 120.f;
	float FormantScale = 1.f;	// vocal-tract size: > 1 a smaller, brighter tract
	float Press = 0.6f;			// strain: a tenser, buzzier pulse
	float Breath = 0.22f;		// aspiration noise
	float Rasp = 0.3f;			// jitter, shimmer, subharmonics
	float VibratoHz = 5.2f;
	float VibratoDepth = 0.02f;	// share of the pitch
	float Tempo = 1.f;			// > 1 quicker syllables

	/** A card with no manifest entry: a stable voice from its name (pitch 85-260 Hz, tract and rasp to match). */
	static FSlapVoiceProfile FromName(const FString& Card);
};

/** voices.json, read once: the profiles and the clips that are imported assets. */
struct FSlapVoiceManifest
{
	bool bLoaded = false;	// Load() has run
	bool bValid = false;	// a po-voice-manifest/1 file was read
	TMap<FString, FSlapVoiceProfile> Profiles;	// by card id
	TSet<FString> Imported;	// asset names (S_V_<card>_<EMOTION>_<take>_<level>) of real recordings: "exists" and "imported"
	int32 Clips = 0;		// takes listed

	/** Content/Art/Exports/Audio/Voice/voices.json (a checkout), else the staged Content/Data/voices.json. */
	bool Load();
	FSlapVoiceProfile ProfileFor(const FString& Card) const;
};

/**
 * A synthesised voice line: a Rosenberg glottal pulse (jitter, shimmer, subharmonic rasp, aspiration) through a cascade
 * of four vowel formants, built from 1-4 syllables with consonant onsets (h, s, sh, f, k, t, p, n, an inhaled gasp) and
 * per-emotion prosody; the level scales loudness, length, pitch range, rasp, breath, syllables and vibrato. Deterministic
 * per (card, emotion, take, level). Every play gets its own generator, so one cached wave can sound twice at once.
 */
UCLASS(Transient)
class USlapVoiceWave : public USoundWaveProcedural
{
	GENERATED_BODY()

public:
	static constexpr int32 SynthRate = 32000;	// Hz; the generator resamples to the device rate

	/** Emotion order = ASlapHUD::EEmote: 0 HAPPY, 1 ANGRY, 2 HURT, 3 INSULT, 4 CELEBRATE. Take 1..5, Level 1..5.
	 *  Peaks at 0.30 (level 1) .. 0.94 (level 5), the loudness voice_pack.py bakes into the recorded placeholders. */
	static TArray<float> Render(const FSlapVoiceProfile& Voice, const FString& Card, int32 Emotion, int32 Take, int32 Level);
	static USlapVoiceWave* Synthesize(UObject* Outer, const FSlapVoiceProfile& Voice, const FString& Card, int32 Emotion, int32 Take, int32 Level);

	virtual ISoundGeneratorPtr CreateSoundGenerator(const FSoundGeneratorInitParams& InParams) override;

private:
	TSharedPtr<const TArray<float>, ESPMode::ThreadSafe> Pcm;	// mono, SynthRate
};
