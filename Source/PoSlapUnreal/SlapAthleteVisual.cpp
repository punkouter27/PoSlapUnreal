#include "SlapAthleteVisual.h"

#include "AnimationRuntime.h"
#include "Algo/BinarySearch.h"
#include "Components/PoseableMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "EngineUtils.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "MuJoCo/Core/AMjManager.h"
#include "MuJoCo/Core/MjPhysicsEngine.h"
#include "MuJoCo/Core/MjRenderSnapshot.h"
#include "MuJoCo/Utils/MjUtils.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "SlapReel.h"

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
	BuildClip(CS, RefIdx);
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

	Pose.Reset();
	const FSlapReel& Reel = FSlapReel::Playing();
	if (Reel.IsValid())	// -SlapReel=<file>: one recorded physics step per frame (SlapReel.h); the live physics is not drawn
	{
		const float* F = Reel.Frame(FSlapReel::PlayStep);
		ReelPos.SetNumUninitialized(3 * Reel.NumBodies, EAllowShrinking::No);
		ReelQuat.SetNumUninitialized(4 * Reel.NumBodies, EAllowShrinking::No);
		for (int32 b = 0; b < Reel.NumBodies; ++b)
		{
			for (int32 i = 0; i < 3; ++i) ReelPos[3 * b + i] = F[7 * b + i];
			for (int32 i = 0; i < 4; ++i) ReelQuat[4 * b + i] = F[7 * b + 3 + i];
		}
		Pose.SetNum(BodyIds.Num());
		for (int32 k = 0; k < BodyIds.Num(); ++k)
		{
			const int32 b = BodyIds[k];
			if (b >= Reel.NumBodies) { Pose.Reset(); break; }	// a reel from another pairing's model
			Pose[k] = FTransform(MjUtils::MjToUERotation(&ReelQuat[4 * b]) * Offsets[k], MjUtils::MjToUEPosition(&ReelPos[3 * b]), Scales[k]);
		}
		if (Pose.Num()) ClampHands(ReelPos, ReelQuat);
	}
	else AAMjManager::GetManager()->PhysicsEngine->WithRenderState([&](const FMjRenderSnapshot& S)
	{
		if (S.XPos.Num() == 0) return;
		Pose.SetNum(BodyIds.Num());
		for (int32 k = 0; k < BodyIds.Num(); ++k)
		{
			const int32 b = BodyIds[k];
			Pose[k] = FTransform(MjUtils::MjToUERotation(&S.XQuat[4 * b]) * Offsets[k], MjUtils::MjToUEPosition(&S.XPos[3 * b]), Scales[k]);
		}
		if (ReplayRate == 0.0) ClampHands(S.XPos, S.XQuat);	// live only: a replay shows the poses recorded (already clamped)
	});
	if (Pose.Num() == 0) return;

	const double Now = GetWorld()->GetTimeSeconds();
	if (ReplayRate > 0.0)	// replay: the recorded pose at the replay clock, blended between the two frames around it
	{
		const double T = ReplayFrom + (Now - ReplayStart) * ReplayRate;
		const int32 k = Algo::UpperBoundBy(History, T, [](const TPair<double, TArray<FTransform>>& F) { return F.Key; });
		if (T >= ReplayTo || k == 0 || k >= History.Num()) ReplayRate = 0.0;
		else
		{
			const auto& A = History[k - 1];
			const auto& B = History[k];
			const float Alpha = (T - A.Key) / FMath::Max(B.Key - A.Key, 1e-6);
			for (int32 i = 0; i < Pose.Num(); ++i) Pose[i].Blend(A.Value[i], B.Value[i], Alpha);
		}
	}
	if (ReplayRate == 0.0)	// live: record (the replay plays from a frozen history)
	{
		History.Emplace(Now, Pose);
		const int32 Old = Algo::LowerBoundBy(History, Now - HistorySeconds, [](const TPair<double, TArray<FTransform>>& F) { return F.Key; });
		if (Old > 0) History.RemoveAt(0, Old);
	}

	// World pose -> parent-relative transforms in one pass. (SetBoneTransformByName converts with the
	// parent's previous-frame transform, so a moving child would lag a frame.)
	const FReferenceSkeleton& Ref = Mesh->GetSkinnedAsset()->GetRefSkeleton();
	const FTransform ToComponent = Mesh->GetComponentTransform().Inverse();
	TArray<FTransform>& Local = Mesh->BoneSpaceTransforms;
	CompSpace.SetNum(Local.Num());
	for (int32 i = 0; i < Local.Num(); ++i)
	{
		const int32 P = Ref.GetParentIndex(i);
		if (RefToDriven[i] != INDEX_NONE)
		{
			CompSpace[i] = Pose[RefToDriven[i]] * ToComponent;
			Local[i] = P == INDEX_NONE ? CompSpace[i] : CompSpace[i].GetRelativeTransform(CompSpace[P]);
		}
		else
		{
			CompSpace[i] = P == INDEX_NONE ? Local[i] : Local[i] * CompSpace[P];
		}
	}
	Mesh->MarkRefreshTransformDirty();
	if (!bChecked) Swap(LastPose, Pose);	// both buffers stay allocated
}

// ---- the mesh clamp: the drawn palm stays out of the drawn face (Web/art.js clipData / clipPush) ---------------------

namespace
{
	bool ClipEnabled()	// -SlapClip=0: off (the before/after screenshots)
	{
		static const bool bOn = [] { int32 V = 1; FParse::Value(FCommandLine::Get(), TEXT("SlapClip="), V); return V != 0; }();
		return bOn;
	}
	constexpr int32 MaxHandPoints = 300, MaxFacePoints = 2000;	// a stride sample: the browser keeps them all (a phone has 16 ms)
}

void ASlapAthleteVisual::BuildClip(const TArray<FTransform>& CS, const TArray<int32>& RefIdx)
{
	// The hands', the head's and the neck's (the lower jaw is skinned to it) vertices with weight >= 0.5, from LOD0's CPU
	// copy (the athletes' meshes keep one: Scripts/broadcast_fx.py sets Allow CPU Access), in their bone's frame.
	bClip = false;
	for (FClip& C : Clip) C = FClip();
	USkinnedAsset* Asset = Mesh->GetSkinnedAsset();
	const FSkeletalMeshRenderData* RD = Asset ? Asset->GetResourceForRendering() : nullptr;
	if (!RD || RD->LODRenderData.Num() == 0) return;
	const FSkeletalMeshLODRenderData& L = RD->LODRenderData[0];
	const FPositionVertexBuffer& PB = L.StaticVertexBuffers.PositionVertexBuffer;
	const FStaticMeshVertexBuffer& TB = L.StaticVertexBuffers.StaticMeshVertexBuffer;
	const FSkinWeightVertexBuffer& WB = *L.GetSkinWeightVertexBuffer();
	if (!PB.GetAllowCPUAccess() || !PB.GetVertexData() || !TB.GetAllowCPUAccess() || !WB.GetNeedsCPUAccess() || WB.GetNumVertices() != PB.GetNumVertices())
	{
		UE_LOG(LogTemp, Warning, TEXT("SlapAthleteVisual %s: LOD0 has no CPU copy (Allow CPU Access off): the drawn-hand clamp is off"), *BodyPrefix);
		return;
	}
	const TCHAR* Names[ClipParts] = { TEXT("hand_l"), TEXT("hand_r"), TEXT("head"), TEXT("neck") };
	TArray<int32> PartOf;
	PartOf.Init(INDEX_NONE, Bones.Num());
	for (int32 c = 0; c < ClipParts; ++c)
	{
		Clip[c].Driven = Bones.IndexOfByKey(FName(Names[c]));
		if (Clip[c].Driven == INDEX_NONE)
		{
			UE_LOG(LogTemp, Warning, TEXT("SlapAthleteVisual %s: no driven bone '%s': the drawn-hand clamp is off"), *BodyPrefix, Names[c]);
			return;
		}
		PartOf[Clip[c].Driven] = c;
	}
	const uint32 MaxInf = WB.GetMaxBoneInfluences();
	for (const FSkelMeshRenderSection& Sec : L.RenderSections)
	{
		for (uint32 v = Sec.BaseVertexIndex; v < Sec.BaseVertexIndex + Sec.NumVertices; ++v)
		{
			uint32 Best = 0, BestW = 0, Sum = 0;
			for (uint32 i = 0; i < MaxInf; ++i)
			{
				const uint32 W = WB.GetBoneWeight(v, i);
				Sum += W;
				if (W > BestW) { BestW = W; Best = WB.GetBoneIndex(v, i); }
			}
			if (Sum == 0 || 2 * BestW < Sum || !Sec.BoneMap.IsValidIndex(Best)) continue;
			const int32 RefBone = Sec.BoneMap[Best];
			const int32 k = RefToDriven.IsValidIndex(RefBone) ? RefToDriven[RefBone] : INDEX_NONE;
			const int32 c = k != INDEX_NONE ? PartOf[k] : INDEX_NONE;
			if (c == INDEX_NONE) continue;
			const FTransform& Rest = CS[RefIdx[k]];
			// world = pose location + pose rotation x (scale x the vertex in the bone's rest frame): the pose of this frame
			Clip[c].P.Add(FVector3f(Scales[k] * Rest.InverseTransformPosition(FVector(PB.VertexPosition(v)))));
			if (c >= ClipHead) Clip[c].N.Add(FVector3f(Rest.InverseTransformVectorNoScale(FVector(FVector3f(TB.VertexTangentZ(v)))).GetSafeNormal()));
		}
	}
	for (int32 c = 0; c < ClipParts; ++c)
	{
		FClip& C = Clip[c];
		const int32 Cap = c >= ClipHead ? MaxFacePoints : MaxHandPoints;
		if (C.P.Num() > Cap)	// an even stride over the vertex order
		{
			TArray<FVector3f> P, N;
			for (int32 i = 0; i < Cap; ++i)
			{
				const int32 j = (int32)((int64)i * C.P.Num() / Cap);
				P.Add(C.P[j]);
				if (C.N.Num()) N.Add(C.N[j]);
			}
			C.P = MoveTemp(P);
			C.N = MoveTemp(N);
		}
		if (C.P.Num() == 0)
		{
			if (c == ClipNeck) continue;	// no jaw skinned to the neck: the head alone
			UE_LOG(LogTemp, Warning, TEXT("SlapAthleteVisual %s: no vertices skinned to '%s': the drawn-hand clamp is off"), *BodyPrefix, Names[c]);
			return;
		}
		FVector Mean = FVector::ZeroVector;
		for (const FVector3f& p : C.P) Mean += FVector(p);
		C.C = c >= ClipHead ? Mean / C.P.Num() : FVector::ZeroVector;	// a hand: its reach from the bone
		for (const FVector3f& p : C.P) C.R = FMath::Max(C.R, FVector::Dist(FVector(p), C.C));
	}
	bClip = ClipEnabled();
	UE_LOG(LogTemp, Log, TEXT("SlapAthleteVisual %s: drawn-hand clamp %s (points hand %d/%d, head %d, neck %d; head radius %.1f cm)"), *BodyPrefix,
		bClip ? TEXT("on") : TEXT("off (-SlapClip=0)"), Clip[ClipHandL].P.Num(), Clip[ClipHandR].P.Num(), Clip[ClipHead].P.Num(), Clip[ClipNeck].P.Num(), Clip[ClipHead].R);
}

double ASlapAthleteVisual::ClipPush(const FClip& Hand, const FClip& Face, const FTransform& FaceT, FTransform& HandT) const
{
	// clipPush: move the drawn hand out along the mean normal of the face vertices it is under, by the deepest 5 % of its
	// vertices (+2 mm), up to 5 times. Worked in the face's frame (cm).
	const FQuat FR = FaceT.GetRotation(), HR = HandT.GetRotation();
	const FVector FL = FaceT.GetLocation(), HL = HandT.GetLocation();
	if (FVector::Dist(HL, FL + FR.RotateVector(Face.C)) > Face.R + Hand.R + 1.0) return 0.0;
	TArray<FVector3f, TInlineAllocator<MaxHandPoints>> Q;	// the hand's vertices in the face's frame
	for (const FVector3f& p : Hand.P) Q.Add(FVector3f(FR.UnrotateVector(HL + HR.RotateVector(FVector(p)) - FL)));
	const FVector3f C(Face.C);
	const float R2 = FMath::Square((float)Face.R + 2.f);
	TArray<float, TInlineAllocator<MaxHandPoints>> Dep;
	TArray<int32, TInlineAllocator<MaxHandPoints>> Under;
	FVector3f Push = FVector3f::ZeroVector;
	for (int32 It = 0; It < 5; ++It)
	{
		Dep.Reset();
		Under.Reset();
		for (const FVector3f& q0 : Q)
		{
			const FVector3f q = q0 + Push;
			if ((q - C).SizeSquared() > R2) continue;
			float BestD = UE_BIG_NUMBER;
			int32 Bj = 0;
			for (int32 j = 0; j < Face.P.Num(); ++j)
			{
				const float D2 = (Face.P[j] - q).SizeSquared();
				if (D2 < BestD) { BestD = D2; Bj = j; }
			}
			if (BestD > 16.f) continue;	// 4 cm from the skin: not at the face
			const float D = (Face.P[Bj] - q) | Face.N[Bj];
			if (D > 0.f) { Dep.Add(D); Under.Add(Bj); }
		}
		if (Dep.Num() < FMath::Max(1, Q.Num() / 100)) break;
		FVector3f N = FVector3f::ZeroVector;
		for (const int32 j : Under) N += Face.N[j];
		if (!N.Normalize()) break;
		Dep.Sort();
		Push += N * (Dep[FMath::Min(Dep.Num() - 1, (int32)(0.95f * Dep.Num()))] + 0.2f);
	}
	if (Push.IsZero()) return 0.0;
	HandT.SetLocation(HL + FR.RotateVector(FVector(Push)));
	return Push.Size();
}

void ASlapAthleteVisual::ClampHands(const TArray<double>& XPos, const TArray<double>& XQuat)
{
	ClipLastCm = 0.0;
	if (!bClip) return;
	if (!Opponent.IsValid())
	{
		for (TActorIterator<ASlapAthleteVisual> It(GetWorld()); It; ++It)
			if (*It != this && It->BodyPrefix != BodyPrefix) { Opponent = *It; break; }
	}
	const ASlapAthleteVisual* O = Opponent.Get();
	if (!O || !O->bReady || !O->bClip) return;
	for (const int32 f : { (int32)ClipHead, (int32)ClipNeck })	// the head, then the jaw (skinned to the neck), as app.js
	{
		const FClip& Face = O->Clip[f];
		if (Face.P.Num() == 0) continue;
		const int32 b = O->BodyIds[Face.Driven];
		if (4 * b + 3 >= XQuat.Num()) return;
		const FTransform FaceT(MjUtils::MjToUERotation(&XQuat[4 * b]) * O->Offsets[Face.Driven], MjUtils::MjToUEPosition(&XPos[3 * b]));	// this snapshot's pose
		for (const int32 h : { (int32)ClipHandL, (int32)ClipHandR })
			ClipLastCm += ClipPush(Clip[h], Face, FaceT, Pose[Clip[h].Driven]);
	}
	const double Now = GetWorld()->GetTimeSeconds();
	if (ClipLastCm > 0.0)
	{
		ClipEpisodeMax = FMath::Max(ClipEpisodeMax, ClipLastCm);
		ClipMaxCm = FMath::Max(ClipMaxCm, ClipLastCm);
		++ClipFrames;
		ClipLastAt = Now;
	}
	else if (ClipFrames > 0 && Now - ClipLastAt > 0.3)
	{
		if (SlapUi::HudLogLevel() >= 1) UE_LOG(LogTemp, Log, TEXT("SlapAthleteVisual %s: drawn hand held out of the drawn face for %d frames, up to %.1f cm"), *BodyPrefix, ClipFrames, ClipEpisodeMax);
		ClipFrames = 0;
		ClipEpisodeMax = 0.0;
	}
}
