#pragma once

#include "CoreMinimal.h"
#include "MuJoCo/Utils/MjUtils.h"

/** MuJoCo name lookup shared by the controller and the visuals. */
namespace Slap
{
	inline FString Name(const mjModel* m, mjtObj Type, int32 Id) { return MjUtils::MjToString(mj_id2name(m, Type, Id)); }

	/** Model names carry URLab's "{Actor}_" prefix; match a training name as the whole name or a "_"-suffix. */
	inline bool Matches(const FString& Full, const FString& Short)
	{
		return Full == Short || Full.EndsWith(TEXT("_") + Short);
	}

	inline int32 Find(const mjModel* m, mjtObj Type, int32 Count, const FString& Short)
	{
		for (int32 i = 0; i < Count; ++i)
		{
			if (Matches(Name(m, Type, i), Short)) return i;
		}
		return -1;
	}
}
