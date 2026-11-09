#pragma once

// ASlapHUD's own helpers, shared by its .cpp files (SlapHUD*.cpp). Not a public header.

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "SlapController.h"
#include "SlapNames.h"

namespace SlapUi
{
	using Slap::Rounds, Slap::MinStamina, Slap::MinNeck, Slap::CleanSpeed, Slap::StrikeClock, Slap::Recovery;

	/** slap.HudLog (SlapHUDPanels.cpp): 0 warnings only, 1 one line per event (default), 2 + per-frame diagnostics. */
	int32 HudLogLevel();
	/** slap.DisplayPass (SlapHUDPanels.cpp; -SlapDisplayPass=0): the 2026-11-04 display pass on (1) or off (0). */
	bool DisplayPassOn();

	inline const FName BoneHead("head"), BonePelvis("pelvis"), BoneFootL("foot_l"), BoneFootR("foot_r"), BoneHandL("hand_l"), BoneHandR("hand_r");
	// the athletes' material parameters (Scripts/broadcast_fx.py add_wear / add_crowd)
	inline const FName PRippleTime("RippleTime"), PRipplePoint("RipplePoint"), PRippleAmp("RippleAmp"), PRippleDir("RippleDir"),
		PFlushPoint("FlushPoint"), PFlush("Flush"), PTired("Tired"), PRise("Rise"), PCheer("Cheer"), PPrint("Print"), PPrintU("PrintU"), PPrintV("PrintV"),
		PCrowdCut("CrowdCut"), PCheekFlush("CheekFlush"), PDecal("Decal"), POpacity("Opacity"), PRoughness("Roughness");
	// the cheek dent: morph targets on the athlete meshes (Content/Art; a mesh without them is left alone)
	inline const FName DentMorph[2] = { FName("CheekDent_L"), FName("CheekDent_R") };

	inline const FColor TeamColor[2] = { FColor(235, 70, 60), FColor(70, 130, 240) };
	inline const FColor Gold(255, 220, 120), Grey(200, 200, 200);
	inline constexpr float TopBar = 56.f, BottomBar = 40.f;	// Slate units: the corner anchors' rows (BuildAnchors)
	inline constexpr float Touch = 44.f;	// the minimum touch target (Slate units, ~7 mm on a phone)
	inline constexpr int32 MinSlateFont = 12;	// the smallest Slate text (readable on a 6" phone)
	inline constexpr float MinTextScale = 0.8f;	// a canvas line shrinks to fit its box down to this share of its scale
	inline constexpr double SnapSeconds = 2.0;	// the head-snap sparkline's window after each strike
	/** The phone arena's levels: "<level>_Mobile" (Scripts/arena_mobile_level.py). */
	inline bool IsMobileLevel(const FString& Name) { return Name.EndsWith(TEXT("_Mobile")); }
	inline FString DesktopLevel(const FString& Name) { return IsMobileLevel(Name) ? Name.LeftChop(7) : Name; }
	inline constexpr float MatRadius = 270.f;	// cm: the padded safety mat around the centre; the hard stage deck outside (Web/audio.js MAT_RADIUS)
	inline const TCHAR* const ShotName[] = { TEXT("WALKIN"), TEXT("WIDE"), TEXT("SIDE"), TEXT("TENSION"), TEXT("STRIKE"), TEXT("IMPACT"), TEXT("KO"), TEXT("REPLAY"), TEXT("WINNER") };
	inline const TCHAR* const CamName[] = { TEXT("AUTO"), TEXT("WIDE"), TEXT("SIDE"), TEXT("CLOSE") };

	inline int32 Team(const FString& Name) { return Name == TEXT("blue") ? 1 : 0; }
	template <typename T> T* Load(const TCHAR* Path) { return LoadObject<T>(nullptr, Path, nullptr, LOAD_Quiet | LOAD_NoWarn); }
	template <typename T> T* LoadNamed(const TCHAR* Folder, const TCHAR* Name) { return Load<T>(*FString::Printf(TEXT("%s/%s.%s"), Folder, Name, Name)); }

	/** Stamina and neck strength as the share left above their floors (1 fresh, 0 at the floor). */
	inline double StaminaLeft(double Stamina) { return FMath::Clamp((Stamina - MinStamina) / (1.0 - MinStamina), 0.0, 1.0); }
	inline double NeckLeft(double Neck) { return FMath::Clamp((Neck - MinNeck) / (1.0 - MinNeck), 0.0, 1.0); }

	/** A ring in screen space. */
	inline void Ring(AHUD* Hud, const FVector& C, float R, int32 Sides, const FLinearColor& Col, float Thick)
	{
		for (int32 k = 0; k < Sides; ++k)
		{
			const float A0 = 2.f * PI * k / Sides, A1 = 2.f * PI * (k + 1) / Sides;
			Hud->DrawLine(C.X + R * FMath::Cos(A0), C.Y + R * FMath::Sin(A0), C.X + R * FMath::Cos(A1), C.Y + R * FMath::Sin(A1), Col, Thick);
		}
	}
}

/** A HUD log line at slap.HudLog >= Level (1 = per event, 2 = per frame). Warnings and errors stay plain UE_LOGs. */
#define SLAP_HUD_LOG(Level, Format, ...) do { if (SlapUi::HudLogLevel() >= (Level)) { UE_LOG(LogSlap, Log, Format, ##__VA_ARGS__); } } while (0)
