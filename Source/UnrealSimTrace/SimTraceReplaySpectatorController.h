#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"
#include "SimTraceReplaySpectatorController.generated.h"

UCLASS()
class UNREALSIMTRACE_API ASimTraceReplaySpectatorController : public APlayerController
{
	GENERATED_BODY()

public:
	ASimTraceReplaySpectatorController();
};
