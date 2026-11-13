// ASlapHUD: the broadcast pass of 2026-11-12, stage side. Render side only: nothing here reaches the physics, the brains
// or the timing, and everything is allocated once at startup (BuildStage), never spawned or destroyed in a match.
//
//   the jumbotron   a screen hung behind the podium shows a second camera (a scene capture at 30 Hz): the introduced
//                   fighter on the walk-in, the defender's face while he waits, the struck face after the touch, the
//                   fallen from above after a KO, the winner. HIGH tier and up, desktop only (-SlapJumbo=0: off).
//   3D titles       Text3D letters in the arena: each fighter's name over his corner on the walk-in and for the winner,
//                   and the big caption (ROUND 1, KNOCKDOWN, KNOCKOUT) standing behind the podium (-SlapTitles=0: off).
//   Sequencer       authored camera moves (Scripts/broadcast_pass.py -> /Game/Cinematics/LS_*): a crane down as the
//                   walk-in begins, a crane up from the fallen on a KO, a low hero push on the winner. AUTO camera only
//                   (-SlapSeq=0: off). The spawned Cine Camera takes the level camera's grade and is focused here.
//   soft tissue     the belly and the jowl lag the bones on a damped spring and are drawn as a world-position offset in
//                   the skin material (JiggleA/B...). The bones still mirror MuJoCo exactly (-SlapJiggle=0: off).

#include "SlapHUD.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "CineCameraActor.h"
#include "CineCameraComponent.h"
#include "Components/PoseableMeshComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "DefaultLevelSequenceInstanceData.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Extensions/Text3DDefaultMaterialExtension.h"
#include "GameFramework/PlayerController.h"
#include "LevelSequence.h"
#include "LevelSequenceActor.h"
#include "LevelSequencePlayer.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "Text3DComponent.h"

using namespace SlapUi;

namespace
{
	const FName BoneBelly("belly");
	const FName PScreen("Screen"), PJigA("JiggleA"), PJigAV("JiggleAV"), PJigAR("JiggleAR"), PJigB("JiggleB"), PJigBV("JiggleBV"), PJigBR("JiggleBR");
	constexpr float JumboW = 272.f, JumboH = 153.f;	// cm, 16:9
	constexpr float JumboBack = 320.f, JumboZ = 212.f;	// cm behind the podium (away from the broadcast side), centre height
	constexpr double JumboRate = 30.0;					// captures per second
	const TCHAR* const SeqName[] = { TEXT("LS_Intro"), TEXT("LS_KO_Red"), TEXT("LS_KO_Blue"), TEXT("LS_Winner_Red"), TEXT("LS_Winner_Blue") };
	constexpr float SeqFocal[] = { 16.f, 18.f, 18.f, 24.f, 24.f };	// mm, on the portrait gate below (Scripts/broadcast_pass.py add_shots)
	constexpr float GateW = 13.365f, GateH = 23.76f;				// mm: a Super 35 gate on its side, 9:16 as the level camera

	bool Flag(const TCHAR* Key, bool bDefault = true)
	{
		int32 V = bDefault ? 1 : 0;
		FParse::Value(FCommandLine::Get(), Key, V);
		return V != 0;
	}
	float FlagF(const TCHAR* Key, float Default)
	{
		FParse::Value(FCommandLine::Get(), Key, Default);
		return Default;
	}
}

void ASlapHUD::BuildStage()
{
	const bool bDesktop = !PLATFORM_ANDROID && !PLATFORM_IOS;
	// an AHUD is a hidden actor, and a hidden actor's primitives are never drawn: the screen and the letters live on a
	// plain actor of their own, spawned here once (startup allocation; nothing is spawned or destroyed during a match)
	StageActor = GetWorld()->SpawnActor<AActor>();
	if (!StageActor) return;
	AActor* Host = StageActor;

	// ---- the jumbotron: the panel, its render target and the second camera
	UMaterialInterface* Mat = Load<UMaterialInterface>(TEXT("/Game/FX/M_Jumbotron.M_Jumbotron"));
	UStaticMesh* Plane = Load<UStaticMesh>(TEXT("/Engine/BasicShapes/Plane.Plane"));
	if (bDesktop && Flag(TEXT("SlapJumbo=")) && Mat && Plane)
	{
		JumboTarget = NewObject<UTextureRenderTarget2D>(Host, TEXT("JumboTarget"));
		JumboTarget->RenderTargetFormat = RTF_RGBA8_SRGB;
		JumboTarget->ClearColor = FLinearColor::Black;
		JumboTarget->InitAutoFormat(640, 360);
		JumboTarget->UpdateResourceImmediate(true);
		JumboScreen = NewObject<UStaticMeshComponent>(Host, TEXT("JumboScreen"));
		JumboScreen->SetStaticMesh(Plane);
		JumboScreen->SetMobility(EComponentMobility::Movable);
		JumboScreen->SetCollisionEnabled(ECollisionEnabled::NoCollision);	// Chaos stays out
		JumboScreen->SetCastShadow(false);
		JumboSkin = UMaterialInstanceDynamic::Create(Mat, Host);
		JumboSkin->SetTextureParameterValue(PScreen, JumboTarget);
		JumboSkin->SetScalarParameterValue(TEXT("Glow"), 2.f);
		JumboScreen->SetMaterial(0, JumboSkin);
		JumboScreen->SetVisibility(false);
		JumboScreen->RegisterComponent();
		JumboCam = NewObject<USceneCaptureComponent2D>(Host, TEXT("JumboCam"));
		JumboCam->TextureTarget = JumboTarget;
		JumboCam->CaptureSource = SCS_FinalColorLDR;
		JumboCam->bCaptureEveryFrame = false;	// captured by hand at JumboRate (Jumbo)
		JumboCam->bCaptureOnMovement = false;
		JumboCam->bAlwaysPersistRenderingState = true;
		JumboCam->FOVAngle = 24.f;
		JumboCam->HiddenComponents.Add(JumboScreen);	// no screen inside the screen
		JumboCam->RegisterComponent();
	}

	// ---- the 3D titles
	if (Flag(TEXT("SlapTitles=")))
	{
		auto Make = [Host](const TCHAR* Name, float Extrude, float MaxWidth)
		{
			UText3DComponent* T = NewObject<UText3DComponent>(Host, Name);
			T->SetMobility(EComponentMobility::Movable);
			T->SetExtrude(Extrude);
			T->SetBevel(0.6f);
			T->SetHorizontalAlignment(EText3DHorizontalTextAlignment::Center);
			T->SetVerticalAlignment(EText3DVerticalTextAlignment::Center);
			T->SetHasMaxWidth(true);	// a long caption shrinks to the gap it stands in
			T->SetMaxWidth(MaxWidth);
			T->SetScaleProportionally(true);
			T->SetCastShadow(false);
			T->SetVisibility(false, true);
			T->RegisterComponent();
			return T;
		};
		for (int32 a = 0; a < 2; ++a) NameTitle[a] = Make(a ? TEXT("TitleBlue") : TEXT("TitleRed"), 6.f, 150.f);
		BigTitle = Make(TEXT("TitleBig"), 10.f, 190.f);
		TitleYaw = FlagF(TEXT("SlapTitleYaw="), TitleYaw);
		TitleSize = FlagF(TEXT("SlapTitleSize="), TitleSize);
	}

	// ---- the Sequencer shots: one player per sequence, made now, played when the match calls for it
	if (Flag(TEXT("SlapSeq=")))
	{
		for (int32 k = 0; k < NumSeq; ++k)
		{
			ULevelSequence* Seq = LoadNamed<ULevelSequence>(TEXT("/Game/Cinematics"), SeqName[k]);
			if (!Seq) continue;
			ALevelSequenceActor* Actor = nullptr;
			ULevelSequencePlayer* Player = ULevelSequencePlayer::CreateLevelSequencePlayer(this, Seq, FMovieSceneSequencePlaybackSettings(), Actor);
			if (!Player || !Actor) continue;
			Actor->bOverrideInstanceData = true;	// the transform origin: the podium, or the winner's own spot
			SeqActor[k] = Actor;
			++SeqLoaded;
		}
	}
	bJiggle = Flag(TEXT("SlapJiggle="));
	UE_LOG(LogSlap, Log, TEXT("SlapHUD: stage pass: jumbotron %s, 3D titles %s, sequences %d/%d, soft tissue %s"),
		JumboCam ? TEXT("yes") : (Mat ? TEXT("off") : TEXT("off (no M_Jumbotron: run Scripts/broadcast_pass.py)")),
		BigTitle ? TEXT("yes") : TEXT("off"), SeqLoaded, (int32)NumSeq, bJiggle ? TEXT("on") : TEXT("off"));
}

// ---- the jumbotron ---------------------------------------------------------------------------------------------------

void ASlapHUD::Jumbo(const FSlapHud& H, double Now)
{
	if (!JumboCam || !JumboScreen) return;
	const bool bOn = TierLive >= 2 && H.Match > 0 && Body[0].IsValid() && Body[1].IsValid();
	if (JumboScreen->IsVisible() != bOn) JumboScreen->SetVisibility(bOn);
	if (!bOn) return;
	const FVector Head[2] = { Body[0]->GetBoneLocation(BoneHead), Body[1]->GetBoneLocation(BoneHead) };
	const FVector Pelvis[2] = { Body[0]->GetBoneLocation(BonePelvis), Body[1]->GetBoneLocation(BonePelvis) };
	if (!bJumboPlaced)	// once: behind the podium, facing the broadcast side (+Y); the image's right is the viewer's right (+X)
	{
		bJumboPlaced = true;
		StageCentre = FVector(0.5f * (Pelvis[0].X + Pelvis[1].X), 0.5f * (Pelvis[0].Y + Pelvis[1].Y), 0.f);
		const FQuat Face = FRotationMatrix::MakeFromXY(FVector(1.f, 0.f, 0.f), FVector(0.f, 0.f, -1.f)).ToQuat();
		JumboScreen->SetWorldTransform(FTransform(Face, StageCentre + FVector(0.f, -FlagF(TEXT("SlapJumboBack="), JumboBack), FlagF(TEXT("SlapJumboZ="), JumboZ)),
			FVector(JumboW / 100.f, JumboH / 100.f, 1.f)));
		if (Camera.IsValid())	// the level camera's grade (its exposure is metered on the lit stage: Scripts/arena_env.py)
		{
			JumboCam->PostProcessSettings = Camera->GetCameraComponent()->PostProcessSettings;
			JumboCam->PostProcessBlendWeight = 1.f;
			JumboCam->PostProcessSettings.bOverride_DepthOfFieldFocalDistance = false;	// the second camera stays sharp and ungraded by the KO
			JumboCam->PostProcessSettings.bOverride_ColorSaturation = JumboCam->PostProcessSettings.bOverride_VignetteIntensity = false;
			JumboCam->PostProcessSettings.bOverride_SceneFringeIntensity = false;
		}
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: jumbotron at %s (%.0f x %.0f cm)"), *JumboScreen->GetComponentLocation().ToString(), JumboW, JumboH);
	}
	const double Real = FPlatformTime::Seconds();	// real time: the screen keeps its 30 Hz through slow motion
	if (Real - JumboAt < 1.0 / JumboRate) return;
	const float Dt = FMath::Min(float(Real - JumboAt), 0.1f);
	JumboAt = Real;

	// what the second camera shows. Always from the broadcast side (+Y), so left and right match the main picture
	const int32 Def = Team(H.Defender), Str = 1 - Def;
	const FVector Up = FVector::UpVector, Out(0.f, 1.f, 0.f);
	FVector Look, Pos;
	float Fov = 24.f;
	if (!H.Result.IsEmpty() && H.Winner >= 0 && Now - KOAt >= KOShot)	// the winner, chest up
	{
		const int32 W = H.Winner;
		Look = Head[W] - Up * 14.f;
		Pos = Look + Out * 170.f + FVector(W ? -60.f : 60.f, 0.f, 8.f);
	}
	else if (Now - KOAt < KOShot)	// the fallen, from above
	{
		Look = Pelvis[Struck];
		Pos = Look + Up * 260.f + Out * 60.f;
		Fov = 30.f;
	}
	else if (H.bWalkIn)	// the fighter being introduced
	{
		const int32 W = H.WalkInProgress < 0.5 ? 0 : 1;
		Look = Head[W] - Up * 16.f;
		Pos = Look + Out * 190.f + FVector(W ? -70.f : 70.f, 0.f, 0.f);
	}
	else if (StrikeAt > 0.0 && Now - StrikeAt < 2.5)	// the struck face
	{
		Look = Head[Struck] - Up * 3.f;
		Pos = Look + Out * 115.f + FVector(Struck ? -50.f : 50.f, 0.f, 4.f);
		Fov = 27.f;
	}
	else	// the defender, waiting for it
	{
		Look = Head[Def];
		Pos = Look + Out * 120.f + FVector(Def ? -55.f : 55.f, 0.f, 6.f);
		(void)Str;
	}
	const FRotator Rot = (Look - Pos).Rotation();
	const bool bCut = JumboSubject != (int32)(Look.X > StageCentre.X) + 2 * (int32)H.bWalkIn + 4 * (int32)(Now - KOAt < KOShot) || !bJumboAimed;
	JumboSubject = (int32)(Look.X > StageCentre.X) + 2 * (int32)H.bWalkIn + 4 * (int32)(Now - KOAt < KOShot);
	bJumboAimed = true;
	JumboCam->FOVAngle = bCut ? Fov : FMath::FInterpTo(JumboCam->FOVAngle, Fov, Dt, 4.f);
	JumboCam->SetWorldLocationAndRotation(bCut ? Pos : FMath::VInterpTo(JumboCam->GetComponentLocation(), Pos, Dt, 6.f),
		bCut ? Rot : FMath::RInterpTo(JumboCam->GetComponentRotation(), Rot, Dt, 8.f));
	JumboCam->CaptureScene();
	++JumboFrames;
}

// ---- the 3D titles ---------------------------------------------------------------------------------------------------

void ASlapHUD::Titles(const FSlapHud& H, double Now)
{
	if (!BigTitle || !Body[0].IsValid() || !Body[1].IsValid()) return;
	const bool bShowing = H.Match > 0 && TierLive >= 1;
	if (StageCentre.IsZero())	// the middle of the podium on the floor (the jumbotron sets it too, when it is on)
	{
		const FVector P0 = Body[0]->GetBoneLocation(BonePelvis), P1 = Body[1]->GetBoneLocation(BonePelvis);
		StageCentre = FVector(0.5f * (P0.X + P1.X), 0.5f * (P0.Y + P1.Y), 0.f);
	}
	const FQuat Face = FRotator(0.f, TitleYaw, 0.f).Quaternion();	// the letters' front toward the broadcast side
	auto Style = [](UText3DComponent* T, const FLinearColor& Front, const FLinearColor& Side)
	{
		if (UText3DDefaultMaterialExtension* M = T->GetCastedMaterialExtension<UText3DDefaultMaterialExtension>())
		{
			M->SetStyle(EText3DMaterialStyle::Solid);
			M->SetIsUnlit(true);	// the hall is dark: the letters carry their own light, like an LED sign
			M->SetFrontColor(Front);
			M->SetBevelColor(Front);
			M->SetExtrudeColor(Side);
			M->SetBackColor(Side);
		}
	};
	// each fighter's name over his own corner: on the walk-in (rising out of the floor behind him) and for the winner
	for (int32 a = 0; a < 2; ++a)
	{
		const bool bWinner = !H.Result.IsEmpty() && H.Winner == a && Now - KOAt >= KOShot;
		const bool bShow = bShowing && !H.Name[a].IsEmpty() && (H.bWalkIn || bWinner);
		UText3DComponent* T = NameTitle[a];
		if (bShow && TitleName[a] != H.Name[a])	// rebuilt only when the name changes (a new pairing)
		{
			TitleName[a] = H.Name[a];
			T->SetText(FText::FromString(H.Name[a].ToUpper()));
			Style(T, FLinearColor(TeamColor[a]) * 3.f, FLinearColor(TeamColor[a]) * 0.6f);	// unlit: bright enough to read in the dark hall
		}
		NameShown[a] = FMath::FInterpTo(NameShown[a], bShow ? 1.f : 0.f, GetWorld()->GetDeltaSeconds(), bShow ? 3.f : 6.f);
		const bool bVis = NameShown[a] > 0.02f;
		if (T->IsVisible() != bVis) T->SetVisibility(bVis, true);
		if (!bVis) continue;
		// standing on the mat behind his own corner, rising out of the floor (the crane shot of the walk-in looks down on it)
		const float Rise = FMath::InterpEaseOut(0.f, 1.f, NameShown[a], 2.f);
		T->SetWorldTransform(FTransform(Face, StageCentre + FVector(a ? 80.f : -80.f, -120.f, 22.f * Rise - 30.f * (1.f - Rise)), FVector(TitleSize * 1.2f * FMath::Max(Rise, 0.05f))));
	}
	// the big caption, standing behind the podium: it pops in, holds for the caption's time, drops away
	const bool bCap = bShowing && Now - CapAt < CapFor && !CapBig.IsEmpty();
	if (bCap && TitleBig != CapBig)
	{
		TitleBig = CapBig;
		BigTitle->SetText(FText::FromString(CapBig));
		Style(BigTitle, FLinearColor(CapColor) * 2.5f, FLinearColor(CapColor) * 0.5f);
		++TitlesShown;
	}
	if (!bCap) TitleBig.Reset();
	const float In = bCap ? FMath::Clamp(float(Now - CapAt) / 0.35f, 0.f, 1.f) : 0.f;
	const float Outro = bCap ? FMath::Clamp(float(CapAt + CapFor - Now) / 0.25f, 0.f, 1.f) : 0.f;
	const float Pop = bCap ? (1.f + 0.35f * FMath::Sin(In * PI)) * In * Outro : 0.f;	// overshoots, settles at 1
	const bool bVis = Pop > 0.02f;
	if (BigTitle->IsVisible() != bVis) BigTitle->SetVisibility(bVis, true);
	if (bVis)
	{
		const FVector Mid = 0.5f * (Body[0]->GetBoneLocation(BoneHead) + Body[1]->GetBoneLocation(BoneHead));
		// in the gap between the two heads, behind them: a swinging arm passes in front of the letters
		BigTitle->SetWorldTransform(FTransform(Face, FVector(Mid.X, Mid.Y - 70.f, Mid.Z - 6.f), FVector(TitleSize * Pop)));
	}
}

// ---- the Sequencer shots ---------------------------------------------------------------------------------------------

void ASlapHUD::PlaySeq(int32 k, const FTransform& Origin)
{
	ALevelSequenceActor* A = SeqActor[k].Get();
	if (!A || !A->GetSequencePlayer()) return;
	StopSeq();
	if (UDefaultLevelSequenceInstanceData* Data = Cast<UDefaultLevelSequenceInstanceData>(A->DefaultInstanceData))
	{
		Data->TransformOriginActor = nullptr;
		Data->TransformOrigin = Origin;
	}
	A->GetSequencePlayer()->Play();
	SeqPlaying = k;
	++SeqPlays;
	SLAP_HUD_LOG(1, TEXT("SlapHUD: sequence %s at %s"), SeqName[k], *Origin.GetLocation().ToString());
}

void ASlapHUD::StopSeq()
{
	if (SeqPlaying < 0) return;
	if (ALevelSequenceActor* A = SeqActor[SeqPlaying].Get())
		if (A->GetSequencePlayer() && A->GetSequencePlayer()->IsPlaying()) A->GetSequencePlayer()->Stop();
	SeqPlaying = -1;	// DrawHUD puts the view back on the level camera
}

void ASlapHUD::SeqTick(const FSlapHud& H, double Now)
{
	if (SeqLoaded == 0 || !Body[0].IsValid() || !Body[1].IsValid()) return;
	const FVector Pelvis[2] = { Body[0]->GetBoneLocation(BonePelvis), Body[1]->GetBoneLocation(BonePelvis) };
	const FVector Mid(0.5f * (Pelvis[0].X + Pelvis[1].X), 0.5f * (Pelvis[0].Y + Pelvis[1].Y), 0.f);
	// the director's own shots win whenever the picture is somebody else's: a pinned camera, the menu, a replay
	const bool bAllowed = CamMode == ECam::Auto && !bMenu && Now >= ReplayEnd && H.Match > 0;
	if (!bAllowed) { StopSeq(); }
	else
	{
		if (H.bWalkIn && !bSeqWalk) PlaySeq(SeqIntro, FTransform(Mid));	// the walk-in begins
		if (H.KOSeq != SeqSeenKO && SeqSeenKO != INT32_MIN && KOAt > 0.0 && Now - KOAt < 0.5) PlaySeq(Struck ? SeqKOBlue : SeqKORed, FTransform(Mid));
		const bool bWon = !H.Result.IsEmpty() && H.Winner >= 0 && Now - KOAt >= KOShot;
		if (bWon && !bSeqWon) PlaySeq(H.Winner ? SeqWinBlue : SeqWinRed, FTransform(FVector(Pelvis[H.Winner].X, Pelvis[H.Winner].Y, 0.f)));
		bSeqWon = bWon;
	}
	bSeqWalk = H.bWalkIn;
	SeqSeenKO = H.KOSeq;
	if (SeqPlaying < 0) return;
	ALevelSequenceActor* A = SeqActor[SeqPlaying].Get();
	if (!A || !A->GetSequencePlayer() || !A->GetSequencePlayer()->IsPlaying()) { SeqPlaying = -1; return; }
	// the spawned Cine Camera: the level camera's grade, and focus on what the shot is about
	APlayerController* PC = GetOwningPlayerController();
	ACineCameraActor* Cine = PC ? Cast<ACineCameraActor>(PC->GetViewTarget()) : nullptr;
	if (!Cine || !Camera.IsValid()) return;
	UCineCameraComponent* CC = Cine->GetCineCameraComponent();
	CC->Filmback.SensorWidth = GateW;
	CC->Filmback.SensorHeight = GateH;
	CC->LensSettings.MinFocalLength = 8.f;
	CC->LensSettings.MaxFocalLength = 200.f;
	CC->LensSettings.MinFStop = 1.2f;
	CC->SetCurrentFocalLength(SeqFocal[SeqPlaying]);
	CC->PostProcessSettings = Camera->GetCameraComponent()->PostProcessSettings;
	CC->PostProcessBlendWeight = 1.f;
	CC->PostProcessSettings.bOverride_DepthOfFieldFocalDistance = false;	// the Cine Camera's own lens does the depth of field
	const bool bDof = TierLive >= 2 && !PLATFORM_ANDROID && !PLATFORM_IOS;
	const FVector Subject = SeqPlaying == SeqIntro ? Mid + FVector(0.f, 0.f, 110.f)
		: SeqPlaying == SeqKORed || SeqPlaying == SeqKOBlue ? Pelvis[Struck] : Body[FMath::Clamp(H.Winner, 0, 1)]->GetBoneLocation(BoneHead);
	CC->FocusSettings.FocusMethod = bDof ? ECameraFocusMethod::Manual : ECameraFocusMethod::Disable;
	CC->FocusSettings.ManualFocusDistance = FVector::Dist(Cine->GetActorLocation(), Subject);
	CC->CurrentAperture = 2.8f;
}

// ---- soft tissue -----------------------------------------------------------------------------------------------------

void ASlapHUD::LoadSoftness(const FSlapHud& H)
{
	// how soft each character is: the body-mass index off the card (kg / m^2), 20 = lean (0.25), 32 and up = 1
	for (int32 a = 0; a < 2; ++a)
	{
		if (H.Card[a].IsEmpty() || H.Card[a] == SoftCard[a]) continue;
		SoftCard[a] = H.Card[a];
		Softness[a] = 0.5f;
		TSharedPtr<FJsonObject> J = Slap::ReadJson(FPaths::ProjectContentDir() / TEXT("Data") / (H.Card[a] + TEXT(".json")));
		if (!J) J = Slap::ReadJson(FPaths::ProjectDir() / TEXT("training/characters") / (H.Card[a] + TEXT(".json")));
		double Mass = 0.0, Height = 0.0;
		if (J && J->TryGetNumberField(TEXT("mass_kg"), Mass) && J->TryGetNumberField(TEXT("height_m"), Height) && Height > 0.5)
			Softness[a] = FMath::GetMappedRangeValueClamped(FVector2D(20.0, 32.0), FVector2D(0.25, 1.0), Mass / (Height * Height));
		UE_LOG(LogSlap, Log, TEXT("SlapHUD: soft tissue: %s %.0f kg, %.2f m -> softness %.2f"), *H.Card[a], Mass, Height, Softness[a]);
	}
}

void ASlapHUD::Jiggle(const FSlapHud& H)
{
	if (!bJiggle || Skin.Num() < 2) return;
	LoadSoftness(H);
	const float Dt = FMath::Min(GetWorld()->GetDeltaSeconds(), 0.1f);
	if (Dt <= 0.f) return;
	// tissue on a spring behind its bone: x'' = -w^2 x - 2 z w x' - (the bone's acceleration). The belly swings at 3.5 Hz,
	// the jowl at 7 Hz, both lightly damped. The bone's velocity is smoothed over 20 ms first: the render snapshot
	// steps at the physics rate (200 Hz), so a raw frame difference at a higher frame rate alternates between 0 and double
	struct FSite { FName Bone; float Hz, Zeta, Max; FVector Offset; FName Centre, Vec, Radius; float R; };
	const FSite Sites[2] = {
		{ BoneBelly, 3.5f, 0.22f, 4.0f, FVector(9.f, 0.f, -4.f), PJigA, PJigAV, PJigAR, 24.f },
		{ BoneHead, 7.0f, 0.30f, 1.4f, FVector(6.f, 0.f, -9.f), PJigB, PJigBV, PJigBR, 9.f } };
	for (int32 a = 0; a < 2; ++a)
	{
		if (!Body[a].IsValid() || !Skin[a]) continue;
		for (int32 s = 0; s < 2; ++s)
		{
			const FSite& Site = Sites[s];
			FSoft& St = Soft[a][s];
			const FTransform Bone = Body[a]->GetBoneTransformByName(Site.Bone, EBoneSpaces::WorldSpace);
			const FVector P = Bone.GetLocation();
			if (!St.bSeen || FVector::DistSquared(P, St.Prev) > FMath::Square(60.f))	// the first frame, or a reset to the foot boxes
			{
				St = FSoft();
				St.bSeen = true;
				St.Prev = P;
			}
			const FVector V = (P - St.Prev) / Dt;
			St.Prev = P;
			const FVector Vf = St.Vel + (V - St.Vel) * (1.f - FMath::Exp(-Dt / 0.02f));
			const FVector Acc = ((Vf - St.Vel) / Dt).GetClampedToMaxSize(6000.f);	// cm/s^2 (6 g)
			St.Vel = Vf;
			const float W = 2.f * PI * Site.Hz;
			const int32 Sub = FMath::Clamp(FMath::CeilToInt32(Dt * 240.f), 1, 24);
			const float h = Dt / Sub;
			for (int32 k = 0; k < Sub; ++k)	// semi-implicit Euler
			{
				St.XVel += (-W * W * St.X - 2.f * Site.Zeta * W * St.XVel - Acc) * h;
				St.X += St.XVel * h;
			}
			St.X = St.X.GetClampedToMaxSize(Site.Max);
			// the centre sits on the front of the belly / under the chin: the offset is in the bone's frame (x forward, z up)
			const FVector Fwd = FVector(a ? -1.f : 1.f, 0.f, 0.f);
			const FVector Centre = P + Fwd * Site.Offset.X + FVector::UpVector * Site.Offset.Z;
			Skin[a]->SetVectorParameterValue(Site.Centre, FLinearColor(Centre));
			Skin[a]->SetVectorParameterValue(Site.Vec, FLinearColor(St.X * Softness[a]));
			Skin[a]->SetScalarParameterValue(Site.Radius, Site.R * (0.8f + 0.4f * Softness[a]));
			JigglePeak = FMath::Max(JigglePeak, float(St.X.Size()) * Softness[a]);
		}
	}
}
