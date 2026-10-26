#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SlapAthleteVisual.generated.h"

class UPoseableMeshComponent;

/**
 * The skinned athlete, moved bone by bone from MuJoCo bodies (display only, no physics).
 * Bone names == body names (training/build_body.py); bodies are found as "<anything>_<BodyPrefix><bone>".
 */
UCLASS()
class POSLAPUNREAL_API ASlapAthleteVisual : public AActor
{
	GENERATED_BODY()

public:
	ASlapAthleteVisual();

	UPROPERTY(VisibleAnywhere, Category = "PoSlap")
	TObjectPtr<UPoseableMeshComponent> Mesh;

	/** "red_" or "blue_" in the arena, "" for the solo model. */
	UPROPERTY(EditAnywhere, Category = "PoSlap")
	FString BodyPrefix = TEXT("red_");

	virtual void Tick(float DeltaSeconds) override;

private:
	bool Init();

	bool bReady = false;
	bool bChecked = false;
	TArray<FName> Bones;	// driven bones
	TArray<int32> BodyIds;	// MuJoCo body per driven bone
	TArray<FQuat> Offsets;	// bone rotation relative to its body, fixed at the rest pose
	TArray<FVector> Scales;	// bone world scale at the rest pose
	TArray<int32> RefToDriven;	// reference-skeleton index -> driven-bone index, or INDEX_NONE
	TArray<FTransform> LastPose;	// world pose set last frame, kept only until the self-check runs
	TArray<FTransform> Pose, CompSpace;	// per-frame scratch (world pose, component space), kept to skip the allocations
};
