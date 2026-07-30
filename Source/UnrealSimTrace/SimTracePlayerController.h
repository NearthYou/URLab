#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"
#include "SimTracePlayerController.generated.h"

UCLASS()
class UNREALSIMTRACE_API ASimTracePlayerController : public APlayerController
{
	GENERATED_BODY()

public:
	ASimTracePlayerController();

protected:
	virtual void BeginPlay() override;
	virtual void ProcessPlayerInput(float DeltaTime, bool bGamePaused) override;
};

