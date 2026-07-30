#include "SimTraceCharacter.h"

#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "EnhancedActionKeyMapping.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "InputAction.h"
#include "InputActionValue.h"
#include "InputCoreTypes.h"
#include "InputMappingContext.h"
#include "InputModifiers.h"
#include "HAL/PlatformMisc.h"
#include "SimTraceGameInstance.h"
#include "SimTraceGameMode.h"
#include "SimTraceCaptureComponent.h"

ASimTraceCharacter::ASimTraceCharacter()
{
	bReplicates = true;
	bAlwaysRelevant = true;
	GetCapsuleComponent()->InitCapsuleSize(34.0, 96.0);

	FirstPersonCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("FirstPersonCamera"));
	FirstPersonCamera->SetupAttachment(GetCapsuleComponent());
	FirstPersonCamera->SetRelativeLocation(FVector(0.0, 0.0, 64.0));
	FirstPersonCamera->bUsePawnControlRotation = true;
	FirstPersonCamera->FieldOfView = 90.0;

	CaptureComponent = CreateDefaultSubobject<USimTraceCaptureComponent>(TEXT("SimTraceCapture"));
	CaptureComponent->SetupAttachment(FirstPersonCamera);

	GetMesh()->SetHiddenInGame(true);
	GetMesh()->SetCollisionEnabled(ECollisionEnabled::NoCollision);

	UCharacterMovementComponent* Movement = GetCharacterMovement();
	Movement->MaxWalkSpeed = 600.0;
	Movement->JumpZVelocity = 520.0;
	Movement->AirControl = 0.6f;
	Movement->BrakingDecelerationFalling = 1500.0;

	MoveAction = CreateDefaultSubobject<UInputAction>(TEXT("IA_Move"));
	MoveAction->ValueType = EInputActionValueType::Axis2D;
	LookAction = CreateDefaultSubobject<UInputAction>(TEXT("IA_Look"));
	LookAction->ValueType = EInputActionValueType::Axis2D;
	JumpAction = CreateDefaultSubobject<UInputAction>(TEXT("IA_Jump"));
	JumpAction->ValueType = EInputActionValueType::Boolean;
	ResetAction = CreateDefaultSubobject<UInputAction>(TEXT("IA_Reset"));
	ResetAction->ValueType = EInputActionValueType::Boolean;
	ExitAction = CreateDefaultSubobject<UInputAction>(TEXT("IA_Exit"));
	ExitAction->ValueType = EInputActionValueType::Boolean;
	MappingContext = CreateDefaultSubobject<UInputMappingContext>(TEXT("IMC_SimTrace"));

	UInputModifierSwizzleAxis* ForwardSwizzle =
		CreateDefaultSubobject<UInputModifierSwizzleAxis>(TEXT("ForwardSwizzle"));
	ForwardSwizzle->Order = EInputAxisSwizzle::YXZ;
	UInputModifierNegate* BackwardNegate =
		CreateDefaultSubobject<UInputModifierNegate>(TEXT("BackwardNegate"));
	UInputModifierSwizzleAxis* BackwardSwizzle =
		CreateDefaultSubobject<UInputModifierSwizzleAxis>(TEXT("BackwardSwizzle"));
	BackwardSwizzle->Order = EInputAxisSwizzle::YXZ;
	UInputModifierNegate* LeftNegate =
		CreateDefaultSubobject<UInputModifierNegate>(TEXT("LeftNegate"));

	FEnhancedActionKeyMapping& Forward = MappingContext->MapKey(MoveAction, EKeys::W);
	Forward.Modifiers.Add(ForwardSwizzle);
	FEnhancedActionKeyMapping& Backward = MappingContext->MapKey(MoveAction, EKeys::S);
	Backward.Modifiers.Add(BackwardSwizzle);
	Backward.Modifiers.Add(BackwardNegate);
	FEnhancedActionKeyMapping& Left = MappingContext->MapKey(MoveAction, EKeys::A);
	Left.Modifiers.Add(LeftNegate);
	MappingContext->MapKey(MoveAction, EKeys::D);
	MappingContext->MapKey(LookAction, EKeys::Mouse2D);
	MappingContext->MapKey(JumpAction, EKeys::SpaceBar);
	MappingContext->MapKey(ResetAction, EKeys::R);
	MappingContext->MapKey(ExitAction, EKeys::Escape);
}

void ASimTraceCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);

	UEnhancedInputComponent* EnhancedInput = Cast<UEnhancedInputComponent>(PlayerInputComponent);
	if (!EnhancedInput)
	{
		return;
	}

	EnhancedInput->BindAction(MoveAction, ETriggerEvent::Triggered, this, &ASimTraceCharacter::MoveInput);
	EnhancedInput->BindAction(LookAction, ETriggerEvent::Triggered, this, &ASimTraceCharacter::LookInput);
	EnhancedInput->BindAction(JumpAction, ETriggerEvent::Triggered, this, &ASimTraceCharacter::JumpInput);
	EnhancedInput->BindAction(JumpAction, ETriggerEvent::Completed, this, &ASimTraceCharacter::JumpCompleted);
	EnhancedInput->BindAction(ResetAction, ETriggerEvent::Started, this, &ASimTraceCharacter::ManualAbortInput);
	EnhancedInput->BindAction(ExitAction, ETriggerEvent::Started, this, &ASimTraceCharacter::ExitInput);

	const USimTraceGameInstance* SimTraceGameInstance = GetGameInstance<USimTraceGameInstance>();
	if (SimTraceGameInstance && SimTraceGameInstance->GetRuntimeConfig().Mode != ESimTraceMode::Human)
	{
		return;
	}

	const APlayerController* PlayerController = Cast<APlayerController>(GetController());
	if (PlayerController)
	{
		if (UEnhancedInputLocalPlayerSubsystem* Subsystem =
			ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(PlayerController->GetLocalPlayer()))
		{
			Subsystem->AddMappingContext(MappingContext, 0);
		}
	}
}

void ASimTraceCharacter::NotifyHit(
	UPrimitiveComponent* MyComponent,
	AActor* Other,
	UPrimitiveComponent* OtherComponent,
	bool bSelfMoved,
	FVector HitLocation,
	FVector HitNormal,
	FVector NormalImpulse,
	const FHitResult& Hit)
{
	Super::NotifyHit(
		MyComponent,
		Other,
		OtherComponent,
		bSelfMoved,
		HitLocation,
		HitNormal,
		NormalImpulse,
		Hit);
	bCollisionSinceLastSample = true;
}

void ASimTraceCharacter::BeginInputFrame()
{
	CurrentAction = FSimTraceActionState();
}

void ASimTraceCharacter::ResetForEpisode(const FTransform& StartTransform)
{
	SetActorTransform(StartTransform, false, nullptr, ETeleportType::TeleportPhysics);
	GetCharacterMovement()->StopMovementImmediately();
	GetCharacterMovement()->SetMovementMode(MOVE_Walking);
	if (Controller)
	{
		Controller->SetControlRotation(StartTransform.Rotator());
	}
	CurrentAction = FSimTraceActionState();
	bCollisionSinceLastSample = false;
	StopJumping();
}

bool ASimTraceCharacter::ConsumeCollision()
{
	const bool bResult = bCollisionSinceLastSample;
	bCollisionSinceLastSample = false;
	return bResult;
}

void ASimTraceCharacter::MoveInput(const FInputActionValue& Value)
{
	CurrentAction.Move = Value.Get<FVector2D>();
	AddMovementInput(GetActorRightVector(), CurrentAction.Move.X);
	AddMovementInput(GetActorForwardVector(), CurrentAction.Move.Y);
}

void ASimTraceCharacter::LookInput(const FInputActionValue& Value)
{
	CurrentAction.Look = Value.Get<FVector2D>();
	AddControllerYawInput(CurrentAction.Look.X);
	AddControllerPitchInput(CurrentAction.Look.Y);
}

void ASimTraceCharacter::JumpInput(const FInputActionValue& Value)
{
	CurrentAction.bJumpPressed = Value.Get<bool>();
	if (CurrentAction.bJumpPressed)
	{
		Jump();
	}
}

void ASimTraceCharacter::JumpCompleted(const FInputActionValue& Value)
{
	CurrentAction.bJumpPressed = false;
	StopJumping();
}

void ASimTraceCharacter::ManualAbortInput(const FInputActionValue& Value)
{
	if (ASimTraceGameMode* GameMode = GetWorld()->GetAuthGameMode<ASimTraceGameMode>())
	{
		GameMode->RequestManualAbort();
	}
}

void ASimTraceCharacter::ExitInput(const FInputActionValue& Value)
{
	FPlatformMisc::RequestExit(false);
}
