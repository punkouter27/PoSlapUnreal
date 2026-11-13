// ASlapHUD: the highlight reel (SlapReel.h). Render side only.
//
// Live: 3 s after every knockout (and after any slap of 9 m/s or more) the controller's last 6 s of physics steps are
// saved to Saved/PoSlap/reels/<stamp>_<red>_vs_<blue>_<why>.reel with a small .json beside it (the level, the cards, the
// palm speed). The newest 20 are kept.
// Playback (-SlapReel=<file> [-SlapReelOut=<dir>] [-SlapReelFrom=<s before the touch>] [-SlapReelTo=<s after>]): the athletes
// are drawn from the file, one recorded step per rendered frame at a fixed 5 ms frame time, a camera move is flown round
// the contact, and with -SlapReelOut every frame is written as a PNG; then it quits. Scripts/highlight_reel.py runs it
// off screen at 4K and encodes the frames at 60 fps: 3.3x slow motion in which every frame is a real physics step.

#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "HAL/FileManager.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "SlapReel.h"
#include "TimerManager.h"
#include "UnrealClient.h"
#include "Widgets/SWidget.h"

using namespace SlapUi;

namespace
{
	constexpr double ReelAfter = 3.0;		// s of game time after the event before the window is saved
	constexpr double ReelBigSlap = 9.0;		// m/s: a slap this fast is saved too
	constexpr int32 ReelKeep = 20, ReelWarm = 90;	// files kept; frames held on the first step while the picture settles
	FString ReelDir() { return FPaths::ProjectSavedDir() / TEXT("PoSlap/reels"); }
}

void ASlapHUD::ReelBegin()
{
	const TCHAR* Cmd = FCommandLine::Get();
	FString File;
	if (!FParse::Value(Cmd, TEXT("SlapReel="), File)) return;
	FSlapReel& R = FSlapReel::Playing();
	if (!R.Load(File))
	{
		UE_LOG(LogSlap, Error, TEXT("SlapHUD: -SlapReel: cannot read %s"), *File);
		return;
	}
	bReel = true;
	FParse::Value(Cmd, TEXT("SlapReelOut="), ReelOut);
	float From = 0.6f, To = 2.4f;
	FParse::Value(Cmd, TEXT("SlapReelFrom="), From);
	FParse::Value(Cmd, TEXT("SlapReelTo="), To);
	const int32 T = R.Touch >= 0 ? R.Touch : R.NumSteps / 2;
	ReelFirst = FMath::Clamp(T - FMath::RoundToInt32(From / R.Timestep), 0, R.NumSteps - 1);
	ReelLast = FMath::Clamp(T + FMath::RoundToInt32(To / R.Timestep), ReelFirst, R.NumSteps - 1);
	FSlapReel::PlayStep = ReelFirst;
	FApp::SetFixedDeltaTime(R.Timestep);	// a frame is a physics step: effects, motion blur and the soft tissue see 5 ms
	FApp::SetUseFixedTimeStep(true);
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: reel %s: %d bodies, %d steps of %.0f ms, touch at step %d (%s strikes); playing steps %d-%d%s"), *File, R.NumBodies, R.NumSteps,
		1000.f * R.Timestep, R.Touch, R.Striker ? TEXT("blue") : TEXT("red"), ReelFirst, ReelLast, ReelOut.IsEmpty() ? TEXT("") : *(TEXT(", frames to ") + ReelOut));
}

void ASlapHUD::ReelWatch(const FSlapHud& H, double Now)
{
	// live: note the moments worth a reel, save the window once it has run on
	USlapController* C = Controller.Get();
	if (!C || bReel) return;
	const bool bKO = H.KOSeq != ReelSeenKO, bSlap = H.StrikeSeq != ReelSeenStrike && H.Speed >= ReelBigSlap;
	if (ReelSeenKO != INT32_MIN && H.Match > 0 && (bKO || bSlap))
	{
		ReelSaveAt = Now + ReelAfter;
		ReelWhy = bKO ? TEXT("ko") : TEXT("slap");
		ReelSpeed = H.Speed;
	}
	ReelSeenKO = H.KOSeq;
	ReelSeenStrike = H.StrikeSeq;
	if (ReelSaveAt < 0.0 || Now < ReelSaveAt) return;
	ReelSaveAt = -1.0;
	FSlapReel R;
	C->TakeReel(R);
	if (!R.IsValid() || R.Touch < 0) return;
	IFileManager& FM = IFileManager::Get();
	const FString Base = ReelDir() / FString::Printf(TEXT("%s_%s_vs_%s_%s"), *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S")), *H.Card[0], *H.Card[1], *ReelWhy);
	if (!R.Save(Base + TEXT(".reel"))) return;
	const FString Level = UGameplayStatics::GetCurrentLevelName(this, true);
	FFileHelper::SaveStringToFile(FString::Printf(TEXT("{\"level\": \"/Game/Levels/%s\", \"red\": \"%s\", \"blue\": \"%s\", \"why\": \"%s\", \"striker\": \"%s\", \"speed\": %.2f, \"steps\": %d, \"touch\": %d, \"timestep\": %.4f}\n"),
		*Level, *H.Card[0], *H.Card[1], *ReelWhy, R.Striker ? TEXT("blue") : TEXT("red"), ReelSpeed, R.NumSteps, R.Touch, R.Timestep), *(Base + TEXT(".json")));
	++ReelsSaved;
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: reel saved: %s.reel (%s, %.1f m/s, %d steps, touch at %d)"), *Base, *ReelWhy, ReelSpeed, R.NumSteps, R.Touch);
	TArray<FString> Old;	// the newest ReelKeep stay (the names sort by time)
	FM.FindFiles(Old, *(ReelDir() / TEXT("*.reel")), true, false);
	Old.Sort();
	for (int32 i = 0; i + ReelKeep < Old.Num(); ++i)
	{
		FM.Delete(*(ReelDir() / Old[i]));
		FM.Delete(*(ReelDir() / FPaths::ChangeExtension(Old[i], TEXT("json"))));
	}
}

void ASlapHUD::ReelFrame()
{
	// playback: one recorded physics step per frame; the camera flies the contact; every frame may be written out
	const FSlapReel& R = FSlapReel::Playing();
	if (Ui.IsValid() && Ui->GetVisibility() != EVisibility::Collapsed) Ui->SetVisibility(EVisibility::Collapsed);	// a clean picture: no menu, no FPS counter
	CacheAthletes();
	if (!Camera.IsValid() || !Body[0].IsValid() || !Body[1].IsValid() || Curtain()) return;
	if (ReelWarmed < ReelWarm) ++ReelWarmed;	// shaders, exposure and the temporal history settle on the first step
	else if (ReelShot > 0 && !bReelDone) ++FSlapReel::PlayStep;
	const int32 Rs = FMath::Min(FSlapReel::PlayStep, ReelLast);
	FSlapReel::PlayStep = Rs;
	const int32 Def = 1 - FMath::Clamp(R.Striker, 0, 1);
	const double Tau = (Rs - (R.Touch >= 0 ? R.Touch : ReelFirst)) * (double)R.Timestep;	// s from the touch
	const double U = (Rs - ReelFirst) / double(FMath::Max(ReelLast - ReelFirst, 1));		// 0..1 through the clip

	// the camera: a two-shot that closes on the contact, holds the struck head, then pulls out as he goes
	ACameraActor* Cam = Camera.Get();
	if (!bHaveBase) { BaseRot = Cam->GetActorRotation(); bHaveBase = true; }
	const FVector Head[2] = { Body[0]->GetBoneLocation(BoneHead), Body[1]->GetBoneLocation(BoneHead) };
	const FVector Pelvis = Body[Def]->GetBoneLocation(BonePelvis);
	const float In = FMath::SmoothStep(-0.5f, -0.05f, (float)Tau), Away = FMath::SmoothStep(0.5f, 1.6f, (float)Tau);
	FVector Look = FMath::Lerp(0.5f * (Head[0] + Head[1]) - FVector(0.f, 0.f, 8.f), Head[Def], In);
	Look = FMath::Lerp(Look, 0.5f * (Head[Def] + Pelvis), Away);
	const float Yaw = (Def ? -1.f : 1.f) * FMath::Lerp(34.f, -12.f, (float)U);
	const float Dist = FMath::Lerp(FMath::Lerp(175.f, 112.f, In), 220.f, Away);
	const FVector Pos = Look - BaseRot.Vector().RotateAngleAxis(Yaw, FVector::UpVector) * Dist + FVector(0.f, 0.f, FMath::Lerp(6.f, 28.f, Away));
	Cam->SetActorLocationAndRotation(Pos, (Look - Pos).Rotation());
	FPostProcessSettings& PP = Cam->GetCameraComponent()->PostProcessSettings;
	PP.bOverride_DepthOfFieldFocalDistance = PP.bOverride_DepthOfFieldFstop = true;
	PP.DepthOfFieldFocalDistance = Dist;
	PP.DepthOfFieldFstop = 2.8f;
	Jiggle(Last);

	// the caption: what the frame is, in numbers (the recorded telemetry rides with every step)
	const float* F = R.Frame(Rs) + R.NumBodies * 7;
	const float Y = Canvas->ClipY - 54.f * S;
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 0.f, Y - 6.f * S, Canvas->ClipX, 60.f * S);
	Text(FString::Printf(TEXT("PALM %.1f m/s     HEAD %.0f g     %+.3f s"), F[0], F[1], Tau), Canvas->ClipX * 0.5f, Y, Gold, 1.5f * S, true, GEngine->GetMediumFont());
	Text(FString::Printf(TEXT("SUPER SLOW MOTION   every frame is one MuJoCo step (%.0f ms)"), 1000.f * R.Timestep), Canvas->ClipX * 0.5f, Y + 24.f * S, Grey, 0.9f * S, true);
	const FString Stamp = TEXT(POSLAP_BUILD_VERSION);	// the build stamp, bottom right (the Slate anchor is hidden here)
	float Tw = 0.f, Th = 0.f;
	Canvas->StrLen(GEngine->GetSmallFont(), Stamp, Tw, Th);
	Text(Stamp, Canvas->ClipX - 6.f * S - Tw * 0.9f * S, Y + 38.f * S, Grey, 0.9f * S);

	if (ReelWarmed < ReelWarm || bReelDone) return;
	if (!ReelOut.IsEmpty()) FScreenshotRequest::RequestScreenshot(ReelOut / FString::Printf(TEXT("frame_%05d.png"), ReelShot), false, false);
	++ReelShot;
	if (Rs >= ReelLast)
	{
		bReelDone = true;
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: reel done: %d frames (steps %d-%d)"), ReelShot, ReelFirst, ReelLast);
		FTimerHandle Quit;	// the last frame's file is written at the end of this frame
		GetWorldTimerManager().SetTimer(Quit, FTimerDelegate::CreateWeakLambda(this, [this] { UKismetSystemLibrary::QuitGame(this, nullptr, EQuitPreference::Quit, false); }), 0.05f, false);
	}
}
