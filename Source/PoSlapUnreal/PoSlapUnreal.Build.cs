// Copyright Epic Games, Inc. All Rights Reserved.

using System;
using System.Diagnostics;
using UnrealBuildTool;

public class PoSlapUnreal : ModuleRules
{
	public PoSlapUnreal(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "URLab" });

		PrivateDependencyModuleNames.AddRange(new string[] { "NNE", "NNERuntimeBasicCpu", "Json", "Niagara", "Slate", "SlateCore", "InputCore", "AssetRegistry", "WebSockets" });

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
