#include "SlapEditorLib.h"

#include "EffectConvolutionReverb.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/SkinnedAssetCommon.h"

bool USlapEditorLib::FillImpulseResponse(UAudioImpulseResponse* Response, const TArray<float>& Samples, int32 NumChannels, int32 SampleRate, float NormalizationDb)
{
	if (!Response || Samples.Num() == 0 || NumChannels <= 0 || SampleRate <= 0) return false;
	Response->Modify();
	Response->ImpulseResponse = Samples;
	Response->NumChannels = NumChannels;
	Response->SampleRate = SampleRate;
	Response->NormalizationVolumeDb = NormalizationDb;
	Response->bTrueStereo = false;	// independent left/right impulses (broadcast_audio.py arena_ir: decorrelated tails)
#if WITH_EDITORONLY_DATA
	Response->bIsEvenChannelCount = NumChannels % 2 == 0;
#endif
	Response->MarkPackageDirty();
	return true;
}

bool USlapEditorLib::SetMeshCPUAccess(USkeletalMesh* Mesh, bool bAllow)
{
#if WITH_EDITOR
	FSkeletalMeshLODInfo* Info = Mesh ? Mesh->GetLODInfo(0) : nullptr;
	if (!Info || Info->bAllowCPUAccess == bAllow) return false;
	Mesh->Modify();
	Info->bAllowCPUAccess = bAllow;
	Mesh->PostEditChange();	// rebuilds the render data with (or without) the CPU copy
	Mesh->MarkPackageDirty();
	return true;
#else
	return false;
#endif
}
