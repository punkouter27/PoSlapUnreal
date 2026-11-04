#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "SlapEditorLib.generated.h"

class UAudioImpulseResponse;
class USkeletalMesh;

/**
 * Asset-pipeline helpers the editor's Python cannot do itself (Scripts/broadcast_fx.py calls them as
 * unreal.SlapEditorLib.*): fill an impulse response from samples (its sample array is not an editable property), and
 * keep a CPU copy of an athlete mesh's LOD0 (the drawn-hand clamp reads its vertices: SlapAthleteVisual.cpp).
 */
UCLASS()
class POSLAPUNREAL_API USlapEditorLib : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Interleaved samples (-1..1), channel count and rate -> the convolution reverb's impulse response. */
	UFUNCTION(BlueprintCallable, Category = "PoSlap|Editor")
	static bool FillImpulseResponse(UAudioImpulseResponse* Response, const TArray<float>& Samples, int32 NumChannels, int32 SampleRate, float NormalizationDb);

	/** LOD0's Allow CPU Access (editor only; the render data is rebuilt). Returns whether it changed. */
	UFUNCTION(BlueprintCallable, Category = "PoSlap|Editor")
	static bool SetMeshCPUAccess(USkeletalMesh* Mesh, bool bAllow);
};
