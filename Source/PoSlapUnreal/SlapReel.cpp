// The highlight reel's file and the controller's recorder (SlapReel.h). Display only.

#include "SlapReel.h"

#include "Misc/FileHelper.h"
#include "Serialization/MemoryReader.h"
#include "Serialization/MemoryWriter.h"
#include "SlapController.h"
#include "SlapControllerInternal.h"

int32 FSlapReel::PlayStep = 0;

FSlapReel& FSlapReel::Playing()
{
	static FSlapReel Reel;
	return Reel;
}

bool FSlapReel::Save(const FString& Path) const
{
	if (!IsValid()) return false;
	TArray<uint8> Bytes;
	FMemoryWriter Ar(Bytes);
	uint32 M = Magic;
	int32 Nb = NumBodies, Ns = NumSteps, T = Touch, St = Striker;
	float Dt = Timestep;
	Ar << M << Nb << Ns << T << St << Dt;
	Ar.Serialize(const_cast<float*>(Data.GetData()), Data.Num() * sizeof(float));
	return FFileHelper::SaveArrayToFile(Bytes, *Path);
}

bool FSlapReel::Load(const FString& Path)
{
	*this = FSlapReel();
	TArray<uint8> Bytes;
	if (!FFileHelper::LoadFileToArray(Bytes, *Path)) return false;
	FMemoryReader Ar(Bytes);
	uint32 M = 0;
	Ar << M << NumBodies << NumSteps << Touch << Striker << Timestep;
	const int64 Want = (int64)NumSteps * Stride();
	if (M != Magic || NumBodies <= 0 || NumBodies > 4096 || NumSteps <= 0 || NumSteps > 1000000 || Ar.TotalSize() - Ar.Tell() != Want * (int64)sizeof(float))
	{
		*this = FSlapReel();
		return false;
	}
	Data.SetNumUninitialized(Want);
	Ar.Serialize(Data.GetData(), Want * sizeof(float));
	return true;
}

void USlapController::ReelBind(const mjModel* m)
{
	// allocated once here (Bind): the step loop below only copies into it
	FScopeLock L(&ReelLock);
	ReelBodies = m->nbody;
	ReelDt = (float)m->opt.timestep;
	ReelBuf.SetNumZeroed(FSlapReel::Steps * (ReelBodies * 7 + FSlapReel::Extra));
	ReelHead = ReelCount = 0;
	ReelTouchAge = -1;
}

void USlapController::ReelStep(const mjData* d, double PalmSpeed, double HeadG, bool bTouchedNow, int32 Striker)
{
	if (ReelBodies == 0) return;
	FScopeLock L(&ReelLock);	// uncontended except in the frame that saves a reel
	float* Out = &ReelBuf[ReelHead * (ReelBodies * 7 + FSlapReel::Extra)];
	for (int32 b = 0; b < ReelBodies; ++b)
	{
		for (int32 i = 0; i < 3; ++i) *Out++ = (float)d->xpos[3 * b + i];
		for (int32 i = 0; i < 4; ++i) *Out++ = (float)d->xquat[4 * b + i];
	}
	*Out++ = (float)PalmSpeed;
	*Out++ = (float)HeadG;
	ReelHead = (ReelHead + 1) % FSlapReel::Steps;
	ReelCount = FMath::Min(ReelCount + 1, FSlapReel::Steps);
	if (bTouchedNow) { ReelTouchAge = 0; ReelStriker = Striker; }
	else if (ReelTouchAge >= 0 && ++ReelTouchAge >= FSlapReel::Steps) ReelTouchAge = -1;	// the touch has left the window
}

void USlapController::TakeReel(FSlapReel& Out) const
{
	FScopeLock L(&ReelLock);
	Out = FSlapReel();
	if (ReelCount == 0) return;
	const int32 Stride = ReelBodies * 7 + FSlapReel::Extra;
	Out.NumBodies = ReelBodies;
	Out.NumSteps = ReelCount;
	Out.Timestep = ReelDt;
	Out.Touch = ReelTouchAge >= 0 ? ReelCount - 1 - ReelTouchAge : -1;
	Out.Striker = ReelStriker;
	Out.Data.SetNumUninitialized(ReelCount * Stride);
	const int32 Oldest = (ReelHead - ReelCount + FSlapReel::Steps) % FSlapReel::Steps;	// the oldest step
	for (int32 k = 0; k < ReelCount; ++k)
		FMemory::Memcpy(&Out.Data[k * Stride], &ReelBuf[((Oldest + k) % FSlapReel::Steps) * Stride], Stride * sizeof(float));
}
