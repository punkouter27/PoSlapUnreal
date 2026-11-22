// Automation tests for the diagnostics overlay, the idle demo mode and the editor-first config (Po Game Standard
// C9 / C10 / C12 / C13). Run: UnrealEditor-Cmd.exe PoSlapUnreal.uproject -ExecCmds="Automation RunTests PoSlap.Showcase;Quit"
//   -unattended -nullrhi -nosplash   (docs/showcase.md)

#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "SlapRewardConfig.h"
#include "SlapShowcase.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "SlapMuJoCo.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapShowcaseIdleTest, "PoSlap.Showcase.IdleDemoIgnoresLongFrames",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapShowcaseIdleTest::RunTest(const FString& Parameters)
{
	USlapShowcaseSubsystem* S = NewObject<USlapShowcaseSubsystem>();
	const USlapShowcaseConfig* Cfg = USlapShowcaseSettings::GetShowcaseConfig();
	TestEqual(TEXT("idle threshold is 30 s"), Cfg->IdleSeconds, 30.f);

	S->AdvanceClocks(70.f);   // one 70 s hitch (shader warm-up): not idle time
	TestFalse(TEXT("a 70 s frame does not start demo mode"), S->IsDemoActive());
	TestEqual(TEXT("a 70 s frame adds no idle time"), S->GetIdleSeconds(), 0.f);

	for (int32 i = 0; i < 20 * 60; ++i) S->AdvanceClocks(1.f / 60.f);
	TestFalse(TEXT("20 s of 60 fps frames: still no demo"), S->IsDemoActive());
	for (int32 i = 0; i < 11 * 60; ++i) S->AdvanceClocks(1.f / 60.f);
	TestTrue(TEXT("31 s of 60 fps frames: demo mode"), S->IsDemoActive());

	S->NotifyUserInput();
	TestFalse(TEXT("input leaves demo mode"), S->IsDemoActive());
	TestEqual(TEXT("input restarts the idle clock"), S->GetIdleSeconds(), 0.f);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapShowcaseOverlayTest, "PoSlap.Showcase.OverlayAndSimBenchmark",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapShowcaseOverlayTest::RunTest(const FString& Parameters)
{
	USlapShowcaseSubsystem* S = NewObject<USlapShowcaseSubsystem>();
	S->ReportEpisodeReward(12.5f);
	S->ReportInferenceMs(0.4f);
	S->ReportInferenceMs(0.6f);
	S->SetCurriculumStage(TEXT("L4 (grandpa_L4)"));
	S->AdvanceClocks(1.f / 60.f);
	const FString Text = S->BuildOverlayText();
	for (const TCHAR* Row : { TEXT("FPS"), TEXT("Episode reward           12.50"), TEXT("Inference latency        0.50 ms"),
		TEXT("Curriculum stage         L4 (grandpa_L4)"), TEXT("Sim benchmark") })
	{
		TestTrue(FString::Printf(TEXT("overlay shows '%s'"), Row), Text.Contains(Row));
	}

	// The benchmark steps the same arena model the game imports (training/models, the one MuJoCo 3.10.0 trainers use).
	const FString Xml = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("training/models/grandpa_vs_kim_arena.xml"));
	char Err[1000] = "";
	mjModel* M = mj_loadXML(TCHAR_TO_UTF8(*Xml), nullptr, Err, sizeof(Err));
	if (!TestNotNull(*FString::Printf(TEXT("arena model loads (%s)"), UTF8_TO_TCHAR(Err)), M)) return false;
	const FSlapSimBenchmark B = USlapShowcaseSubsystem::StepForBudget(M, 0.5);
	mj_deleteModel(M);
	TestTrue(TEXT("benchmark stepped"), B.Steps > 0);
	TestTrue(TEXT("benchmark ran for its budget"), B.WallSeconds >= 0.5 && B.WallSeconds < 5.0);
	TestTrue(TEXT("steps/s and real-time factor are positive"), B.StepsPerSecond() > 0.0 && B.RealTimeFactor() > 0.0);
	const FString Line = USlapShowcaseSubsystem::FormatBenchmark(B);
	TestTrue(TEXT("result names its units and what it measures"), Line.Contains(TEXT("physics steps/s")) && Line.Contains(TEXT("CPU MuJoCo"))
		&& Line.Contains(TEXT("training runs MuJoCo Warp on the GPU")));
	AddInfo(FString::Printf(TEXT("sim benchmark: %d steps in %.2f s = %.0f steps/s (%.1fx real time)"), B.Steps, B.WallSeconds, B.StepsPerSecond(), B.RealTimeFactor()));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapShowcaseConfigTest, "PoSlap.Showcase.ConfigDataAssets",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapShowcaseConfigTest::RunTest(const FString& Parameters)
{
	// Project Settings > Game > Slap Showcase picks the two DataAssets (Config/DefaultGame.ini)
	const USlapShowcaseSettings* Settings = GetDefault<USlapShowcaseSettings>();
	TestEqual(TEXT("showcase DataAsset selected"), Settings->ShowcaseConfig.ToSoftObjectPath().ToString(),
		FString(TEXT("/Game/Project/DataAssets/DA_ShowcaseConfig.DA_ShowcaseConfig")));
	TestEqual(TEXT("reward DataAsset selected"), Settings->RewardConfig.ToSoftObjectPath().ToString(),
		FString(TEXT("/Game/Project/DataAssets/DA_RewardConfig.DA_RewardConfig")));
	const USlapShowcaseConfig* Show = USlapShowcaseSettings::GetShowcaseConfig();
	const USlapRewardConfig* Reward = USlapShowcaseSettings::GetRewardConfig();
	if (!TestTrue(TEXT("showcase settings come from the asset"), Show && Show->GetPathName() == TEXT("/Game/Project/DataAssets/DA_ShowcaseConfig.DA_ShowcaseConfig"))) return false;
	if (!TestTrue(TEXT("reward settings come from the asset"), Reward && Reward->GetPathName() == TEXT("/Game/Project/DataAssets/DA_RewardConfig.DA_RewardConfig"))) return false;

	// The reward asset mirrors training/configs/reward_config.json (what the trainer reads)
	FString Raw;
	const FString Json = FPaths::ProjectDir() / TEXT("training/configs/reward_config.json");
	if (!TestTrue(TEXT("reward_config.json reads"), FFileHelper::LoadFileToString(Raw, *Json))) return false;
	TSharedPtr<FJsonObject> Root;
	if (!TestTrue(TEXT("reward_config.json parses"), FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Raw), Root) && Root.IsValid())) return false;
	const TSharedPtr<FJsonObject> Pen = Root->GetObjectField(TEXT("penalties"));
	// the asset stores floats: equal to the JSON's doubles within float precision (relative 1e-6)
	auto Same = [this](const TCHAR* What, float Asset, double Json)
	{
		TestTrue(FString::Printf(TEXT("%s: asset %g == reward_config.json %g"), What, Asset, Json), FMath::Abs(Asset - Json) <= 1e-6 * FMath::Max(1e-12, FMath::Abs(Json)));
	};
	Same(TEXT("torque_sq"), Reward->TorqueSqWeight, Pen->GetNumberField(TEXT("torque_sq")));
	Same(TEXT("joint_ang_vel_sq"), Reward->JointAngVelSqWeight, Pen->GetNumberField(TEXT("joint_ang_vel_sq")));
	Same(TEXT("action_rate_sq"), Reward->ActionRateSqWeight, Pen->GetNumberField(TEXT("action_rate_sq")));
	Same(TEXT("foot_slip"), Reward->FootSlipWeight, Pen->GetNumberField(TEXT("foot_slip")));
	const TArray<TSharedPtr<FJsonValue>>& Stages = Root->GetArrayField(TEXT("curriculum"));
	if (TestEqual(TEXT("curriculum stage count"), Reward->CurriculumStages.Num(), Stages.Num()))
	{
		for (int32 i = 0; i < Stages.Num(); ++i)
		{
			const TSharedPtr<FJsonObject> J = Stages[i]->AsObject();
			TestEqual(FString::Printf(TEXT("stage %d name"), i), Reward->CurriculumStages[i].Stage, J->GetStringField(TEXT("stage")));
			Same(*FString::Printf(TEXT("stage %d threshold"), i), Reward->CurriculumStages[i].Threshold, J->GetNumberField(TEXT("threshold")));
		}
	}
	TestEqual(TEXT("demo idle threshold"), Show->IdleSeconds, 30.f);
	TestTrue(TEXT("demo cameras listed"), Show->DemoCameras.Num() >= 2);
	return true;
}

#endif
