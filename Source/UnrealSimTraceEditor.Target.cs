using UnrealBuildTool;

public class UnrealSimTraceEditorTarget : TargetRules
{
	public UnrealSimTraceEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V7;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("UnrealSimTrace");
	}
}

