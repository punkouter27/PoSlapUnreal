// USlapVoiceWave: the procedural stand-in for a voice line that has no recording yet (README: voice matrix).

#include "SlapVoiceSynth.h"

#include <atomic>

#include "Math/RandomStream.h"
#include "Sound/SoundGenerator.h"
#include "UObject/Package.h"

namespace
{
	constexpr int32 SynthRate = 24000;	// Hz: a voice's formants sit under 3.5 kHz; the generator resamples to the device rate

	// one shape per emotion (ASlapHUD::EEmote order); the level and the card scale it
	struct FVoiceShape
	{
		float Pitch;	// Hz at level 1, before the card's offset
		float Glide;	// semitones over the line (Arch: up and back down)
		bool bArch;
		float Seconds;
		float Rough;	// pitch jitter, shimmer and drive
		float Breath;	// aspiration noise
		float Syllables;	// bursts per second (0: one sustained cry)
		float Vibrato;	// semitones
		int32 VowelFrom, VowelTo;
	};
	const FVoiceShape Shapes[] = {
		{ 210.f, 3.f, false, 0.55f, 0.10f, 0.25f, 6.5f, 0.0f, 0, 0 },	// HAPPY: "ha-ha-ha", rising
		{ 125.f, -2.f, false, 0.60f, 0.55f, 0.15f, 0.0f, 0.0f, 0, 2 },	// ANGRY: a low growl, "raah"
		{ 250.f, -9.f, false, 0.35f, 0.30f, 0.30f, 0.0f, 0.0f, 2, 3 },	// HURT: a sharp falling "ow"
		{ 165.f, 4.f, true, 0.60f, 0.25f, 0.10f, 3.0f, 0.0f, 1, 0 },	// INSULT: a sing-song taunt
		{ 230.f, 5.f, false, 1.00f, 0.20f, 0.20f, 5.0f, 0.6f, 0, 1 },	// CELEBRATE: a long rising whoop with laughter
	};
	// formant centres (Hz): a, e, o, u
	const float Vowels[][3] = { { 730.f, 1090.f, 2440.f }, { 530.f, 1840.f, 2480.f }, { 570.f, 840.f, 2410.f }, { 300.f, 870.f, 2240.f } };
	const float FormantGain[3] = { 1.f, 0.55f, 0.28f };
	const float FormantWidth[3] = { 90.f, 110.f, 160.f };	// Hz

	struct FBandPass	// RBJ band-pass, 0 dB peak
	{
		float B0 = 0.f, A1 = 0.f, A2 = 0.f, X1 = 0.f, X2 = 0.f, Y1 = 0.f, Y2 = 0.f;
		void Set(float Freq, float Width)
		{
			const float W0 = 2.f * PI * FMath::Min(Freq, 0.45f * SynthRate) / SynthRate;
			const float Alpha = FMath::Sin(W0) * Width / (2.f * Freq);
			const float A0 = 1.f + Alpha;
			B0 = Alpha / A0;
			A1 = -2.f * FMath::Cos(W0) / A0;
			A2 = (1.f - Alpha) / A0;
		}
		float Run(float X)
		{
			const float Y = B0 * (X - X2) - A1 * Y1 - A2 * Y2;
			X2 = X1; X1 = X; Y2 = Y1; Y1 = Y;
			return Y;
		}
	};

	float PolyBlep(float T, float Dt)
	{
		if (T < Dt) { T /= Dt; return T + T - T * T - 1.f; }
		if (T > 1.f - Dt) { T = (T - 1.f) / Dt; return T * T + T + T + 1.f; }
		return 0.f;
	}

	class FSlapVoiceGenerator final : public ISoundGenerator
	{
	public:
		FSlapVoiceGenerator(TSharedRef<const TArray<float>, ESPMode::ThreadSafe> InPcm, double InStep, int32 InChannels)
			: Pcm(InPcm), Step(InStep), Channels(FMath::Max(InChannels, 1)) {}

		virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override
		{
			const TArray<float>& S = *Pcm;
			const int32 Frames = NumSamples / Channels;
			for (int32 f = 0; f < Frames; ++f)
			{
				const int32 K = (int32)Pos;
				float V = 0.f;
				if (K + 1 < S.Num())
				{
					V = FMath::Lerp(S[K], S[K + 1], (float)(Pos - K));
					Pos += Step;
				}
				else bDone = true;
				for (int32 c = 0; c < Channels; ++c) OutAudio[f * Channels + c] = V;
			}
			return NumSamples;
		}
		virtual bool IsFinished() const override { return bDone; }
		virtual int32 GetNumChannels() const override { return Channels; }

	private:
		TSharedRef<const TArray<float>, ESPMode::ThreadSafe> Pcm;
		double Pos = 0.0;
		double Step;
		int32 Channels;
		std::atomic<bool> bDone { false };
	};
}

USlapVoiceWave* USlapVoiceWave::Synthesize(UObject* Outer, const FString& Card, int32 Emotion, int32 Take, int32 Level)
{
	const FVoiceShape& V = Shapes[FMath::Clamp(Emotion, 0, (int32)UE_ARRAY_COUNT(Shapes) - 1)];
	const float L = (FMath::Clamp(Level, 1, 5) - 1) / 4.f;	// 0 weakest .. 1 strongest
	// the card's voice: a fixed pitch offset (-6..+6 semitones) and vocal-tract size from its name, so cards differ
	const uint32 CardHash = GetTypeHash(Card.ToLower());
	const float CardSemis = (float)(CardHash % 13) - 6.f;
	const float Tract = 0.92f + 0.16f * (float)((CardHash / 13) % 101) / 100.f;
	FRandomStream R((int32)HashCombine(CardHash, (uint32)(Emotion * 100 + Take * 10 + Level)));
	// the take: a slightly different line each time
	const float TakeSemis = R.FRandRange(-1.f, 1.f);
	const float Seconds = V.Seconds * (0.7f + 0.7f * L) * R.FRandRange(0.88f, 1.12f);
	const float Pitch = V.Pitch * FMath::Pow(2.f, (CardSemis + TakeSemis + 4.f * L) / 12.f);	// louder lines are higher
	const float Glide = V.Glide * (0.7f + 0.6f * L);
	const float Rough = FMath::Min(V.Rough + 0.35f * L, 1.f);
	const float Loud = 0.3f + 0.6f * L;

	const int32 N = FMath::Max(1, FMath::RoundToInt(Seconds * SynthRate));
	TArray<float> Out;
	Out.SetNumUninitialized(N);
	FBandPass Formant[3];
	float Phase = 0.f, Jitter = 0.f, Shimmer = 0.f, Peak = 1e-6f;
	const float Attack = 0.02f, Release = FMath::Min(0.12f, 0.4f * Seconds);
	for (int32 i = 0; i < N; ++i)
	{
		const float T = (float)i / SynthRate, U = (float)i / N;
		if ((i & 31) == 0)	// the vowel moves through the line
			for (int32 k = 0; k < 3; ++k)
				Formant[k].Set(Tract * FMath::Lerp(Vowels[V.VowelFrom][k], Vowels[V.VowelTo][k], U), FormantWidth[k]);
		Jitter = 0.995f * Jitter + 0.005f * R.FRandRange(-1.f, 1.f);
		Shimmer = 0.99f * Shimmer + 0.01f * R.FRandRange(-1.f, 1.f);
		const float Semis = (V.bArch ? Glide * FMath::Sin(PI * U) : Glide * U) + V.Vibrato * FMath::Sin(2.f * PI * 5.5f * T);
		const float F0 = Pitch * FMath::Pow(2.f, Semis / 12.f) * (1.f + 1.2f * Rough * Jitter);
		const float Dt = F0 / SynthRate;
		Phase += Dt;
		if (Phase >= 1.f) Phase -= 1.f;
		const float Buzz = (2.f * Phase - 1.f - PolyBlep(Phase, Dt)) * (1.f + 4.f * Rough * Shimmer);
		const float Src = 0.6f * Buzz + R.FRandRange(-1.f, 1.f) * V.Breath * (0.3f + 0.5f * Rough);
		float Y = 0.f;
		for (int32 k = 0; k < 3; ++k) Y += FormantGain[k] * Formant[k].Run(Src);
		float Env = FMath::Min(1.f, T / Attack) * FMath::Min(1.f, (Seconds - T) / Release);
		if (V.Syllables > 0.f) Env *= 0.12f + 0.88f * FMath::Pow(FMath::Abs(FMath::Sin(PI * V.Syllables * T)), 1.5f);
		Out[i] = Y * FMath::Max(Env, 0.f);
		Peak = FMath::Max(Peak, FMath::Abs(Out[i]));
	}
	// normalise, then drive: rougher lines are more saturated
	const float Drive = 1.f + 3.f * Rough;
	const float Norm = 1.f / FMath::Tanh(Drive);
	for (float& S : Out) S = Loud * Norm * FMath::Tanh(Drive * S / Peak);

	USlapVoiceWave* W = NewObject<USlapVoiceWave>(Outer ? Outer : GetTransientPackage());
	W->Pcm = MakeShared<TArray<float>, ESPMode::ThreadSafe>(MoveTemp(Out));
	W->PcmRate = SynthRate;
	W->SetSampleRate(SynthRate);
	W->NumChannels = 1;
	W->Duration = (float)N / SynthRate;
	W->bLooping = false;
	W->SoundGroup = SOUNDGROUP_Voice;
	return W;
}

ISoundGeneratorPtr USlapVoiceWave::CreateSoundGenerator(const FSoundGeneratorInitParams& InParams)
{
	if (!Pcm.IsValid() || Pcm->Num() < 2) return nullptr;
	const double Step = InParams.SampleRate > 0.f ? (double)PcmRate / InParams.SampleRate : 1.0;
	return MakeShared<FSlapVoiceGenerator, ESPMode::ThreadSafe>(Pcm.ToSharedRef(), Step, InParams.NumChannels);
}
