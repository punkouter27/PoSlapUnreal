// ASlapHUD: broadcast science (KO danger, head impact, kinetic chain, the palm trail) and the brain X-ray. Display only.

#include "SlapHUD.h"

#include "Algo/Sort.h"
#include "Components/PoseableMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "SlapHUDInternal.h"

using namespace SlapUi;

void ASlapHUD::DrawScience(const FSlapHud& H)
{
	if (H.bWalkIn) return;
	const double Now = GetWorld()->GetTimeSeconds();
	const int32 Sk = Team(H.Striker), Def = 1 - Sk;
	auto Heat = [](double K) { return FLinearColor::LerpUsingHSV(FLinearColor(0.35f, 0.9f, 0.45f), FLinearColor(1.f, 0.25f, 0.2f), FMath::Clamp((float)K, 0.f, 1.f)); };

	// KO danger: from each athlete's centre of mass (over the floor) to his capture point; the cross is where he would have
	// to step to stop. Green inside the soles, red 15 cm outside
	for (int32 a = 0; a < 2; ++a)
	{
		const FVector C = Canvas->Project(FVector(H.Com[a].X, H.Com[a].Y, 1.0)), P = Canvas->Project(FVector(H.Capture[a].X, H.Capture[a].Y, 1.0));
		if (C.Z <= 0 || P.Z <= 0) continue;
		const FLinearColor Col = Heat(H.Danger[a]);
		DrawLine(C.X, C.Y, P.X, P.Y, Col, 2.f * S);
		const float R = 6.f * S;
		DrawLine(P.X - R, P.Y - R, P.X + R, P.Y + R, Col, 3.f * S);
		DrawLine(P.X - R, P.Y + R, P.X + R, P.Y - R, Col, 3.f * S);
		if (H.Danger[a] > 0.5 && Body[a].IsValid())
		{
			const FVector Hd = Canvas->Project(Body[a]->GetBoneLocation(BoneHead) + FVector(0.f, 0.f, 30.f));
			if (Hd.Z > 0) Text(FString::Printf(TEXT("KO DANGER %d%%"), FMath::RoundToInt(100.0 * H.Danger[a])), Hd.X, Hd.Y, Col.ToFColor(true), 1.2f * S, true);
		}
	}

	// the striker's palm, last 0.35 s: brighter where it was faster
	if (TrailOwner != Sk) { Trail.Reset(); TrailOwner = Sk; }
	if (Body[Sk].IsValid()) Trail.Emplace(Now, Body[Sk]->GetBoneLocation(BoneHandR));
	Trail.RemoveAll([Now](const TPair<double, FVector>& P) { return Now - P.Key > 0.35; });
	for (int32 i = 1; i < Trail.Num(); ++i)
	{
		const double Dt = FMath::Max(Trail[i].Key - Trail[i - 1].Key, 1e-4);
		const float V = (Trail[i].Value - Trail[i - 1].Value).Size() / 100.0 / Dt;	// m/s (game time: slow motion slows it too)
		const FVector A = Canvas->Project(Trail[i - 1].Value), B = Canvas->Project(Trail[i].Value);
		if (A.Z <= 0 || B.Z <= 0) continue;
		const float Age = 1.f - (Now - Trail[i].Key) / 0.35f;
		const float K = FMath::Clamp(V / (float)CleanSpeed, 0.f, 1.f);
		DrawLine(A.X, A.Y, B.X, B.Y, FLinearColor(1.f, 0.85f + 0.15f * K, 0.4f + 0.6f * K, Age), (2.f + 5.f * K) * S);
	}

	// head impact and kinetic chain: right column, under the menu button and the chat box
	const float W = 192.f * S, X = Canvas->ClipX - W - 8.f * S, Y = PanelBottom + 6.f * S, BarW = W - 24.f * S;	// under the name plates
	DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.6f), X, Y, W, 184.f * S);
	Text(FString::Printf(TEXT("HEAD IMPACT  %s"), *H.Defender.ToUpper()), X + 12.f * S, Y + 6.f * S, Grey, S);
	auto Bar = [&](float By, double V, double Max, double Line, const FString& Label, const TCHAR* LineLabel)
	{
		DrawRect(FLinearColor(1.f, 1.f, 1.f, 0.12f), X + 12.f * S, By, BarW, 10.f * S);
		DrawRect(Heat(V / Line), X + 12.f * S, By, BarW * FMath::Clamp((float)(V / Max), 0.f, 1.f), 10.f * S);
		const float Lx = X + 12.f * S + BarW * (Line / Max);
		DrawLine(Lx, By - 3.f * S, Lx, By + 13.f * S, FLinearColor::White, 2.f * S);
		Text(Label, X + 12.f * S, By + 13.f * S, FColor::White, 1.1f * S);
		Text(LineLabel, Lx, By + 15.f * S, Grey, 0.75f * S, true);
	};
	Bar(Y + 26.f * S, H.Hic, 1000.0, 700.0, FString::Printf(TEXT("HIC %.0f"), H.Hic), TEXT("crash-test limit"));
	Bar(Y + 64.f * S, H.Bric, 1.5, 1.0, FString::Printf(TEXT("BrIC %.2f"), H.Bric), TEXT("critical"));
	const float Cy = Y + 112.f * S;
	if (H.ChainOrder < 0)
	{
		Text(TEXT("KINETIC CHAIN"), X + 12.f * S, Cy, Grey, S);
		Text(TEXT("waiting for the touch"), X + 12.f * S, Cy + 20.f * S, Grey, 0.9f * S);
		return;
	}
	Text(FString::Printf(TEXT("KINETIC CHAIN  %d / 4 in order"), H.ChainOrder), X + 12.f * S, Cy, H.ChainOrder >= 3 ? Gold : Grey, S);
	// each link at the time it peaked, joined hips to hand; the axis spans the earliest peak (left) to the touch (right)
	float Span = 0.05f;
	for (const double C : H.Chain) Span = FMath::Max(Span, -(float)C);
	Text(FString::Printf(TEXT("peaks over the last %.0f ms"), 1000.f * Span), X + 12.f * S, Cy + 15.f * S, Grey, 0.75f * S);
	static const TCHAR* Links[5] = { TEXT("H"), TEXT("T"), TEXT("S"), TEXT("E"), TEXT("P") };
	FVector2D Prev = FVector2D::ZeroVector;
	for (int32 k = 0; k < 5; ++k)
	{
		const FVector2D Pt(X + 12.f * S + BarW * (1.f + (float)H.Chain[k] / Span), Cy + 30.f * S + 9.f * S * k);
		if (k > 0) DrawLine(Prev.X, Prev.Y, Pt.X, Pt.Y, H.Chain[k - 1] <= H.Chain[k] ? FLinearColor(0.35f, 0.9f, 0.45f) : FLinearColor(1.f, 0.3f, 0.25f), 2.f * S);
		DrawRect(FLinearColor(Gold), Pt.X - 3.f * S, Pt.Y - 3.f * S, 6.f * S, 6.f * S);
		Text(Links[k], Pt.X + 5.f * S, Pt.Y - 7.f * S, FColor::White, 0.75f * S);
		Prev = Pt;
	}
}

void ASlapHUD::DrawXRay(const FSlapHud& H)
{
	// each brain's target pose (where it is steering the body) as a stick figure, and a ring on what it just reacted to:
	// its own body parts, or the opponent's head and hand. Sizes are each group's share of the reaction (sums to 1)
	static const TCHAR* GroupName[10] = { TEXT("own legs"), TEXT("own arms"), TEXT("trunk"), TEXT("balance"), TEXT("rhythm"),
		TEXT("opp head"), TEXT("opp hand"), TEXT("memory"), TEXT("fatigue"), TEXT("own palm") };
	static const TArray<FName> GroupBones[10] = { { "thigh_l", "thigh_r", "shin_l", "shin_r", "foot_l", "foot_r" },
		{ "upperarm_l", "upperarm_r", "forearm_l", "forearm_r", "hand_l" }, { "belly", "chest", "neck" }, { "pelvis" }, {},
		{ "head" }, { "hand_r" }, { "head" }, { "chest" }, { "hand_r" } };
	for (int32 a = 0; a < 2; ++a)
	{
		const FLinearColor Team(TeamColor[a]);
		const TArray<FVector>& G = H.Ghost[a];
		for (int32 i = 0; i < G.Num() && i < H.GhostParent[a].Num(); ++i)
		{
			const int32 P = H.GhostParent[a][i];
			if (P < 0) continue;
			const FVector A = Canvas->Project(G[i]), B = Canvas->Project(G[P]);
			if (A.Z > 0 && B.Z > 0) DrawLine(A.X, A.Y, B.X, B.Y, Team.CopyWithNewOpacity(0.7f), 3.f * S);
		}
		for (int32 g = 0; g < 10; ++g)
		{
			const float F = H.Focus[a][g];
			if (F < 0.05f) continue;
			const int32 On = g == 5 || g == 6 ? 1 - a : a;	// the opponent's head and hand are on his body
			if (!Body[On].IsValid()) continue;
			for (const FName& Bone : GroupBones[g])
			{
				const FVector C = Canvas->Project(Body[On]->GetBoneLocation(Bone));
				if (C.Z <= 0) continue;
				const float R = (6.f + 36.f * F) * S;
				Ring(this, C, R, 24, Team.CopyWithNewOpacity(0.35f + 0.65f * F), 2.f * S);
			}
		}
		// the top three, in words, above the bottom panels
		int32 Order[10] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 };
		Algo::Sort(Order, [&](int32 L, int32 R) { return H.Focus[a][L] > H.Focus[a][R]; });
		FString Row = FString::Printf(TEXT("%s REACTS TO "), a ? TEXT("BLUE") : TEXT("RED"));
		for (int32 k = 0; k < 3 && H.Focus[a][Order[k]] > 0.f; ++k) Row += FString::Printf(TEXT(" %s %d%%"), GroupName[Order[k]], FMath::RoundToInt(100.f * H.Focus[a][Order[k]]));
		const float Ry = Bot - (bShowTelemetry ? 100.f : 60.f) * S + 22.f * S * a;	// above the telemetry strips
		DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 8.f * S, Ry - 2.f * S, 380.f * S, 20.f * S);
		Text(Row, 16.f * S, Ry, TeamColor[a], S);
	}
}

