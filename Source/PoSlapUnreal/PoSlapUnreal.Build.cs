// Copyright Epic Games, Inc. All Rights Reserved.

using System;
using System.Diagnostics;
using UnrealBuildTool;

public class PoSlapUnreal : ModuleRules
{
	public PoSlapUnreal(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		// One translation unit per file: the HUD's .cpp files each keep their own file-local colours and helpers (Amber, Red,
		// Hi, ...), which collide once UBT merges committed files into a unity blob (a clean checkout failed to build, 2026-11-12)
		bUseUnity = false;

		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "URLab", "DeveloperSettings" });

		PrivateDependencyModuleNames.AddRange(new string[] { "NNE", "NNERuntimeBasicCpu", "Json", "Niagara", "Slate", "SlateCore", "InputCore", "AssetRegistry", "WebSockets", "Synthesis", "RHI",
			"AudioExtensions",	// USlapVoiceWave (SlapVoiceSynth.h) subclasses USoundWaveProcedural, whose vtable needs IAudioProxyDataFactory
			"LevelSequence", "MovieScene", "CinematicCamera", "Text3D" });

		// One version stamp (Po Game Standard): UTC yyMMdd of the HEAD commit (the web build uses the same rule), else today (UTC).
		string Stamp = DateTime.UtcNow.ToString("yyMMdd");
		try
		{
			var Git = Process.Start(new ProcessStartInfo("git", "log -1 --format=%ct")
				{ WorkingDirectory = ModuleDirectory, RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true });
			string Out = Git.StandardOutput.ReadToEnd().Trim();
			Git.WaitForExit();
			long Secs;
			if (Git.ExitCode == 0 && long.TryParse(Out, out Secs)) Stamp = DateTimeOffset.FromUnixTimeSeconds(Secs).UtcDateTime.ToString("yyMMdd");
		}
		catch (Exception) {}
		PublicDefinitions.Add("POSLAP_BUILD_VERSION=\"" + Stamp + "\"");
	}
}
