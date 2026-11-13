// Athletes from the fingerprints and cards: joints, motors, geoms, brains (BindPolicy), and the Hill motor list.

#include "SlapController.h"

#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "NNEModelData.h"
#include "SlapControllerInternal.h"

using namespace SlapCtl;

void USlapController::BindHillMotors(mjModel* m, const FJsonObject* const (&Fps)[2])
{
	// The replay path runs no brains, so it has no athletes - but the Hill force-velocity cap (G4) is part of the
	// PHYSICS, not of the brain: Python's replay harness applies it (brain_cpu.muscle_step) while the replayed model
	// must match it (2026-10-29: without it the replay diverged 1.2 m over 5 s). So the replay binds the same motor
	// list the policy path does, straight from the fingerprints: per joint, the actuator, its joint's dof address and
	// the joint's top speed (fp["max_joint_speed_rad_s"]).
	HillMotors.Reset();
	const TArray<int32> Pelvises = Slap::CornerPelvises(m);
	for (int32 i = 0; i < Pelvises.Num(); ++i)
	{
		const FString Prefix = Slap::CornerPrefix(Slap::Name(m, mjOBJ_BODY, Pelvises[i]));	// "..._red_pelvis" -> "..._red_"
		const FJsonObject& Fp = *Fps[FMath::Min(i, 1)];
		const TArray<TSharedPtr<FJsonValue>>& Order = Fp.GetArrayField(TEXT("joint_order"));
		for (int32 j = 0; j < Order.Num(); ++j)
		{
			const FString J = Order[j]->AsString();
			const int32 Act = mj_name2id(m, mjOBJ_ACTUATOR, TCHAR_TO_UTF8(*(Prefix + J)));
			if (Act < 0) continue;	// this corner does not have that joint (a mixed pairing)
			FMotor Motor;
			Motor.Act = Act;
			Motor.Vadr = m->jnt_dofadr[m->actuator_trnid[2 * Act]];
			Motor.Vmax = JointSpeed(Fp, j);
			HillMotors.Add(Motor);
		}
	}
}

void USlapController::BindPolicy(mjModel* m, const FJsonObject* const (&Fps)[2])
{
	// each corner its own character: joint ranges, stand pose, speed limit and card come from that athlete's fingerprint
	FString Tape[2] = { TEXT("?"), TEXT("?") }, Display[2] = { TEXT("?"), TEXT("?") }, CardName[2];
	// red, then blue, by name: a cooked build compiles the components in another order (blue's body first on Android),
	// and everything indexed by corner (walk-in side, fingerprint, turns) assumes 0 = red
	HillMotors.Reset();	// a second Bind must not apply the Hill cap twice
	const TArray<int32> Pelvises = Slap::CornerPelvises(m);
	for (const int32 b : Pelvises)
	{
		const FString BodyName = Slap::Name(m, mjOBJ_BODY, b);
		const int32 c = FMath::Min(Athletes.Num(), 1);
		const FJsonObject& Fp = *Fps[c];
		const TArray<TSharedPtr<FJsonValue>>& Order = Fp.GetArrayField(TEXT("joint_order"));
		const TArray<TSharedPtr<FJsonValue>>& Range = Fp.GetArrayField(TEXT("ctrl_range_rad"));
		const TSharedPtr<FJsonObject> StandRad = Fp.GetObjectField(TEXT("stand_pose_rad"));
		// the character card is the source of truth (the fingerprint keeps a build-time copy): fatigue, display name
		CardName[c] = Fp.GetObjectField(TEXT("card"))->GetStringField(TEXT("name"));
		// the card file, else the fingerprint's build-time copy of it: a build that staged only the fingerprint must not
		// bind an athlete with a zero strike mass (no slap impulse, so no head snap, no stagger and no KO)
		TSharedPtr<FJsonObject> Card = LoadJson(TEXT("training/characters/") + CardName[c] + TEXT(".json"));
		if (!Card) Card = Fp.GetObjectField(TEXT("card"));
		if (Card)
		{
			Tape[c] = FString::Printf(TEXT("%.2f m  %.0f kg  STR %.2f  SPD %.2f  TGH %.2f"), Card->GetNumberField(TEXT("height_m")), Card->GetNumberField(TEXT("mass_kg")),
				Card->GetNumberField(TEXT("strength")), Card->GetNumberField(TEXT("speed")), Card->GetNumberField(TEXT("toughness")));
			Display[c] = Card->GetStringField(TEXT("display"));
		}
		FAthlete& A = Athletes.AddDefaulted_GetRef();
		for (int32 j = 0; j < Order.Num(); ++j)
		{
			A.Vmax.Add(JointSpeed(Fp, j));	// Hill's top speed (per joint; one number in older fingerprints)
			A.MaxStep.Add(A.Vmax.Last() * 0.02);
		}
		A.Prefix = Slap::CornerPrefix(BodyName);
		A.Name = A.Prefix.LeftChop(1);
		A.Name = A.Name.Mid(A.Name.Find(TEXT("_"), ESearchCase::CaseSensitive, ESearchDir::FromEnd) + 1);	// "Arena_red" -> "red"
		A.Pelvis = b;
		A.Chest = mj_name2id(m, mjOBJ_BODY, TCHAR_TO_UTF8(*(A.Prefix + TEXT("chest"))));	// eye contact aims in the CHEST frame (the neck hinges hang off it; the waist ball separates it from the pelvis)
		const int32 Root = mj_name2id(m, mjOBJ_JOINT, TCHAR_TO_UTF8(*(A.Prefix + TEXT("root"))));
		A.RootQ = m->jnt_qposadr[Root];
		A.RootV = m->jnt_dofadr[Root];
		for (int32 j = 0; j < Order.Num(); ++j)
		{
			const FString J = Order[j]->AsString();
			const int32 Act = mj_name2id(m, mjOBJ_ACTUATOR, TCHAR_TO_UTF8(*(A.Prefix + J)));
			const int32 Jnt = m->actuator_trnid[2 * Act];
			A.Act.Add(Act);
			A.Qadr.Add(m->jnt_qposadr[Jnt]);
			A.Vadr.Add(m->jnt_dofadr[Jnt]);
			A.Lo.Add(Range[j]->AsArray()[0]->AsNumber());
			A.Hi.Add(Range[j]->AsArray()[1]->AsNumber());
			double Rad = 0.0;	// exact radians from Python; a degree conversion here can differ in the last bit
			StandRad->TryGetNumberField(J, Rad);
			A.Stand.Add(Rad);
			if (J.StartsWith(TEXT("neck")))
			{
				A.NeckAct.Add(Act);
				A.Fr0.Append({ m->actuator_forcerange[2 * Act], m->actuator_forcerange[2 * Act + 1] });
				if (J == TEXT("neck_turn")) A.YawIdx = j;
			}
			if (J == TEXT("hip_flex_l")) A.HipL = j;
			else if (J == TEXT("hip_flex_r")) A.HipR = j;
			else if (J == TEXT("knee_l")) A.KneeL = j;
			else if (J == TEXT("knee_r")) A.KneeR = j;
			else if (J == TEXT("ankle_flex_l")) A.AnkleL = j;
			else if (J == TEXT("ankle_flex_r")) A.AnkleR = j;
			else if (J == TEXT("shoulder_swing_l")) A.ShSwingL = j;
			else if (J == TEXT("shoulder_swing_r")) A.ShSwingR = j;
			else if (J == TEXT("waist_twist")) A.WaistTwist = j;
			else if (J == TEXT("elbow_r")) A.ElbowR = j;
		}
		for (int32 j = 0; j < A.Act.Num() && j < A.Vmax.Num(); ++j)
		{
			FMotor Motor;	// the Hill cap's list (ApplyHill): this joint's actuator, dof address and top speed
			Motor.Act = A.Act[j];
			Motor.Vadr = A.Vadr[j];
			Motor.Vmax = A.Vmax[j];
			HillMotors.Add(Motor);
		}
		for (int32 k = 0; k < m->nbody; ++k)	// the brain X-ray's ghost: every body of this athlete, linked to its parent
		{
			if (m->body_rootid[k] != b) continue;
			A.BodyParent.Add(A.Bodies.Find(m->body_parentid[k]));
			A.Bodies.Add(k);
		}
		A.Goal = A.Stand;
		A.PrevAction.Init(0.f, Order.Num());
		A.Action.SetNumZeroed(Order.Num());	// this athlete's own brain output buffer
		A.WalkAction.SetNumZeroed(Order.Num());	// and the walking brain's
		if (Card)
		{
			A.StaminaPerStrike = Card->GetNumberField(TEXT("stamina_per_strike"));
			A.NeckDamage = Card->GetNumberField(TEXT("neck_damage_per_snap"));
			A.StrikeMass = Card->GetNumberField(TEXT("strike_mass"));
			Card->TryGetNumberField(TEXT("chin"), A.Chin);	// rules 2: the stun threshold's scale (absent = 1)
			Card->TryGetNumberField(TEXT("faint_chance_per_turn"), A.FaintChance);
			Card->TryGetNumberField(TEXT("faint_seconds"), A.FaintSeconds);
			Card->TryGetNumberField(TEXT("faint_strength"), A.FaintStrength);
			double Walk = 1.2;	// card walk_speed_mps scales the walk-in command (locomotion.py speed_scale: / 1.2)
			Card->TryGetNumberField(TEXT("walk_speed_mps"), Walk);
			A.WalkSpeed = WalkSpeed * Walk / 1.2;
		}
		for (const int32 Act : A.Act) A.FrAll.Append({ m->actuator_forcerange[2 * Act], m->actuator_forcerange[2 * Act + 1] });
		auto Geom = [&](const TCHAR* N) { return mj_name2id(m, mjOBJ_GEOM, TCHAR_TO_UTF8(*(A.Prefix + N))); };
		A.Feet = { Geom(TEXT("foot_l")), Geom(TEXT("foot_r")) };	// the foot box is the sole (Blueprint 2.1)
		A.Head = Geom(TEXT("head"));
		A.HeadBody = m->geom_bodyid[A.Head];
		A.Face = { A.Head };	// physics 3 adds the jaw capsules (build_body.face_parts); v1/v2 have none
		for (const TCHAR* J : { TEXT("jaw_l"), TEXT("jaw_r") }) if (Geom(J) >= 0) A.Face.Add(Geom(J));
		A.FaceZone = mj_name2id(m, mjOBJ_SITE, TCHAR_TO_UTF8(*(A.Prefix + TEXT("face_zone"))));
		A.Hand = Geom(TEXT("hand_r"));
		A.Palms = { Geom(TEXT("hand_l")), A.Hand };
	}
	Floor = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("floor"));
	Podium = Slap::Find(m, mjOBJ_GEOM, m->ngeom, TEXT("podium"));
	bInTurn = false;
	MatchNo = 0;
	HoldLeft = 0;

	// one brain per corner (roster): the asset, or an .onnx file from the command line (tournament replays, checks)
	auto FromFile = [this](const TCHAR* Key, UNNEModelData* Asset, FString& Label) -> UNNEModelData*
	{
		FString Path;
		TArray<uint8> Bytes;
		Label = Asset ? Asset->GetName() : TEXT("-");
		if (!FParse::Value(FCommandLine::Get(), Key, Path)) return Asset;
		if (!FFileHelper::LoadFileToArray(Bytes, *FPaths::Combine(FPaths::ProjectDir(), Path)))
		{
			UE_LOG(LogSlap, Error, TEXT("%s%s: no such file"), Key, *Path);
			return Asset;
		}
		UNNEModelData* Data = NewObject<UNNEModelData>(this);
		Data->Init(TEXT("onnx"), Bytes);
		LoadedBrains.Add(Data);
		Label = FPaths::GetBaseFilename(Path);
		return Data;
	};
	FString Labels[2];
	UNNEModelData* Red = FromFile(TEXT("SlapRedBrain="), Brain.Get(), Labels[0]);
	UNNEModelData* Blue = FromFile(TEXT("SlapBlueBrain="), BlueBrain.Get(), Labels[1]);
	if (!Blue)	// no brain of his own: red's
	{
		Blue = Red;
		Labels[1] = Labels[0];
	}
	for (int32 a = 0; a < Athletes.Num(); ++a)
	{
		FAthlete& A = Athletes[a];
		FNet N;
		if (!MakeNet(a == 1 ? Blue : Red, N)) continue;	// athletes bind red, then blue (BindPolicy)
		A.Net = N.Net;
		A.NumIn = N.NumIn;
		A.NumOut = N.NumOut;
		UE_LOG(LogSlap, Log, TEXT("Policy: %s brain %s reads %d inputs"), *A.Name, *Labels[FMath::Min(a, 1)], A.NumIn);
	}
	// The walk-in on each character's walking brain (S2, the cooked /Game/Brains/<card>_S2, so Android has it too; user
	// 2026-10-29: the walk-in is pure RL); a character without one gets the scripted stride. -SlapWalkBrain=0: scripted for all
	FString WalkFlag;
	const bool bWalkBrains = !(FParse::Value(FCommandLine::Get(), TEXT("SlapWalkBrain="), WalkFlag) && WalkFlag == TEXT("0"));
	FString Walkers;
	for (int32 a = 0; a < Athletes.Num() && bWalkBrains; ++a)
	{
		const FString Name = CardName[FMath::Min(a, 1)] + TEXT("_S2");
		UNNEModelData* Data = LoadObject<UNNEModelData>(nullptr, *FString::Printf(TEXT("/Game/Brains/%s.%s"), *Name, *Name), nullptr, LOAD_NoWarn);
		FNet N;
		if (!Data || !MakeNet(Data, N)) continue;
		LoadedBrains.Add(Data);
		Athletes[a].WalkNet = N.Net;
		Athletes[a].WalkIn = N.NumIn;
		Athletes[a].WalkOut = N.NumOut;
		Walkers += (Walkers.IsEmpty() ? TEXT("") : TEXT(", ")) + Athletes[a].Name;
	}
	UE_LOG(LogSlap, Log, TEXT("Walk-in: walking brains for %s"), Walkers.IsEmpty() ? TEXT("nobody (scripted stride)") : *Walkers);
	if (GhostD) mj_deleteData(GhostD);
	GhostD = mj_makeData(m);
	UE_LOG(LogSlap, Log, TEXT("Policy: %d athletes (%s vs %s), %d joints each, brain %s, defender role %d"), Athletes.Num(), *CardName[0], *CardName[1], Athletes.Num() ? Athletes[0].Action.Num() : 0,
		Athletes.Num() && Athletes[0].Net ? TEXT("loaded") : TEXT("MISSING (holding stand pose)"), DefenderRole);
	SetHud([&](FSlapHud& H)
	{
		for (int32 a = 0; a < FMath::Min(2, Athletes.Num()); ++a)
		{
			H.Name[a] = Display[a] + TEXT(" ") + Athletes[a].Name.ToUpper();
			H.Brain[a] = Labels[a];
			H.Tape[a] = Tape[a];
			H.Card[a] = CardName[a];
			H.StrikeMass[a] = Athletes[a].StrikeMass;
			H.GhostParent[a] = Athletes[a].BodyParent;
		}
		H.WalkNote = Walkers;
	});
}

