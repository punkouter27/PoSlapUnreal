// Automation tests for the Elo rule every completed bout follows, demo mode included (SlapElo.h). Run:
//   UnrealEditor-Cmd.exe PoSlapUnreal.uproject -ExecCmds="Automation RunTests PoSlap.Elo;Quit" -unattended -nullrhi -nosplash

#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "SlapElo.h"

using SlapElo::EOutcome;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapEloOutcomeTest, "PoSlap.Elo.OnlyCompletedBoutsCount",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapEloOutcomeTest::RunTest(const FString& Parameters)
{
	const int32 Seen[2] = { 3, 2 };
	const int32 Same[2] = { 3, 2 }, RedUp[2] = { 4, 2 }, BlueUp[2] = { 3, 3 };
	TestTrue(TEXT("a running or aborted bout (no result) is not rated"), SlapElo::Outcome(TEXT(""), RedUp, 0, Seen, 0) == EOutcome::Pending);
	TestTrue(TEXT("a completed demo bout: red's win is rated"), SlapElo::Outcome(TEXT("RED wins by KO, round 2"), RedUp, 0, Seen, 0) == EOutcome::RedWins);
	TestTrue(TEXT("blue's win"), SlapElo::Outcome(TEXT("BLUE wins by decision"), BlueUp, 0, Seen, 0) == EOutcome::BlueWins);
	TestTrue(TEXT("a draw"), SlapElo::Outcome(TEXT("Draw"), Same, 1, Seen, 0) == EOutcome::Draw);
	TestTrue(TEXT("a KO line before its win count: wait, do not call it a no contest"), SlapElo::Outcome(TEXT("RED wins by KO, round 1"), Same, 0, Seen, 0) == EOutcome::Pending);
	TestTrue(TEXT("a no contest is not rated"), SlapElo::Outcome(TEXT("No contest (simulation blow-up)"), Same, 0, Seen, 0) == EOutcome::NoContest);
	TestFalse(TEXT("a character never plays itself"), SlapElo::Rated(TEXT("kim"), TEXT("kim")));
	TestFalse(TEXT("an unknown corner is not rated"), SlapElo::Rated(TEXT("kim"), TEXT("")));
	TestTrue(TEXT("two characters are rated"), SlapElo::Rated(TEXT("kim"), TEXT("grandpa")));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSlapEloMathTest, "PoSlap.Elo.Math",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FSlapEloMathTest::RunTest(const FString& Parameters)
{
	double A = SlapElo::Start, B = SlapElo::Start;
	TestEqual(TEXT("even ratings: the winner gains K/2"), SlapElo::Apply(A, B, 1.0), 16.0, 1e-9);
	TestEqual(TEXT("1016 / 984"), A, 1016.0, 1e-9);
	TestEqual(TEXT("zero sum"), A + B, 2000.0, 1e-9);
	double C = 1000.0, D = 1000.0;
	TestEqual(TEXT("a draw between equals moves nothing"), SlapElo::Apply(C, D, 0.5), 0.0, 1e-9);
	double Hi = 1200.0, Lo = 1000.0;
	const double Move = SlapElo::Apply(Hi, Lo, 0.5);
	TestTrue(TEXT("a draw: the higher-rated loses some, the lower gains it"), Move < 0.0 && Lo > 1000.0 && FMath::IsNearlyEqual(Hi + Lo, 2200.0));
	TestEqual(TEXT("the draw is 0.5 against the expectation"), Move, 32.0 * (0.5 - SlapElo::Expected(1200.0, 1000.0)), 1e-9);
	return true;
}

#endif
