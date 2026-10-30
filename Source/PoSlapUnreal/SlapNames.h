#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "MuJoCo/Utils/MjUtils.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

/** The game's log category: the match's events, the HUD's cues. The checks grep for these lines. */
DECLARE_LOG_CATEGORY_EXTERN(LogSlap, Log, All);

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

	/**
	 * The roster: every character card in its slot ("slot": 1-16, else the first open one): the tape, its style, the
	 * brains made for it (a new one shows "in training" until it graduates); the other slots are placeholders for
	 * athletes still to come. Out holds one pair per slot: the tile's text and the card's detail text.
	 * ponytail: the cards live in training/characters beside the project; a packaged (Android) build stages them in
	 * Content/Data, so both places are read.
	 */
	inline void LoadRoster(int32 Size, TArray<TPair<FString, FString>>& Out, const TArray<FString>& BrainNames, TArray<FString>* OutCardNames = nullptr)
	{
		TArray<FString> Files;
		for (const FString& Dir : { FPaths::ProjectDir() / TEXT("training/characters"), FPaths::ProjectContentDir() / TEXT("Data") })
		{
			TArray<FString> Found;
			IFileManager::Get().FindFiles(Found, *(Dir / TEXT("*.json")), true, false);
			for (const FString& F : Found)
			{
				if (F.EndsWith(TEXT("_skeleton.json")) || F.EndsWith(TEXT("_fingerprint.json"))) continue;	// not cards
				Files.AddUnique(F);	// the editor has both places: one card, one slot
			}
		}
		Files.Sort();
		Out.Init({}, Size);
		if (OutCardNames) OutCardNames->Init(FString(), Size);
		// the card of a slot is four lines, always: a long summary or brain list is cut, never wrapped (the sheet does not scroll)
		auto Cut = [](FString S, int32 N)
		{
			S.ReplaceInline(TEXT("\n"), TEXT(" "));
			if (S.Len() > N) S = S.Left(N - 1) + TEXT("...");
			return S;
		};
		for (const FString& F : Files)
		{
			// the card by name, from either place: training/characters/<name>.json or the staged Content/Data/<name>.json
			const TSharedPtr<FJsonObject> C = LoadJson(TEXT("training/characters/") + F);
			if (!C) continue;
			const FString Name = C->GetStringField(TEXT("name"));
			FString Mine;
			for (const FString& B : BrainNames)
			{
				if (B.StartsWith(Name + TEXT("_"))) Mine += (Mine.IsEmpty() ? TEXT("") : TEXT(", ")) + B.RightChop(Name.Len() + 1);
			}
			const TSharedPtr<FJsonObject>* Style = nullptr;
			const FString Summary = Cut(C->TryGetObjectField(TEXT("style"), Style) ? (*Style)->GetStringField(TEXT("summary")) : FString(), 74);
			int32 Slot = INDEX_NONE;
			if (C->TryGetNumberField(TEXT("slot"), Slot)) Slot -= 1;
			if (!Out.IsValidIndex(Slot) || !Out[Slot].Key.IsEmpty()) Slot = Out.IndexOfByPredicate([](const TPair<FString, FString>& X) { return X.Key.IsEmpty(); });
			if (Slot == INDEX_NONE) break;	// more cards than slots
			Out[Slot].Key = C->GetStringField(TEXT("display"));
			if (OutCardNames) (*OutCardNames)[Slot] = Name;
			const FString Made = Cut(Mine.IsEmpty() ? FString(TEXT("none yet (in training)")) : Mine, 54);
			Out[Slot].Value = FString::Printf(TEXT("%s   %.2f m  %.0f kg\nSTR %.2f  SPD %.2f  FLX %.2f  TGH %.2f\n%s\nBrains: %s"),
				*Out[Slot].Key, C->GetNumberField(TEXT("height_m")), C->GetNumberField(TEXT("mass_kg")), C->GetNumberField(TEXT("strength")),
				C->GetNumberField(TEXT("speed")), C->GetNumberField(TEXT("flexibility")), C->GetNumberField(TEXT("toughness")), *Summary, *Made);
		}
		for (int32 i = 0; i < Size; ++i)
		{
			if (!Out[i].Key.IsEmpty()) continue;
			Out[i].Value = FString::Printf(TEXT("Slot %d is open.\nA new athlete: a rigged FBX -> Scripts/rig_mixamo.py -> a card\ntraining/characters/<name>.json with \"slot\": %d -> build_body.py\n-> its own ladder (overnight.py --char <name> --queue)"), i + 1, i + 1);
		}
	}
}
