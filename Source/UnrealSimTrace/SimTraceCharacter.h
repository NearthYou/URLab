#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "SimTraceTypes.h"
#include "SimTraceCharacter.generated.h"

class UCameraComponent;
class UInputAction;
class UInputMappingContext;
class UPrimitiveComponent;
class USimTraceCaptureComponent;
struct FInputActionValue;

UCLASS()
class UNREALSIMTRACE_API ASimTraceCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	ASimTraceCharacter();

	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
	virtual void NotifyHit(
		UPrimitiveComponent* MyComponent,
		AActor* Other,
		UPrimitiveComponent* OtherComponent,
		bool bSelfMoved,
		FVector HitLocation,
		FVector HitNormal,
		FVector NormalImpulse,
		const FHitResult& Hit) override;

	void BeginInputFrame();
	void ResetForEpisode(const FTransform& StartTransform);
	bool ConsumeCollision();

	const FSimTraceActionState& GetCurrentAction() const { return CurrentAction; }
	UCameraComponent* GetFirstPersonCamera() const { return FirstPersonCamera; }
	USimTraceCaptureComponent* GetCaptureComponent() const { return CaptureComponent; }
	UInputAction* GetMoveAction() const { return MoveAction; }
	UInputAction* GetLookAction() const { return LookAction; }
	UInputAction* GetJumpAction() const { return JumpAction; }

private:
	UPROPERTY()
	TObjectPtr<UCameraComponent> FirstPersonCamera;

	UPROPERTY()
	TObjectPtr<USimTraceCaptureComponent> CaptureComponent;

	UPROPERTY()
	TObjectPtr<UInputAction> MoveAction;

	UPROPERTY()
	TObjectPtr<UInputAction> LookAction;

	UPROPERTY()
	TObjectPtr<UInputAction> JumpAction;

	UPROPERTY()
	TObjectPtr<UInputAction> ResetAction;

	UPROPERTY()
	TObjectPtr<UInputMappingContext> MappingContext;

	FSimTraceActionState CurrentAction;
	bool bCollisionSinceLastSample = false;

	void MoveInput(const FInputActionValue& Value);
	void LookInput(const FInputActionValue& Value);
	void JumpInput(const FInputActionValue& Value);
	void JumpCompleted(const FInputActionValue& Value);
	void ManualAbortInput(const FInputActionValue& Value);
};
