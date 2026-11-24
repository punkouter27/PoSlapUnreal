// Automation tests for the voice matrix's procedural imitation and voices.json. Run:
//   UnrealEditor-Cmd.exe PoSlapUnreal.uproject -ExecCmds="Automation RunTests PoSlap.Voice;Quit" -unattended -nullrhi -nosplash

#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "SlapVoiceSynth.h"

namespace
{
	float PeakOf(const TArray<float>& X)
	{
		float P = 0.f;
		for (float V : X) P = FMath::Max(P, FMath::Abs(V));
		return P;
	}
	float RmsOf(const TArray<float>& X)
	{
		double S = 0.0;
		for (float V : X) S += (double)V * V;
		return X.Num() ? (float)FMath::Sqrt(S / X.Num()) : 0.f;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapVoiceSynthTest, "PoSlap.Voice.ProceduralImitation",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapVoiceSynthTest::RunTest(const FString& Parameters)
{
	static const TCHAR* Emotions[] = { TEXT("HAPPY"), TEXT("ANGRY"), TEXT("HURT"), TEXT("INSULT"), TEXT("CELEBRATE") };
	FSlapVoiceManifest M;
	M.Load();
	const FSlapVoiceProfile Grandpa = M.ProfileFor(TEXT("grandpa")), Kim = M.ProfileFor(TEXT("kim"));
	for (int32 e = 0; e < 5; ++e)
		for (int32 Take = 1; Take <= 5; ++Take)
		{
			const TArray<float> L1 = USlapVoiceWave::Render(Grandpa, TEXT("grandpa"), e, Take, 1);
			const TArray<float> L5 = USlapVoiceWave::Render(Grandpa, TEXT("grandpa"), e, Take, 5);
			const FString Cell = FString::Printf(TEXT("%s take %d"), Emotions[e], Take);
			TestTrue(Cell + TEXT(": level 1 is not silent"), RmsOf(L1) > 0.01f);
			TestTrue(Cell + TEXT(": level 5 is louder than level 1"), PeakOf(L5) > PeakOf(L1) + 0.3f);
			TestTrue(Cell + TEXT(": level 5 is longer than level 1"), L5.Num() > L1.Num());
			TestTrue(Cell + TEXT(": deterministic"), USlapVoiceWave::Render(Grandpa, TEXT("grandpa"), e, Take, 3) == USlapVoiceWave::Render(Grandpa, TEXT("grandpa"), e, Take, 3));
			const TArray<float> G = USlapVoiceWave::Render(Grandpa, TEXT("grandpa"), e, Take, 3), K = USlapVoiceWave::Render(Kim, TEXT("kim"), e, Take, 3);
			double Diff = FMath::Abs(G.Num() - K.Num());
			for (int32 i = 0; i < FMath::Min(G.Num(), K.Num()); ++i) Diff += FMath::Abs(G[i] - K[i]);
			TestTrue(Cell + TEXT(": grandpa and kim sound different"), Diff > 100.0);
		}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapVoiceManifestTest, "PoSlap.Voice.Manifest",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapVoiceManifestTest::RunTest(const FString& Parameters)
{
	FSlapVoiceManifest M;
	TestTrue(TEXT("voices.json reads (scripts/voice_manifest.py)"), M.Load());
	TestEqual(TEXT("5 emotions x 5 levels x 5 takes per card"), M.Clips, M.Profiles.Num() * 125);
	TestTrue(TEXT("the cast is listed"), M.Profiles.Contains(TEXT("grandpa")) && M.Profiles.Contains(TEXT("kim")));
	TestFalse(TEXT("CELEBRATE is not imported yet: synthesised"), M.Imported.Contains(TEXT("S_V_grandpa_CELEBRATE_1_1")));
	TestFalse(TEXT("a generated placeholder is not played (it is synthesised)"), M.Imported.Contains(TEXT("S_V_grandpa_ANGRY_1_1")));
	return true;
}

#endif
