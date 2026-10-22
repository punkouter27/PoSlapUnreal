#include "SlapAthleteVisual.h"

#include "AnimationRuntime.h"
#include "Components/PoseableMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "MuJoCo/Core/AMjManager.h"
#include "MuJoCo/Core/MjPhysicsEngine.h"
#include "MuJoCo/Core/MjRenderSnapshot.h"
#include "MuJoCo/Utils/MjUtils.h"
#include "SlapNames.h"

ASlapAthleteVisual::ASlapAthleteVisual()
{
	PrimaryActorTick.bCanEverTick = true;
	Mesh = CreateDefaultSubobject<UPoseableMeshComponent>(TEXT("Mesh"));
	Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);	// Chaos stays out: MuJoCo owns all contact
	Mesh->SetSimulatePhysics(false);
	Mesh->SetBoundsScale(4.f);	// bones move far from the reference pose; keep it from being culled
	RootComponent = Mesh;
}

bool ASlapAthleteVisual::Init()
{
	AAMjManager* Manager = AAMjManager::GetManager();
	const mjModel* m = Manager && Manager->PhysicsEngine ? Manager->PhysicsEngine->GetModel() : nullptr;
	USkinnedAsset* Asset = Mesh->GetSkinnedAsset();
	if (!m || !Asset) return false;

	// Rest pose on both sides: MuJoCo at qpos0 (the T-pose the mesh was bound in), mesh at its reference pose.
	mjData* d = mj_makeData(m);
	mj_resetData(m, d);
	mj_kinematics(m, d);

	const FReferenceSkeleton& Ref = Asset->GetRefSkeleton();
	TArray<FTransform> CS;	// component space
	FAnimationRuntime::FillUpComponentSpaceTransforms(Ref, Ref.GetRefBonePose(), CS);
	TArray<int32> RefIdx;	// driven bone -> reference-skeleton index
	TArray<FQuat> BodyRest;
	TMap<FName, TPair<FVector, FVector>> Anchor;	// bone -> (mesh position, MuJoCo position)
	BodyIds.Reset(); Bones.Reset();
	RefToDriven.Init(INDEX_NONE, CS.Num());
	for (int32 i = 0; i < CS.Num(); ++i)
	{
		const int32 b = Slap::Find(m, mjOBJ_BODY, m->nbody, BodyPrefix + Ref.GetBoneName(i).ToString());
		if (b < 0) continue;
		RefToDriven[i] = BodyIds.Num();
		RefIdx.Add(i);
		Bones.Add(Ref.GetBoneName(i));
		BodyIds.Add(b);
		BodyRest.Add(MjUtils::MjToUERotation(d->xquat + 4 * b));
		Anchor.Add(Ref.GetBoneName(i), { CS[i].GetLocation(), MjUtils::MjToUEPosition(d->xpos + 3 * b) });
	}
	mj_deleteData(d);
	const TCHAR* Need[] = { TEXT("pelvis"), TEXT("head"), TEXT("hand_l"), TEXT("hand_r") };
	for (const TCHAR* N : Need)
	{
		if (!Anchor.Contains(N))
		{
			UE_LOG(LogTemp, Error, TEXT("SlapAthleteVisual: no body/bone '%s%s'"), *BodyPrefix, N);
			return false;	// retry next frame (the model may still be compiling)
		}
	}

	// Mesh component space -> world: match the up (pelvis->head) and left (hand_r->hand_l) directions.
	auto Frame = [&](bool World)
	{
		auto P = [&](const TCHAR* N) { return World ? Anchor[N].Value : Anchor[N].Key; };
		return FRotationMatrix::MakeFromXY(P(TEXT("head")) - P(TEXT("pelvis")), P(TEXT("hand_l")) - P(TEXT("hand_r"))).ToQuat();
	};
	const FQuat MeshToWorld = Frame(true) * Frame(false).Inverse();
	Offsets.Reset();
	Scales.Reset();
	for (int32 k = 0; k < BodyIds.Num(); ++k)
	{
		const FTransform& Rest = CS[RefIdx[k]];
		Offsets.Add(BodyRest[k].Inverse() * MeshToWorld * Rest.GetRotation());
		Scales.Add(Rest.GetScale3D() * Mesh->GetComponentScale());	// FBX unit scale lives on the bones; keep it
	}
	bReady = true;
	UE_LOG(LogTemp, Log, TEXT("SlapAthleteVisual %s: driving %d bones, rest bone scale %s"), *BodyPrefix, BodyIds.Num(), *Scales[0].ToString());
	return true;
}

void ASlapAthleteVisual::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bReady && !Init()) return;

	if (!bChecked && LastPose.Num() && GetWorld()->GetTimeSeconds() > 2.0)	// one-off self-check (Blueprint 3; tasks B3, B4)
	{
		bChecked = true;	// the engine has refreshed last frame's pose by now: compare it with what we set
		double MaxErr = 0;
		for (int32 k = 0; k < LastPose.Num(); ++k)
		{
			MaxErr = FMath::Max(MaxErr, FVector::Dist(Mesh->GetBoneLocation(Bones[k], EBoneSpaces::WorldSpace), LastPose[k].GetLocation()));
		}
		const double O[3] = { 0, 0, 0 }, X[3] = { 1, 0, 0 }, Y[3] = { 0, 1, 0 };
		UE_LOG(LogTemp, Log, TEXT("B3 MuJoCo (0,0,0) (1,0,0) (0,1,0) m -> UE %s | %s | %s cm.  B4 %s: max bone-body gap %.4f cm"),
			*MjUtils::MjToUEPosition(O).ToString(), *MjUtils::MjToUEPosition(X).ToString(), *MjUtils::MjToUEPosition(Y).ToString(),
			*BodyPrefix, MaxErr);
		LastPose.Empty();
	}

	TArray<FTransform> Pose;
	AAMjManager::GetManager()->PhysicsEngine->WithRenderState([&](const FMjRenderSnapshot& S)
	{
		if (S.XPos.Num() == 0) return;
		Pose.SetNum(BodyIds.Num());
		for (int32 k = 0; k < BodyIds.Num(); ++k)
		{
			const int32 b = BodyIds[k];
			Pose[k] = FTransform(MjUtils::MjToUERotation(&S.XQuat[4 * b]) * Offsets[k], MjUtils::MjToUEPosition(&S.XPos[3 * b]), Scales[k]);
		}
	});
	if (Pose.Num() == 0) return;

	// World pose -> parent-relative transforms in one pass. (SetBoneTransformByName converts with the
	// parent's previous-frame transform, so a moving child would lag a frame.)
	const FReferenceSkeleton& Ref = Mesh->GetSkinnedAsset()->GetRefSkeleton();
	const FTransform ToComponent = Mesh->GetComponentTransform().Inverse();
	TArray<FTransform>& Local = Mesh->BoneSpaceTransforms;
	TArray<FTransform> CS;
	CS.SetNum(Local.Num());
	for (int32 i = 0; i < Local.Num(); ++i)
	{
		const int32 P = Ref.GetParentIndex(i);
		if (RefToDriven[i] != INDEX_NONE)
		{
			CS[i] = Pose[RefToDriven[i]] * ToComponent;
			Local[i] = P == INDEX_NONE ? CS[i] : CS[i].GetRelativeTransform(CS[P]);
		}
		else
		{
			CS[i] = P == INDEX_NONE ? Local[i] : Local[i] * CS[P];
		}
	}
	Mesh->MarkRefreshTransformDirty();
	if (!bChecked) LastPose = MoveTemp(Pose);
}
