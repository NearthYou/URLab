#include "SimTracePlayerController.h"

#include "EnhancedPlayerInput.h"
#include "InputActionValue.h"
#include "SimTraceCharacter.h"
#include "SimTraceGameInstance.h"
#include "SimTraceGameMode.h"

ASimTracePlayerController::ASimTracePlayerController()
{
	bShowMouseCursor = false;
}

void ASimTracePlayerController::BeginPlay()
{
	Super::BeginPlay();
	FInputModeGameOnly InputMode;
	SetInputMode(InputMode);
}

void ASimTracePlayerController::ProcessPlayerInput(const float DeltaTime, const bool bGamePaused)
{
	ASimTraceCharacter* SimCharacter = Cast<ASimTraceCharacter>(GetPawn());
	if (SimCharacter)
	{
		SimCharacter->BeginInputFrame();

		const USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
		if (GameInstance && GameInstance->GetRuntimeConfig().Mode != ESimTraceMode::Human)
		{
			if (const ASimTraceGameMode* GameMode = GetWorld()->GetAuthGameMode<ASimTraceGameMode>())
			{
				const FSimTraceActionState Action = GameMode->GetSyntheticAction();
				if (UEnhancedPlayerInput* EnhancedInput = Cast<UEnhancedPlayerInput>(PlayerInput))
				{
					EnhancedInput->InjectInputForAction(
						SimCharacter->GetMoveAction(),
						FInputActionValue(Action.Move));
					EnhancedInput->InjectInputForAction(
						SimCharacter->GetLookAction(),
						FInputActionValue(Action.Look));
					EnhancedInput->InjectInputForAction(
						SimCharacter->GetJumpAction(),
						FInputActionValue(Action.bJumpPressed));
					EnhancedInput->InjectInputForAction(
						SimCharacter->GetFireAction(),
						FInputActionValue(Action.bFirePressed));
				}
			}
		}
	}

	Super::ProcessPlayerInput(DeltaTime, bGamePaused);
}
