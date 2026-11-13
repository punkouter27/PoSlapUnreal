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

		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "URLab" });

		PrivateDependencyModuleNames.AddRange(new string[] { "NNE", "NNERuntimeBasicCpu", "Json", "Niagara", "Slate", "SlateCore", "InputCore", "AssetRegistry", "WebSockets", "Synthesis", "RHI",
			"LevelSequence", "MovieScene", "CinematicCamera", "Text3D" });

		// Build stamp YYMMDD (AGENTS.md): last git commit date, else today.
		string Stamp = DateTime.Now.ToString("yyMMdd");
		try
		{
			var Git = Process.Start(new ProcessStartInfo("git", "log -1 --format=%cd --date=format:%y%m%d")
				{ WorkingDirectory = ModuleDirectory, RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true });
			string Out = Git.StandardOutput.ReadToEnd().Trim();
			Git.WaitForExit();
			if (Out.Length == 6) Stamp = Out;
		}
		catch (Exception) {}
		PublicDefinitions.Add("POSLAP_BUILD_VERSION=\"" + Stamp + "\"");
	}
}
