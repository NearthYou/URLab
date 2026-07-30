#pragma once

#include "CoreMinimal.h"
#include "SimTraceTypes.h"

struct UNREALSIMTRACE_API FSimTraceRuntimeConfig
{
	ESimTraceMode Mode = ESimTraceMode::Human;
	int32 Seed = 1000;
	int32 BatchCount = 1;
	bool bCapture = true;
	double MaxSeconds = 60.0;
	FString InputPath;
	FString ReplayName;

	static FSimTraceRuntimeConfig Parse(const TCHAR* CommandLine);
};

