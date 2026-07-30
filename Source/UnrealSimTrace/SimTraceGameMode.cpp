#include "SimTraceGameMode.h"

#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SimTraceCharacter.h"
#include "SimTraceCaptureComponent.h"
#include "SimTraceCourseActor.h"
#include "SimTraceGameInstance.h"
#include "SimTracePlayerController.h"
#include "EpisodeRecorderComponent.h"
#include "TimerManager.h"
#include "UnrealSimTrace.h"

ASimTraceGameMode::ASimTraceGameMode()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.TickGroup = TG_PostPhysics;
	DefaultPawnClass = ASimTraceCharacter::StaticClass();
	PlayerControllerClass = ASimTracePlayerController::StaticClass();

	Recorder = CreateDefaultSubobject<UEpisodeRecorderComponent>(TEXT("EpisodeRecorder"));
}

void ASimTraceGameMode::BeginPlay()
{
	Super::BeginPlay();

	const USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (GameInstance && GameInstance->GetRuntimeConfig().Mode == ESimTraceMode::NativeReplay)
	{
		return;
	}

	FActorSpawnParameters SpawnParameters;
	SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	Course = GetWorld()->SpawnActor<ASimTraceCourseActor>(
		ASimTraceCourseActor::StaticClass(),
		FTransform::Identity,
		SpawnParameters);
	GetWorldTimerManager().SetTimer(EpisodeTimer, this, &ASimTraceGameMode::StartEpisode, 1.0, false);
}

void ASimTraceGameMode::Tick(const float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bEpisodeActive || !Recorder || !SimTraceCharacter || !Course)
	{
		return;
	}

	const USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (!GameInstance)
	{
		return;
	}

	const FSimTraceRuntimeConfig& Config = GameInstance->GetRuntimeConfig();
	const double CurrentRealTickSeconds = FPlatformTime::Seconds();
	const double RealFrameTimeMilliseconds = LastRealTickSeconds > 0.0
		? (CurrentRealTickSeconds - LastRealTickSeconds) * 1000.0
		: 0.0;
	LastRealTickSeconds = CurrentRealTickSeconds;
	ESimTraceEndReason EndReason = ESimTraceEndReason::None;
	if (bManualAbortRequested)
	{
		EndReason = ESimTraceEndReason::ManualAbort;
	}
	else if (Config.Mode == ESimTraceMode::InputReplay &&
		Recorder->GetNextFrameIndex() >= ReplayActions.Num() - 1)
	{
		EndReason = ESimTraceEndReason::ReplaySourceEnd;
	}
	else if (Config.Mode != ESimTraceMode::InputReplay && Course->WasGoalReached())
	{
		EndReason = ESimTraceEndReason::Goal;
	}
	else if (SimTraceCharacter->GetActorLocation().Z < -300.0)
	{
		EndReason = ESimTraceEndReason::Fell;
	}
	else if (Recorder->GetNextFrameIndex() >= FMath::RoundToInt(Config.MaxSeconds * 30.0))
	{
		EndReason = ESimTraceEndReason::Timeout;
	}

	FSimTraceCaptureResult CaptureResult;
	const int32 SimFrame = Recorder->GetNextFrameIndex();
	if (Config.bCapture && !bCaptureReady)
	{
		EndReason = ESimTraceEndReason::CaptureError;
	}
	else if (Config.bCapture && SimFrame % 3 == 0)
	{
		CaptureResult = SimTraceCharacter->GetCaptureComponent()->CaptureFrame(
			SimFrame,
			Recorder->GetEpisodeDirectory());
		if (CaptureResult.bError)
		{
			EndReason = ESimTraceEndReason::CaptureError;
		}
	}

	bool bDone = EndReason != ESimTraceEndReason::None;
	if (bDone &&
		Config.bCapture &&
		!SimTraceCharacter->GetCaptureComponent()->FlushPendingWrites())
	{
		EndReason = ESimTraceEndReason::CaptureError;
		bDone = true;
	}

	if (!Recorder->RecordFrame(
			bDone,
			EndReason,
			RealFrameTimeMilliseconds,
			CaptureResult.bCaptured,
			CaptureResult.bDropped,
			CaptureResult.RgbRelativePath,
			CaptureResult.DepthRelativePath))
	{
		EndReason = ESimTraceEndReason::IoError;
		FinishEpisode(EndReason);
		return;
	}

	if (bDone)
	{
		FinishEpisode(EndReason);
	}
}

FSimTraceActionState ASimTraceGameMode::GetSyntheticAction() const
{
	if (!bEpisodeActive || !Recorder)
	{
		return {};
	}

	const USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (!GameInstance)
	{
		return {};
	}

	if (GameInstance->GetRuntimeConfig().Mode == ESimTraceMode::Bot)
	{
		return BuildBotAction();
	}

	if (GameInstance->GetRuntimeConfig().Mode == ESimTraceMode::InputReplay)
	{
		const int32 Frame = Recorder->GetNextFrameIndex();
		return ReplayActions.IsValidIndex(Frame) ? ReplayActions[Frame] : FSimTraceActionState();
	}

	return {};
}

void ASimTraceGameMode::RequestManualAbort()
{
	bManualAbortRequested = true;
}

void ASimTraceGameMode::StartEpisode()
{
	USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (!GameInstance || !Course)
	{
		FPlatformMisc::RequestExit(false);
		return;
	}

	const FSimTraceRuntimeConfig& Config = GameInstance->GetRuntimeConfig();
	int32 EpisodeSeed = Config.Seed + EpisodeIndex;
	if (Config.Mode == ESimTraceMode::InputReplay && ReplayActions.IsEmpty())
	{
		if (!LoadReplayInput(EpisodeSeed))
		{
			UE_LOG(LogSimTrace, Error, TEXT("Unable to load input replay: %s"), *Config.InputPath);
			FPlatformMisc::RequestExit(false);
			return;
		}
	}

	Course->SetCourseSeed(EpisodeSeed);
	Course->ResetGoal();

	APlayerController* PlayerController = GetWorld()->GetFirstPlayerController();
	SimTraceCharacter = PlayerController ? Cast<ASimTraceCharacter>(PlayerController->GetPawn()) : nullptr;
	if (!SimTraceCharacter && PlayerController)
	{
		FActorSpawnParameters SpawnParameters;
		SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		SimTraceCharacter = GetWorld()->SpawnActor<ASimTraceCharacter>(
			ASimTraceCharacter::StaticClass(),
			Course->GetLayout().StartTransform,
			SpawnParameters);
		PlayerController->Possess(SimTraceCharacter);
	}
	if (!SimTraceCharacter)
	{
		UE_LOG(LogSimTrace, Error, TEXT("No SimTrace character could be created"));
		FPlatformMisc::RequestExit(false);
		return;
	}

	SimTraceCharacter->ResetForEpisode(Course->GetLayout().StartTransform);
	bCaptureReady = !Config.bCapture ||
		SimTraceCharacter->GetCaptureComponent()->InitializeCapture();
	BotWaypointIndex = 0;
	bManualAbortRequested = false;
	if (!Recorder->BeginEpisode(
			SimTraceCharacter,
			Course,
			Config,
			EpisodeIndex,
			ParentEpisodeId))
	{
		UE_LOG(LogSimTrace, Error, TEXT("Unable to start episode recorder"));
		FPlatformMisc::RequestExit(false);
		return;
	}

	GameInstance->StartEpisodeReplay(Recorder->GetReplayName());
	LastRealTickSeconds = FPlatformTime::Seconds();
	bEpisodeActive = true;
	if (GEngine)
	{
		GEngine->AddOnScreenDebugMessage(
			-1,
			5.0,
			FColor::Cyan,
			FString::Printf(
				TEXT("SimTrace %s | seed %d | episode %d/%d"),
				*LexToString(Config.Mode),
				EpisodeSeed,
				EpisodeIndex + 1,
				Config.BatchCount));
	}
}

void ASimTraceGameMode::FinishEpisode(const ESimTraceEndReason EndReason)
{
	if (!bEpisodeActive)
	{
		return;
	}
	bEpisodeActive = false;
	if (SimTraceCharacter && SimTraceCharacter->GetCaptureComponent())
	{
		SimTraceCharacter->GetCaptureComponent()->FlushPendingWrites();
	}

	USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (GameInstance)
	{
		GameInstance->StopEpisodeReplay();
	}

	const FString EpisodeDirectory = Recorder->GetEpisodeDirectory();
	const FString ReplayName = Recorder->GetReplayName();
	Recorder->FinishEpisode(EndReason);

	if (GEngine)
	{
		GEngine->AddOnScreenDebugMessage(
			-1,
			4.0,
			FColor::Green,
			FString::Printf(TEXT("Episode complete: %s"), *LexToString(EndReason)));
	}

	FTimerDelegate ContinueDelegate;
	ContinueDelegate.BindUObject(
		this,
		&ASimTraceGameMode::ArchiveAndContinue,
		EpisodeDirectory,
		ReplayName);
	GetWorldTimerManager().SetTimer(EpisodeTimer, ContinueDelegate, 0.75, false);
}

void ASimTraceGameMode::ArchiveAndContinue(
	FString EpisodeDirectory,
	FString ReplayName)
{
	USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (GameInstance)
	{
		GameInstance->ArchiveReplay(ReplayName, EpisodeDirectory);
	}
	if (Recorder)
	{
		Recorder->RefreshCompletedManifest();
	}

	++EpisodeIndex;
	if (GameInstance && EpisodeIndex < GameInstance->GetRuntimeConfig().BatchCount)
	{
		StartEpisode();
		return;
	}

	FPlatformMisc::RequestExit(false);
}

bool ASimTraceGameMode::LoadReplayInput(int32& OutSeed)
{
	const USimTraceGameInstance* GameInstance = GetGameInstance<USimTraceGameInstance>();
	if (!GameInstance)
	{
		return false;
	}

	FString InputPath = GameInstance->GetRuntimeConfig().InputPath;
	if (InputPath.IsEmpty())
	{
		return false;
	}
	InputPath = FPaths::ConvertRelativePathToFull(InputPath);

	TArray<FString> Lines;
	if (!FFileHelper::LoadFileToStringArray(Lines, *InputPath))
	{
		return false;
	}

	ReplayActions.Reset();
	for (const FString& Line : Lines)
	{
		TSharedPtr<FJsonObject> Object;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Line);
		if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid())
		{
			return false;
		}

		FSimTraceActionState Action;
		const TArray<TSharedPtr<FJsonValue>>* Move = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Look = nullptr;
		if (Object->TryGetArrayField(TEXT("move_input"), Move) && Move && Move->Num() >= 2)
		{
			Action.Move = FVector2D((*Move)[0]->AsNumber(), (*Move)[1]->AsNumber());
		}
		if (Object->TryGetArrayField(TEXT("look_input"), Look) && Look && Look->Num() >= 2)
		{
			Action.Look = FVector2D((*Look)[0]->AsNumber(), (*Look)[1]->AsNumber());
		}
		Object->TryGetBoolField(TEXT("jump_pressed"), Action.bJumpPressed);
		ReplayActions.Add(Action);
	}

	const FString EpisodeDirectory = FPaths::GetPath(InputPath);
	ParentEpisodeId = FPaths::GetCleanFilename(EpisodeDirectory);
	const FString ManifestPath = FPaths::Combine(EpisodeDirectory, TEXT("manifest.json"));
	FString ManifestJson;
	if (FFileHelper::LoadFileToString(ManifestJson, *ManifestPath))
	{
		TSharedPtr<FJsonObject> Manifest;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ManifestJson);
		double SeedValue = OutSeed;
		if (FJsonSerializer::Deserialize(Reader, Manifest) &&
			Manifest.IsValid() &&
			Manifest->TryGetNumberField(TEXT("seed"), SeedValue))
		{
			OutSeed = static_cast<int32>(SeedValue);
		}
	}

	return !ReplayActions.IsEmpty();
}

FSimTraceActionState ASimTraceGameMode::BuildBotAction() const
{
	FSimTraceActionState Action;
	if (!SimTraceCharacter || !Course)
	{
		return Action;
	}

	const TArray<FVector>& Waypoints = Course->GetLayout().Waypoints;
	if (Waypoints.IsEmpty())
	{
		return Action;
	}

	BotWaypointIndex = FMath::Clamp(BotWaypointIndex, 0, Waypoints.Num() - 1);
	FVector Target = Waypoints[BotWaypointIndex];
	FVector ToTarget = Target - SimTraceCharacter->GetActorLocation();
	ToTarget.Z = 0.0;
	if (ToTarget.SizeSquared() < FMath::Square(130.0) && BotWaypointIndex < Waypoints.Num() - 1)
	{
		++BotWaypointIndex;
		Target = Waypoints[BotWaypointIndex];
		ToTarget = Target - SimTraceCharacter->GetActorLocation();
		ToTarget.Z = 0.0;
	}

	const FVector Direction = ToTarget.GetSafeNormal();
	const FVector Forward = SimTraceCharacter->GetActorForwardVector();
	const FVector Right = SimTraceCharacter->GetActorRightVector();
	Action.Move.X = FMath::Clamp(FVector::DotProduct(Direction, Right), -1.0, 1.0);
	Action.Move.Y = FMath::Clamp(FVector::DotProduct(Direction, Forward), -1.0, 1.0);

	const double DesiredYaw = Direction.Rotation().Yaw;
	const double CurrentYaw = SimTraceCharacter->GetControlRotation().Yaw;
	const double YawDelta = FMath::FindDeltaAngleDegrees(CurrentYaw, DesiredYaw);
	Action.Look.X = FMath::Clamp(YawDelta * 0.08, -2.0, 2.0);

	FHitResult Hit;
	const FVector TraceStart = SimTraceCharacter->GetActorLocation() + FVector(0.0, 0.0, 20.0);
	const FVector TraceEnd = TraceStart + Forward * 140.0;
	FCollisionQueryParams Params(SCENE_QUERY_STAT(SimTraceBotJump), false, SimTraceCharacter);
	const bool bObstacleAhead =
		GetWorld()->LineTraceSingleByChannel(Hit, TraceStart, TraceEnd, ECC_Visibility, Params);
	const double X = SimTraceCharacter->GetActorLocation().X;
	Action.bJumpPressed = bObstacleAhead || (X > 1630.0 && X < 1840.0);
	return Action;
}
