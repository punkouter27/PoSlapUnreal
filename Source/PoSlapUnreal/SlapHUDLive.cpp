// ASlapHUD: the sidecars - the second screen (WebSocket), the commentary line, chat hype and the league.

#include "SlapHUD.h"

#include "HAL/FileManager.h"
#include "IWebSocket.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "SlapHUDInternal.h"
#include "SlapNames.h"
#include "WebSocketsModule.h"

using namespace SlapUi;

void ASlapHUD::LiveSend(const FString& Msg)
{
	// Azure Web PubSub's JSON subprotocol wraps every message for the "live" group; the local relay takes it bare
	Socket->Send(bAzure ? TEXT("{\"type\":\"sendToGroup\",\"group\":\"live\",\"dataType\":\"json\",\"noEcho\":true,\"data\":") + Msg + TEXT("}") : Msg);
}

void ASlapHUD::Live(const FSlapHud& H)
{
	// Second screen (Web/index.html?live=...): both athletes' joints at 30 Hz and every match event, so a phone draws the
	// match with the browser's own renderer instead of a Pixel Streaming video. Off unless switched on (VIEW, -SlapLive=)
	USlapController* C = Controller.Get();
	const double Now = GetWorld()->GetRealTimeSeconds();
	if (!bLive || !C)
	{
		if (Socket) { Socket->Close(); Socket.Reset(); }
		if (C) C->bLiveFeed = false;
		return;
	}
	if (!Socket && Now >= LiveRetry)
	{
		bAzure = LiveUrl.Contains(TEXT("webpubsub.azure.com"));
		Socket = FModuleManager::LoadModuleChecked<FWebSocketsModule>(TEXT("WebSockets")).CreateWebSocket(LiveUrl, bAzure ? TEXT("json.webpubsub.azure.v1") : TEXT(""));
		Socket->OnConnected().AddLambda([Url = LiveUrl] { UE_LOG(LogSlap, Log, TEXT("SlapHUD: second screen connected to %s"), *Url); });
		Socket->OnConnectionError().AddLambda([Url = LiveUrl](const FString& Why) { UE_LOG(LogSlap, Warning, TEXT("SlapHUD: second screen: no connection to %s (%s)"), *Url, *Why); });
		Socket->Connect();
		LiveRetry = Now + 5.0;
	}
	if (!Socket) return;
	if (!Socket->IsConnected())
	{
		if (Now >= LiveRetry) Socket.Reset();	// try again
		C->bLiveFeed = false;
		return;
	}
	LiveRetry = Now + 5.0;
	C->bLiveFeed = true;
	TArray<FString> Events;
	C->TakeEvents(Events);
	for (const FString& E : Events) LiveSend(TEXT("{\"t\":\"ev\",\"e\":") + E + TEXT("}"));
	if (Now - LiveSent < 1.0 / 30.0 || H.Pose.IsEmpty()) return;
	LiveSent = Now;
	TStringBuilder<2048> Msg;
	Msg.Appendf(TEXT("{\"t\":\"pose\",\"c\":[\"%s\",\"%s\"],\"m\":%d,\"r\":%d,\"s\":\"%s\",\"w\":%s,\"slow\":%.2f,"),
		*H.Card[0], *H.Card[1], H.Match, H.Round, *H.Striker, H.bWalkIn ? TEXT("true") : TEXT("false"), H.SlowMo);
	if (const double Wp = WinProb(H); Wp >= 0.0) Msg.Appendf(TEXT("\"wp\":%.3f,"), Wp);	// /api/match's win_prob (Web/live.py)
	Msg << TEXT("\"q\":[");
	for (int32 i = 0; i < H.Pose.Num(); ++i) Msg.Appendf(TEXT("%s%.4f"), i ? TEXT(",") : TEXT(""), H.Pose[i]);
	Msg << TEXT("]}");
	LiveSend(FString(Msg));
}

void ASlapHUD::LoadLeague()
{
	// training/scouting.py: Elo and tendencies per brain from every logged match; a missing file = no scouting line
	if (DisplayPassOn()) LoadScoutLogs();	// Unreal's own match logs: each character's slap side and KO rate
	const TSharedPtr<FJsonObject> J = Slap::ReadJson(FPaths::ProjectDir() / TEXT("training/models/league.json"));
	if (!J) return;
	const TSharedPtr<FJsonObject>* League = nullptr;
	if (!J->TryGetObjectField(TEXT("brains"), League)) return;
	Scout.Reset();
	for (const auto& B : (*League)->Values)	// key is FString on desktop, FSharedString on Android
	{
		const TSharedPtr<FJsonObject> O = B.Value->AsObject();
		if (!O) continue;
		LeagueElo.Add(FString(*B.Key), O->GetNumberField(TEXT("elo")));	// the scouting card between rounds
		LeagueKO.Add(FString(*B.Key), O->GetNumberField(TEXT("ko_rate")));
		Scout.Add(FString(*B.Key), FString::Printf(TEXT("BRAIN ELO %.0f   %d-%d-%d   KO %.0f%%   clean %.0f%%   %.1f m/s"), O->GetNumberField(TEXT("elo")),	// the brain's league Elo, not the character's
			(int32)O->GetNumberField(TEXT("wins")), (int32)O->GetNumberField(TEXT("losses")), (int32)O->GetNumberField(TEXT("draws")),
			100.0 * O->GetNumberField(TEXT("ko_rate")), 100.0 * O->GetNumberField(TEXT("clean_rate")), O->GetNumberField(TEXT("speed"))));
	}
}

void ASlapHUD::PollSidecars(double Now)
{
	if (Now - CommentaryPoll <= 0.5) return;	// Scripts/commentator.py writes one line per call
	CommentaryPoll = Now;
	const FString Path = FPaths::ProjectSavedDir() / TEXT("Commentary/latest.txt");
	const FDateTime Stamp = IFileManager::Get().GetTimeStamp(*Path);
	FString Raw;
	if (Stamp != CommentaryStamp && FFileHelper::LoadFileToString(Raw, *Path))
	{
		CommentaryStamp = Stamp;
		CommentaryAt = Now;
		Commentary.Reset();	// wrapped once here, drawn every frame
		FString Cur;
		TArray<FString> Words;
		Raw.TrimStartAndEnd().ParseIntoArrayWS(Words);
		for (const FString& Wd : Words)
		{
			if (Cur.Len() + Wd.Len() > 44) { Commentary.Add(Cur); Cur.Reset(); }
			Cur += (Cur.IsEmpty() ? TEXT("") : TEXT(" ")) + Wd;
		}
		if (!Cur.IsEmpty()) Commentary.Add(Cur);
	}
	// chat hype (Scripts/chat.py): fresh for 15 s after it last wrote
	const FString HypePath = FPaths::ProjectSavedDir() / TEXT("Commentary/hype.json");
	const FDateTime HypeStamp = IFileManager::Get().GetTimeStamp(*HypePath);
	Hype = 0.f;
	const TSharedPtr<FJsonObject> J = (FDateTime::UtcNow() - HypeStamp).GetTotalSeconds() < 15.0 ? Slap::ReadJson(HypePath) : nullptr;
	if (J)
	{
		Hype = FMath::Clamp((float)J->GetNumberField(TEXT("hype")), 0.f, 1.f);
		ChatLine = J->GetStringField(TEXT("line"));
		ChatAt = Now;
	}
}
