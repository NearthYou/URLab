#include "SimTraceRuntimeConfig.h"

#include "Misc/Parse.h"

FSimTraceRuntimeConfig FSimTraceRuntimeConfig::Parse(const TCHAR* CommandLine)
{
	FSimTraceRuntimeConfig Config;

	FString Mode;
	if (FParse::Value(CommandLine, TEXT("SimTraceMode="), Mode))
	{
		Mode = Mode.ToLower();
		if (Mode == TEXT("bot"))
		{
			Config.Mode = ESimTraceMode::Bot;
		}
		else if (Mode == TEXT("input-replay"))
		{
			Config.Mode = ESimTraceMode::InputReplay;
		}
		else if (Mode == TEXT("native-replay"))
		{
			Config.Mode = ESimTraceMode::NativeReplay;
		}
	}

	FParse::Value(CommandLine, TEXT("SimTraceSeed="), Config.Seed);
	FParse::Value(CommandLine, TEXT("SimTraceBatchCount="), Config.BatchCount);

	int32 CaptureValue = Config.bCapture ? 1 : 0;
	FParse::Value(CommandLine, TEXT("SimTraceCapture="), CaptureValue);
	Config.bCapture = CaptureValue != 0;

	float MaxSeconds = static_cast<float>(Config.MaxSeconds);
	FParse::Value(CommandLine, TEXT("SimTraceMaxSeconds="), MaxSeconds);
	Config.MaxSeconds = MaxSeconds;

	FParse::Value(CommandLine, TEXT("SimTraceInput="), Config.InputPath);
	FParse::Value(CommandLine, TEXT("SimTraceReplay="), Config.ReplayName);

	Config.BatchCount = FMath::Max(1, Config.BatchCount);
	Config.MaxSeconds = FMath::Max(1.0, Config.MaxSeconds);
	return Config;
}

