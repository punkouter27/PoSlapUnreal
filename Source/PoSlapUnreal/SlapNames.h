#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "MuJoCo/Utils/MjUtils.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

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

	/** The two corners' pelvis bodies, red then blue (a cooked build compiles the components in another order). */
	inline TArray<int32> CornerPelvises(const mjModel* m)
	{
		TArray<int32> Out;
		for (const TCHAR* Corner : { TEXT("red_pelvis"), TEXT("blue_pelvis") })
			for (int32 b = 0; b < m->nbody; ++b)
				if (Name(m, mjOBJ_BODY, b).EndsWith(Corner)) Out.Add(b);
		return Out;
	}

	/** A corner's body prefix ("..._red_") from its pelvis body's name ("..._red_pelvis"). */
	inline FString CornerPrefix(const FString& PelvisBodyName) { return PelvisBodyName.LeftChop(6); }	// "pelvis"

	/** A JSON file, read quietly: null when it is missing or malformed. */
	inline TSharedPtr<FJsonObject> ReadJson(const FString& Path)
	{
		FString Raw;
		TSharedPtr<FJsonObject> J;
		return FFileHelper::LoadFileToString(Raw, *Path) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Raw), J) ? J : nullptr;
	}

	/** A project data file by its repo-relative path: the source checkout / editor, then the packaged
	 *  Content/Data/<file> (Android / packaged builds stage the cards and fingerprints there), then Content/<path>. */
	inline TSharedPtr<FJsonObject> LoadJson(const FString& RelPath)
	{
		for (const FString& Path : { FPaths::Combine(FPaths::ProjectDir(), RelPath),
			FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Data"), FPaths::GetCleanFilename(RelPath)),
			FPaths::Combine(FPaths::ProjectContentDir(), RelPath) })
			if (TSharedPtr<FJsonObject> J = ReadJson(Path)) return J;
		return nullptr;
	}
}
