#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "SimTraceHUD.generated.h"

UCLASS()
class UNREALSIMTRACE_API ASimTraceHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void DrawHUD() override;

private:
	void DrawCrosshair(float CenterX, float CenterY);
	void DrawHitMarker(float CenterX, float CenterY);
};
