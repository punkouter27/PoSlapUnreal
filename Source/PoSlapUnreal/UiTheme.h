#pragma once

#include "CoreMinimal.h"

/**
 * Shared Profile B UMG/Slate UI theme tokens for PoSlapUnreal.
 * Centralizes spacing, corner radii, typography sizes, minimum 48dp touch targets,
 * and standard RL policy colors (RED = heuristic bot, GREEN = reference RL, BLUE = custom).
 */
namespace SlapUiTheme
{
	inline constexpr float Space1 = 4.f;
	inline constexpr float Space2 = 8.f;
	inline constexpr float Space3 = 12.f;
	inline constexpr float Space4 = 16.f;
	inline constexpr float Space5 = 24.f;

	inline constexpr float RadiusS = 8.f;
	inline constexpr float RadiusM = 14.f;
	inline constexpr float RadiusL = 24.f;

	inline constexpr float TouchMin = 48.f;
	inline constexpr float SafePadding = 24.f;

	inline const FColor PolicyBot(235, 70, 60);
	inline const FColor PolicyRef(41, 140, 71);
	inline const FColor PolicyCustom(51, 128, 255);
	inline const FColor Gold(255, 220, 120);
}
