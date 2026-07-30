using UnrealBuildTool;

public class UnrealSimTrace : ModuleRules
{
	public UnrealSimTrace(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"EnhancedInput",
			"Json",
			"JsonUtilities"
		});

		PrivateDependencyModuleNames.AddRange(new[]
		{
			"ImageCore",
			"ImageWrapper",
			"RenderCore",
			"RHI",
			"NetworkReplayStreaming",
			"LocalFileNetworkReplayStreaming"
		});
	}
}

