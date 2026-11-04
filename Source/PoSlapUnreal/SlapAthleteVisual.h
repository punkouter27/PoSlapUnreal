#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SlapAthleteVisual.generated.h"

class UPoseableMeshComponent;
struct FMjRenderSnapshot;

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

	/** Action replay (display only): from now on show the pose recorded between game times From and To at Rate x speed. */
	void Replay(double From, double To, double Rate) { ReplayFrom = From; ReplayTo = To; ReplayRate = Rate; ReplayStart = GetWorld()->GetTimeSeconds(); }

	/** The drawn-hand clamp's last push (cm, 0 = none) and the most it pushed since the level opened (the report). */
	double ClipLastCm = 0.0, ClipMaxCm = 0.0;

private:
	bool Init();

	/**
	 * The mesh clamp (Web/art.js clipData / clipPush): the drawn palm stays out of the drawn face. MuJoCo's palm sinks
	 * 1-7 cm into the head sphere and the sphere does not cover the drawn jaw, so the drawn hand is pushed out along the
	 * face's own normals. Drawing only: the bone is moved, never the MuJoCo state.
	 */
	struct FClip
	{
		TArray<FVector3f> P, N;	// vertices (weight >= 0.5 on this bone) and, for a face part, their normals, in the bone's frame (cm, scaled)
		FVector C = FVector::ZeroVector;	// the face part's centre (bone frame)
		double R = 0.0;			// the face part's radius around C; a hand's reach from its bone
		int32 Driven = INDEX_NONE;	// driven-bone index
	};
	enum { ClipHandL, ClipHandR, ClipHead, ClipNeck, ClipParts };
	FClip Clip[ClipParts];
	bool bClip = false;
	double ClipEpisodeMax = 0.0, ClipLastAt = -100.0;	// one log line per contact: the most it pushed
	int32 ClipFrames = 0;
	TWeakObjectPtr<ASlapAthleteVisual> Opponent;
	void BuildClip(const TArray<FTransform>& CS, const TArray<int32>& RefIdx);
	/** Pushes Hand (a world bone transform, moved in place) out of Face (the other athlete's head or neck bone). Returns cm. */
	double ClipPush(const FClip& Hand, const FClip& Face, const FTransform& FaceT, FTransform& HandT) const;
	void ClampHands(const FMjRenderSnapshot& S);

	bool bReady = false;
	bool bChecked = false;
	TArray<FName> Bones;	// driven bones
	TArray<int32> BodyIds;	// MuJoCo body per driven bone
	TArray<FQuat> Offsets;	// bone rotation relative to its body, fixed at the rest pose
	TArray<FVector> Scales;	// bone world scale at the rest pose
	TArray<int32> RefToDriven;	// reference-skeleton index -> driven-bone index, or INDEX_NONE
	TArray<FTransform> LastPose;	// world pose set last frame, kept only until the self-check runs
	TArray<FTransform> Pose, CompSpace;	// per-frame scratch (world pose, component space), kept to skip the allocations
	TArray<TPair<double, TArray<FTransform>>> History;	// world poses of the last 3 s of game time (replay)
	double ReplayFrom = 0.0, ReplayTo = 0.0, ReplayRate = 0.0, ReplayStart = 0.0;	// rate 0 = live
};
