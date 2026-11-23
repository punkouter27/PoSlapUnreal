// USlapVoiceWave and FSlapVoiceManifest: voices.json at runtime, and the advanced procedural imitation for a voice
// line with no imported recording (README: voice matrix).

#include "SlapVoiceSynth.h"

#include <atomic>

#include "Dom/JsonObject.h"
#include "Math/RandomStream.h"
#include "Misc/Crc.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Sound/SoundGenerator.h"
#include "UObject/Package.h"

namespace
{
	constexpr float Fs = (float)USlapVoiceWave::SynthRate;

	// ---- vowels: the first four formants (Hz, an adult male tract; the profile's FormantScale moves them) ----
	enum EVowel : uint8 { VA, VE, VI, VO, VU, VSchwa };
	const float VowelF[6][4] = {
		{ 730.f, 1090.f, 2440.f, 3400.f },	// a
		{ 530.f, 1840.f, 2480.f, 3500.f },	// e
		{ 300.f, 2200.f, 2950.f, 3700.f },	// i
		{ 570.f, 840.f, 2410.f, 3300.f },	// o
		{ 300.f, 870.f, 2240.f, 3200.f },	// u
		{ 500.f, 1500.f, 2500.f, 3400.f },	// schwa
	};
	const float FormantBw[4] = { 80.f, 100.f, 140.f, 200.f };

	// a consonant before (onset) or after (coda) a syllable's vowel
	enum class ECons : uint8 { None, H, S, Sh, F, K, T, P, N };

	struct FSyl
	{
		float Dur = 0.2f;				// s, the voiced part
		float P0 = 0.f, P1 = 0.f;		// semitones from the line's pitch, start -> end
		float Curve = 1.f;				// < 1: reaches P1 early and holds it; > 1: holds P0, then moves
		float Arch = 0.f;				// semitones added mid-syllable (a rise-fall)
		float Break = 0.f;				// semitones dropped at 45 % (a voice break)
		EVowel V0 = VA, V1 = VA;
		ECons Onset = ECons::None, Coda = ECons::None;
		float Gain = 1.f, Gap = 0.05f;	// Gap: s of silence after
		float Growl = 0.f, Sob = 0.f;	// a rough 28-45 Hz flutter; a 7 Hz break in the voice
	};

	struct FLine
	{
		TArray<FSyl> Syl;
		float Oq = 0.55f;		// open quotient: tense ~0.4, breathy ~0.7
		float Rasp = 0.3f, Breath = 0.2f, Vib = 0.3f, Nasal = 0.f;	// Vib in semitones
		float Inhale = 0.f;		// s of gasp before the first syllable
	};

	// Klatt's two-pole resonator (unity gain at DC): the cascade of four is the vocal tract
	struct FReson
	{
		float A = 1.f, B = 0.f, C = 0.f, Y1 = 0.f, Y2 = 0.f;
		void Set(float F, float Bw)
		{
			F = FMath::Min(F, 0.45f * Fs);
			C = -FMath::Exp(-2.f * PI * Bw / Fs);
			B = 2.f * FMath::Exp(-PI * Bw / Fs) * FMath::Cos(2.f * PI * F / Fs);
			A = 1.f - B - C;
		}
		float Run(float X)
		{
			const float Y = A * X + B * Y1 + C * Y2;
			Y2 = Y1; Y1 = Y;
			return Y;
		}
	};

	struct FTract
	{
		FReson F[4], NasalR;
		float Nasal = 0.f;
		void Set(EVowel From, EVowel To, float Mix, float Scale, float InNasal)
		{
			Nasal = InNasal;
			for (int32 k = 0; k < 4; ++k)
			{
				float Fk = FMath::Lerp(VowelF[From][k], VowelF[To][k], Mix) * Scale;
				if (k == 1) Fk *= 1.f + 0.15f * Nasal;	// the sneer: F2 up
				F[k].Set(Fk, FormantBw[k] * (1.f + 0.4f * Nasal));
			}
			NasalR.Set(270.f, 100.f);
		}
		float Run(float X)
		{
			float Y = X;
			for (FReson& R : F) Y = R.Run(Y);
			return Nasal > 0.f ? (1.f - 0.5f * Nasal) * Y + 1.5f * Nasal * NasalR.Run(X) : Y;
		}
	};

	struct FBandPass	// RBJ band-pass (0 dB peak) for the fricatives and bursts
	{
		float B0 = 0.f, A1 = 0.f, A2 = 0.f, X1 = 0.f, X2 = 0.f, Y1 = 0.f, Y2 = 0.f;
		FBandPass(float Centre, float Q)
		{
			const float W0 = 2.f * PI * FMath::Min(Centre, 0.45f * Fs) / Fs;
			const float Alpha = FMath::Sin(W0) / (2.f * Q);
			const float A0 = 1.f + Alpha;
			B0 = Alpha / A0; A1 = -2.f * FMath::Cos(W0) / A0; A2 = (1.f - Alpha) / A0;
		}
		float Run(float X)
		{
			const float Y = B0 * (X - X2) - A1 * Y1 - A2 * Y2;
			X2 = X1; X1 = X; Y2 = Y1; Y1 = Y;
			return Y;
		}
	};

	// Rosenberg glottal flow, differentiated (lip radiation); per period: jitter, shimmer, and every other pulse
	// weakened by the rasp (a subharmonic)
	struct FGlottis
	{
		float Phase = 0.f, Prev = 0.f, Jit = 0.f, Amp = 1.f;
		int32 Period = 0;
		float Step(float F0, float Oq, float Rasp, FRandomStream& R, float& OutFlow)
		{
			const float Dt = F0 * (1.f + Jit) / Fs;
			Phase += Dt;
			if (Phase >= 1.f)
			{
				Phase -= 1.f;
				++Period;
				Jit = R.FRandRange(-1.f, 1.f) * (0.005f + 0.01f * Rasp);					// 0.5 - 1.5 %
				Amp = (1.f + R.FRandRange(-1.f, 1.f) * (0.03f + 0.03f * Rasp)) * ((Period & 1) ? 1.f - 0.45f * Rasp * Rasp : 1.f);	// 3 - 6 %
			}
			const float Tp = 0.62f * Oq, Tn = 0.38f * Oq;
			const float G = Phase < Tp ? 0.5f * (1.f - FMath::Cos(PI * Phase / Tp)) : Phase < Tp + Tn ? FMath::Cos(0.5f * PI * (Phase - Tp) / Tn) : 0.f;
			OutFlow = G;
			const float D = (G - Prev) / FMath::Max(Dt, 1e-5f);
			Prev = G;
			return 0.05f * Amp * D;
		}
	};

	float Peak(const TArray<float>& X)
	{
		float P = 0.f;
		for (float V : X) P = FMath::Max(P, FMath::Abs(V));
		return P;
	}

	void Append(TArray<float>& Out, const TArray<float>& X, float Gain)
	{
		Out.Reserve(Out.Num() + X.Num());
		for (float V : X) Out.Add(V * Gain);
	}

	/** One syllable's vowel: the pitch contour with vibrato, the vowel glide, the envelope. */
	TArray<float> Voiced(const FSyl& S, const FLine& L, float F0, const FSlapVoiceProfile& V, FTract& Tr, FGlottis& G, FRandomStream& R)
	{
		const int32 N = FMath::Max(16, FMath::RoundToInt(S.Dur * Fs));
		TArray<float> Out;
		Out.SetNumUninitialized(N);
		const float GrowlHz = R.FRandRange(28.f, 45.f);
		const float Release = FMath::Min(0.06f, 0.4f * S.Dur);
		float NoisePrev = 0.f;
		for (int32 i = 0; i < N; ++i)
		{
			const float T = i / Fs, U = (float)i / N;
			if ((i & 31) == 0)
			{
				const float M = FMath::SmoothStep(0.f, 1.f, FMath::Clamp((U - 0.2f) / 0.6f, 0.f, 1.f));
				Tr.Set(S.V0, S.V1, M, V.FormantScale, L.Nasal);
			}
			const float Semis = S.P0 + (S.P1 - S.P0) * FMath::Pow(U, S.Curve) + S.Arch * FMath::Sin(PI * U) - (U > 0.45f ? S.Break : 0.f)
				+ L.Vib * FMath::Min(1.f, T / 0.15f) * FMath::Sin(2.f * PI * V.VibratoHz * T);
			float Flow = 0.f;
			float Src = G.Step(F0 * FMath::Pow(2.f, Semis / 12.f), L.Oq, L.Rasp, R, Flow);
			if (S.Growl > 0.f) Src *= 1.f - S.Growl * 0.5f * (1.f + FMath::Sin(2.f * PI * GrowlHz * T));
			const float Noise = R.FRandRange(-1.f, 1.f);
			const float Asp = 0.5f * (Noise - NoisePrev) * L.Breath * (0.35f + 0.65f * Flow) * 0.6f;	// aspiration, in the open phase
			NoisePrev = Noise;
			float Env = FMath::Min(1.f, T / 0.015f) * FMath::Clamp((S.Dur - T) / Release, 0.f, 1.f);
			if (S.Sob > 0.f) Env *= 1.f - S.Sob * (0.5f + 0.5f * FMath::Sin(2.f * PI * 7.f * T));
			Out[i] = Tr.Run(Src + Asp) * Env * S.Gain;
		}
		return Out;
	}

	/** A consonant (or the inhaled gasp, C == None with Dur), peak 1. Vowel: the tract shape it is said into. */
	TArray<float> Consonant(ECons C, float Dur, EVowel Vowel, float F0, const FSlapVoiceProfile& V, FRandomStream& R)
	{
		TArray<float> Out;
		auto Noise = [&](float Seconds, float Centre, float Q, TFunctionRef<float(float)> Env)
		{
			FBandPass B(Centre, Q);
			const int32 N = FMath::RoundToInt(Seconds * Fs);
			for (int32 i = 0; i < N; ++i) Out.Add(B.Run(R.FRandRange(-1.f, 1.f)) * Env((float)i / N));
		};
		auto Burst = [&](float Centre)
		{
			Out.AddZeroed(FMath::RoundToInt(0.02f * Fs));	// the closure
			FBandPass B(Centre, 1.2f);
			const int32 N = FMath::RoundToInt(0.035f * Fs);
			for (int32 i = 0; i < N; ++i) Out.Add(B.Run(R.FRandRange(-1.f, 1.f)) * FMath::Exp(-i / (0.006f * Fs)));
		};
		switch (C)
		{
		case ECons::H:
		case ECons::None:	// h: breath through the vowel; None: the gasp (an inhaled "hhah", rising, cut sharp)
		{
			FTract Tr;
			Tr.Set(Vowel, Vowel, 0.f, V.FormantScale * (C == ECons::None ? 1.1f : 1.f), 0.f);
			const int32 N = FMath::RoundToInt((C == ECons::H ? 0.05f : Dur) * Fs);
			float Prev = 0.f;
			for (int32 i = 0; i < N; ++i)
			{
				const float U = (float)i / N, X = R.FRandRange(-1.f, 1.f);
				Out.Add(Tr.Run(X - Prev) * (C == ECons::H ? FMath::Sin(0.5f * PI * U) : FMath::Pow(U, 1.5f) * FMath::Clamp((1.f - U) / 0.08f, 0.f, 1.f)));
				Prev = X;
			}
			break;
		}
		case ECons::S: Noise(0.09f, 6000.f, 2.f, [](float U) { return FMath::Square(FMath::Sin(PI * U)); }); break;
		case ECons::Sh: Noise(0.10f, 3000.f, 2.f, [](float U) { return FMath::Square(FMath::Sin(PI * U)); }); break;
		case ECons::F: Noise(0.08f, 3500.f, 0.7f, [](float U) { return FMath::Sin(PI * U); }); break;
		case ECons::K: Burst(2500.f); break;
		case ECons::T: Burst(5000.f); break;
		case ECons::P: Burst(900.f); break;
		case ECons::N:	// a hummed nasal murmur at the syllable's pitch
		{
			FGlottis G;
			FReson M;
			M.Set(250.f * V.FormantScale, 80.f);
			const int32 N = FMath::RoundToInt(0.06f * Fs);
			float Flow = 0.f;
			for (int32 i = 0; i < N; ++i) Out.Add(M.Run(G.Step(F0, 0.55f, 0.f, R, Flow)) * FMath::Min(1.f, i / (0.01f * Fs)));
			break;
		}
		}
		const float P = Peak(Out);
		if (P > 0.f) for (float& X : Out) X /= P;
		return Out;
	}

	float ConsonantLevel(ECons C)
	{
		switch (C)
		{
		case ECons::H: return 0.45f;
		case ECons::S: case ECons::Sh: return 0.35f;
		case ECons::F: return 0.3f;
		case ECons::N: return 0.6f;
		default: return 0.7f;	// the plosive bursts
		}
	}

	/** The emotion's prosody for this take and level (k 0 .. 1). */
	FLine Plan(int32 Emotion, int32 Take, float K, int32 Level, const FSlapVoiceProfile& V, FRandomStream& R)
	{
		FLine L;
		L.Oq = 0.6f - 0.15f * V.Press;
		L.Rasp = FMath::Clamp(V.Rasp + 0.3f * K, 0.f, 1.f);
		L.Breath = V.Breath + 0.15f * K;
		L.Vib = 12.f * FMath::Log2(1.f + V.VibratoDepth) * (0.5f + K);
		const float Rg = 0.6f + 0.8f * K;	// the pitch range
		auto D = [&](float S) { return S / FMath::Max(V.Tempo, 0.3f) * R.FRandRange(0.92f, 1.08f); };
		auto Add = [&L](const FSyl& S) -> FSyl& { return L.Syl.Add_GetRef(S); };
		auto Laugh = [&](int32 N, EVowel Vw, float Top)	// "ha-ha-ha": aspirated, each a little lower
		{
			for (int32 i = 0; i < N; ++i)
			{
				FSyl S; S.Onset = ECons::H; S.Dur = D(0.1f + 0.02f * K); S.P0 = Top - 0.9f * i; S.P1 = S.P0 - 2.f * Rg; S.Arch = Rg;
				S.V0 = S.V1 = Vw; S.Gap = D(0.05f); S.Gain = 1.f - 0.08f * i;
				Add(S);
			}
		};
		switch (Emotion)
		{
		case 0:	// HAPPY: bright vowels, rise-fall, light vibrato, laughter at the higher levels ("heh", "ha-ha", "yeah!")
		{
			L.Oq += 0.05f;
			L.Vib *= 1.5f;
			if (Take < 4)
			{
				const EVowel Vw[] = { VE, VI, VA, VE };
				Laugh(1 + FMath::RoundToInt(3.f * K), Vw[Take], Rg * (4.f + (Take == 1 ? 1.f : 0.f)));
			}
			else
			{
				FSyl S; S.Dur = D(0.3f + 0.35f * K); S.P0 = 0.f; S.P1 = 5.f * Rg; S.Curve = 0.6f; S.Arch = Rg; S.V0 = VI; S.V1 = VA;
				Add(S);
				if (K > 0.5f) Laugh(1 + FMath::RoundToInt(4.f * (K - 0.5f)), VA, 4.f * Rg);
			}
			break;
		}
		case 1:	// ANGRY: tense, loud, falling, rasp and growl, hard onsets ("rraah", "hup!", "argh", "hey!", "grr-ah")
		{
			L.Oq -= 0.12f;
			L.Rasp = FMath::Min(1.f, L.Rasp + 0.35f);
			FSyl S;
			switch (Take)
			{
			case 0: S.Dur = D(0.35f + 0.35f * K); S.P0 = 2.f * Rg; S.P1 = -4.f * Rg; S.Curve = 1.5f; S.V0 = VSchwa; S.V1 = VA; S.Growl = 0.5f + 0.3f * K; Add(S); break;
			case 1:
				S.Onset = ECons::H; S.Coda = ECons::P; S.Dur = D(0.12f + 0.08f * K); S.P0 = 3.f * Rg; S.P1 = 0.f; S.V0 = S.V1 = VSchwa; S.Gap = D(0.12f);
				Add(S);
				if (K > 0.5f) Add(S).P0 += 1.f;	// "hup! hup!"
				break;
			case 2: S.Dur = D(0.3f + 0.3f * K); S.P0 = Rg; S.P1 = -5.f * Rg; S.V0 = VA; S.V1 = VO; S.Growl = 0.35f + 0.3f * K; S.Coda = ECons::H; Add(S); break;
			case 3: S.Onset = ECons::H; S.Dur = D(0.2f + 0.2f * K); S.P0 = 4.f * Rg; S.P1 = Rg; S.V0 = VE; S.V1 = VI; Add(S); break;
			default:
			{
				FSyl G; G.Onset = ECons::K; G.Dur = D(0.15f); G.P0 = G.P1 = -2.f; G.V0 = VU; G.V1 = VSchwa; G.Growl = 0.8f; G.Gap = 0.02f;
				Add(G);
				S.Dur = D(0.25f + 0.25f * K); S.P0 = 3.f * Rg; S.P1 = -3.f * Rg; S.V0 = S.V1 = VA; S.Growl = 0.3f;
				Add(S);
			}
			}
			break;
		}
		case 2:	// HURT: breathy; a gasp, then a groan that breaks downward ("oof", "ugh", "ow", "ah!", a moan; level 5 a long cry)
		{
			L.Breath += 0.25f;
			L.Oq += 0.1f;
			L.Inhale = K > 0.25f && Take != 3 ? D(0.1f + 0.12f * K) : 0.f;
			FSyl S;
			if (Level >= 5)
			{
				S.Dur = D(1.2f); S.P0 = 4.f * Rg; S.P1 = -3.f * Rg; S.Arch = 2.f; S.V0 = VA; S.V1 = VU; S.Sob = 0.5f;
				Add(S);
				break;
			}
			switch (Take)
			{
			case 0: S.Dur = D(0.15f + 0.15f * K); S.P0 = 2.f * Rg; S.P1 = -3.f * Rg; S.Break = 3.f * K; S.V0 = S.V1 = VU; S.Coda = ECons::F; break;
			case 1: S.Dur = D(0.2f + 0.25f * K); S.P0 = Rg; S.P1 = -2.f * Rg; S.Break = 4.f * Rg; S.V0 = S.V1 = VSchwa; S.Growl = 0.2f; S.Coda = ECons::H; break;
			case 2: S.Dur = D(0.25f + 0.3f * K); S.P0 = 5.f * Rg; S.P1 = -2.f * Rg; S.Curve = 1.5f; S.Break = 3.f * K; S.V0 = VA; S.V1 = VU; break;
			case 3: S.Dur = D(0.15f + 0.1f * K); S.P0 = 6.f * Rg; S.P1 = 2.f * Rg; S.V0 = S.V1 = VA; break;
			default: S.Dur = D(0.5f + 0.4f * K); S.P0 = 0.f; S.P1 = -4.f * Rg; S.V0 = VO; S.V1 = VU; S.Sob = 0.3f + 0.3f * K; break;
			}
			Add(S);
			break;
		}
		case 3:	// INSULT: a sneering nasal sing-song ("nyah-nyah", "pfft-heh", a jeering "eh-eh?", "ha-HAH", fast babble)
		{
			L.Nasal = 0.35f + 0.2f * K;
			L.Oq -= 0.05f;
			switch (Take)
			{
			case 0:
			{
				const float Tune[] = { 3.f, 0.f, 5.f, 3.f };	// na-na-NA-na
				const int32 N = 2 + FMath::RoundToInt(2.f * K);
				for (int32 i = 0; i < N; ++i)
				{
					FSyl S; S.Onset = ECons::N; S.Dur = D(0.13f + (i == N - 1 ? 0.08f : 0.f)); S.P0 = Tune[i] * Rg; S.P1 = S.P0 - 1.f;
					S.V0 = VI; S.V1 = VA; S.Gap = D(0.04f);
					Add(S);
				}
				break;
			}
			case 1:
			{
				FSyl P; P.Onset = ECons::P; P.Coda = ECons::F; P.Dur = 0.03f; P.Gain = 0.2f; P.V0 = P.V1 = VSchwa; P.Gap = D(0.08f);
				Add(P);
				for (int32 i = 0; i < 1 + (K > 0.5f); ++i)
				{
					FSyl S; S.Onset = ECons::H; S.Dur = D(0.1f); S.P0 = 2.f * Rg; S.P1 = 0.f; S.V0 = S.V1 = VE; S.Gap = D(0.05f);
					Add(S);
				}
				break;
			}
			case 2:
			{
				FSyl A; A.Dur = D(0.14f); A.P0 = 0.f; A.P1 = -1.f; A.V0 = A.V1 = VE; A.Gap = D(0.06f);
				Add(A);
				FSyl B; B.Onset = ECons::H; B.Dur = D(0.22f + 0.1f * K); B.P0 = 1.f; B.P1 = 6.f * Rg; B.Curve = 0.8f; B.V0 = VE; B.V1 = VI;
				Add(B);
				break;
			}
			case 3:
			{
				FSyl A; A.Onset = ECons::H; A.Dur = D(0.12f); A.P0 = Rg; A.P1 = 0.f; A.V0 = A.V1 = VA; A.Gain = 0.7f; A.Gap = D(0.06f);
				Add(A);
				FSyl B = A; B.Dur = D(0.22f + 0.1f * K); B.P0 = 4.f * Rg; B.P1 = Rg; B.Gain = 1.f;
				Add(B);
				break;
			}
			default:
			{
				const ECons On[] = { ECons::T, ECons::K, ECons::N, ECons::S };
				const EVowel Vw[] = { VA, VE, VI, VO, VSchwa };
				const int32 N = 3 + FMath::RoundToInt(K);
				for (int32 i = 0; i < N; ++i)
				{
					FSyl S; S.Onset = On[i % 4]; S.Dur = D(i == N - 1 ? 0.16f : 0.08f); S.V0 = S.V1 = Vw[R.RandHelper(5)];
					S.P0 = i == N - 1 ? 4.f * Rg : R.FRandRange(-2.f, 3.f) * Rg; S.P1 = S.P0 - 1.f; S.Gap = D(0.025f);
					Add(S);
				}
			}
			}
			break;
		}
		default:	// CELEBRATE: a rising whoop to a held vowel, wide vibrato, then laughter ("woo-hoo!", "yeah!", "yes! yes!")
		{
			L.Vib *= 3.f;
			switch (Take)
			{
			case 0:
			{
				FSyl A; A.Dur = D(0.18f); A.P0 = 0.f; A.P1 = 2.f * Rg; A.V0 = A.V1 = VU; A.Gap = D(0.03f);
				Add(A);
				FSyl B; B.Onset = ECons::H; B.Dur = D(0.45f + 0.5f * K); B.P0 = 2.f * Rg; B.P1 = 7.f * Rg; B.Curve = 0.5f; B.V0 = VU; B.V1 = VO;
				Add(B);
				break;
			}
			case 1: { FSyl S; S.Dur = D(0.5f + 0.5f * K); S.P0 = 0.f; S.P1 = 6.f * Rg; S.Curve = 0.5f; S.V0 = VI; S.V1 = VA; Add(S); break; }
			case 2: { FSyl S; S.Dur = D(0.55f + 0.5f * K); S.P0 = -1.f; S.P1 = 8.f * Rg; S.Curve = 0.5f; S.V0 = VU; S.V1 = VO; Add(S); break; }
			case 3:
				for (int32 i = 0; i < 2; ++i)
				{
					FSyl S; S.Dur = D(0.18f); S.P0 = 2.f * Rg + 2.f * i; S.P1 = 4.f * Rg + 2.f * i; S.V0 = VI; S.V1 = VE; S.Coda = ECons::S; S.Gap = D(0.08f);
					Add(S);
				}
				break;
			default:
			{
				FSyl A; A.Onset = ECons::H; A.Dur = D(0.2f); A.P0 = 2.f * Rg; A.P1 = 3.f * Rg; A.V0 = A.V1 = VE; A.Gap = D(0.04f);
				Add(A);
				FSyl B; B.Dur = D(0.4f + 0.4f * K); B.P0 = 3.f * Rg; B.P1 = 7.f * Rg; B.Curve = 0.5f; B.V0 = VI; B.V1 = VA;
				Add(B);
			}
			}
			if (K > 0.5f) Laugh(FMath::Min(4, 1 + FMath::RoundToInt(6.f * (K - 0.5f))), VA, 4.f * Rg);
		}
		}
		return L;
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

	float Num(const TSharedPtr<FJsonObject>& O, const TCHAR* Field, float Default)
	{
		double V = Default;
		return O.IsValid() && O->TryGetNumberField(Field, V) ? (float)V : Default;
	}
}

FSlapVoiceProfile FSlapVoiceProfile::FromName(const FString& Card)
{
	const uint32 H = FCrc::StrCrc32(*Card.ToLower());
	FSlapVoiceProfile P;
	P.PitchHz = 85.f + (float)(H % 176);						// 85 .. 260 Hz
	P.FormantScale = FMath::Clamp(1.f + (P.PitchHz - 110.f) / 650.f, 0.92f, 1.25f);
	P.Rasp = 0.1f + 0.5f * (float)((H >> 8) % 101) / 100.f;
	P.Breath = 0.12f + 0.2f * (float)((H >> 16) % 101) / 100.f;
	P.VibratoHz = 4.5f + 2.f * (float)((H >> 24) % 101) / 100.f;
	return P;
}

bool FSlapVoiceManifest::Load()
{
	bLoaded = true;
	bValid = false;
	Profiles.Reset();
	Imported.Reset();
	Clips = 0;
	TSharedPtr<FJsonObject> J;
	for (const FString& Path : { FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Art/Exports/Audio/Voice/voices.json")),
		FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Data/voices.json")) })
	{
		FString Text;
		if (FFileHelper::LoadFileToString(Text, *Path) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), J) && J) break;
		J.Reset();
	}
	FString Schema;
	const TArray<TSharedPtr<FJsonValue>>* Chars = nullptr;
	if (!J || !J->TryGetStringField(TEXT("schema"), Schema) || Schema != TEXT("po-voice-manifest/1") || !J->TryGetArrayField(TEXT("characters"), Chars)) return false;
	for (const TSharedPtr<FJsonValue>& CV : *Chars)
	{
		const TSharedPtr<FJsonObject>* C;
		FString Id;
		if (!CV->TryGetObject(C) || !(*C)->TryGetStringField(TEXT("id"), Id)) continue;
		const TSharedPtr<FJsonObject>* VO = nullptr;
		FSlapVoiceProfile P = FSlapVoiceProfile::FromName(Id);
		if ((*C)->TryGetObjectField(TEXT("voice"), VO))
		{
			P.PitchHz = FMath::Clamp(Num(*VO, TEXT("pitchHz"), P.PitchHz), 50.f, 600.f);
			P.FormantScale = FMath::Clamp(Num(*VO, TEXT("formantScale"), P.FormantScale), 0.7f, 1.6f);
			P.Press = FMath::Clamp(Num(*VO, TEXT("press"), P.Press), 0.f, 1.f);
			P.Breath = FMath::Clamp(Num(*VO, TEXT("breath"), P.Breath), 0.f, 1.f);
			P.Rasp = FMath::Clamp(Num(*VO, TEXT("rasp"), P.Rasp), 0.f, 1.f);
			P.VibratoHz = FMath::Clamp(Num(*VO, TEXT("vibratoHz"), P.VibratoHz), 0.f, 12.f);
			P.VibratoDepth = FMath::Clamp(Num(*VO, TEXT("vibratoDepth"), P.VibratoDepth), 0.f, 0.2f);
			P.Tempo = FMath::Clamp(Num(*VO, TEXT("tempo"), P.Tempo), 0.5f, 2.f);
		}
		Profiles.Add(Id, P);
		const TSharedPtr<FJsonObject>* ClipsObj;
		if (!(*C)->TryGetObjectField(TEXT("clips"), ClipsObj)) continue;
		for (const auto& Emotion : (*ClipsObj)->Values)
		{
			const TArray<TSharedPtr<FJsonValue>>* Levels;
			if (!Emotion.Value->TryGetArray(Levels)) continue;
			for (const TSharedPtr<FJsonValue>& LV : *Levels)
			{
				const TSharedPtr<FJsonObject>* LO;
				const TArray<TSharedPtr<FJsonValue>>* Takes;
				if (!LV->TryGetObject(LO) || !(*LO)->TryGetArrayField(TEXT("takes"), Takes)) continue;
				for (const TSharedPtr<FJsonValue>& TV : *Takes)
				{
					const TSharedPtr<FJsonObject>* TO;
					FString Asset;
					bool bImported = false, bExists = false;
					if (!TV->TryGetObject(TO) || !(*TO)->TryGetStringField(TEXT("asset"), Asset)) continue;
					++Clips;
					// a real recording, imported: a generated placeholder (exists false) is synthesised instead
					if ((*TO)->TryGetBoolField(TEXT("exists"), bExists) && bExists && (*TO)->TryGetBoolField(TEXT("imported"), bImported) && bImported)
						Imported.Add(Asset);
				}
			}
		}
	}
	bValid = Profiles.Num() > 0;
	return bValid;
}

FSlapVoiceProfile FSlapVoiceManifest::ProfileFor(const FString& Card) const
{
	const FSlapVoiceProfile* P = Profiles.Find(Card);
	return P ? *P : FSlapVoiceProfile::FromName(Card);
}

TArray<float> USlapVoiceWave::Render(const FSlapVoiceProfile& Voice, const FString& Card, int32 Emotion, int32 Take, int32 Level)
{
	Emotion = FMath::Clamp(Emotion, 0, 4);
	Take = FMath::Clamp(Take, 1, 5);
	Level = FMath::Clamp(Level, 1, 5);
	const float K = (Level - 1) / 4.f;
	FRandomStream R((int32)FCrc::StrCrc32(*FString::Printf(TEXT("%s/%d/%d/%d"), *Card.ToLower(), Emotion, Take, Level)));
	const FLine L = Plan(Emotion, Take - 1, K, Level, Voice, R);
	const float F0 = Voice.PitchHz * FMath::Pow(2.f, (3.f * K + R.FRandRange(-0.7f, 0.7f)) / 12.f);	// a shout sits higher

	FTract Tr;
	FGlottis G;
	TArray<float> Out;
	for (int32 s = 0; s < L.Syl.Num(); ++s)
	{
		const FSyl& S = L.Syl[s];
		const TArray<float> Vowel = Voiced(S, L, F0, Voice, Tr, G, R);
		const float Ref = Peak(Vowel);
		if (s == 0 && L.Inhale > 0.f)
		{
			Append(Out, Consonant(ECons::None, L.Inhale, VA, F0, Voice, R), 0.35f * Ref);
			Out.AddZeroed(FMath::RoundToInt(0.05f * Fs));
		}
		if (S.Onset != ECons::None) Append(Out, Consonant(S.Onset, 0.f, S.V0, F0 * FMath::Pow(2.f, S.P0 / 12.f), Voice, R), ConsonantLevel(S.Onset) * Ref);
		Append(Out, Vowel, 1.f);
		if (S.Coda != ECons::None) Append(Out, Consonant(S.Coda, 0.f, S.V1, F0, Voice, R), ConsonantLevel(S.Coda) * Ref * 0.8f);
		Out.AddZeroed(FMath::Max(0, FMath::RoundToInt(S.Gap * Fs)));
	}
	// trailing silence off, DC out (one-pole high-pass), then the level's peak (0.30 .. 0.94, as the recorded placeholders)
	while (Out.Num() > 1 && Out.Last() == 0.f) Out.Pop(EAllowShrinking::No);
	float X1 = 0.f, Y1 = 0.f;
	for (float& X : Out) { const float Y = X - X1 + 0.995f * Y1; X1 = X; Y1 = Y; X = Y; }
	const float Target = 0.30f + 0.16f * (Level - 1);
	float P = FMath::Max(Peak(Out), 1e-9f);
	if (K > 0.5f)	// a strong one clips the throat a little
	{
		const float Drive = 1.f + 2.f * (K - 0.5f);
		for (float& X : Out) X = FMath::Tanh(Drive * X / P);
		P = FMath::Max(Peak(Out), 1e-9f);
	}
	for (float& X : Out) X *= Target / P;
	const int32 Tail = FMath::Min(Out.Num(), FMath::RoundToInt(0.012f * Fs));
	for (int32 i = 0; i < Tail; ++i) Out[Out.Num() - Tail + i] *= 1.f - (float)i / Tail;
	return Out;
}

USlapVoiceWave* USlapVoiceWave::Synthesize(UObject* Outer, const FSlapVoiceProfile& Voice, const FString& Card, int32 Emotion, int32 Take, int32 Level)
{
	TArray<float> Samples = Render(Voice, Card, Emotion, Take, Level);
	const int32 N = Samples.Num();
	USlapVoiceWave* W = NewObject<USlapVoiceWave>(Outer ? Outer : GetTransientPackage());
	W->Pcm = MakeShared<TArray<float>, ESPMode::ThreadSafe>(MoveTemp(Samples));
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
	const double Step = InParams.SampleRate > 0.f ? (double)SynthRate / InParams.SampleRate : 1.0;
	return MakeShared<FSlapVoiceGenerator, ESPMode::ThreadSafe>(Pcm.ToSharedRef(), Step, InParams.NumChannels);
}
