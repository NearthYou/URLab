#include "SimTraceGameInstance.h"

#include "Camera/CameraComponent.h"
#include "Engine/DemoNetDriver.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "Net/UnrealNetwork.h"
#include "SimTraceCharacter.h"
#include "TimerManager.h"
#include "UnrealSimTrace.h"

void USimTraceGameInstance::Init()
{
	Super::Init();
	RuntimeConfig = FSimTraceRuntimeConfig::Parse(FCommandLine::Get());
	ReplayCompleteHandle = FNetworkReplayDelegates::OnReplayPlaybackComplete.AddUObject(
		this,
		&USimTraceGameInstance::HandleReplayPlaybackComplete);
	UE_LOG(
		LogSimTrace,
		Display,
		TEXT("Mode=%s Seed=%d Batch=%d Capture=%s"),
		*LexToString(RuntimeConfig.Mode),
		RuntimeConfig.Seed,
		RuntimeConfig.BatchCount,
		RuntimeConfig.bCapture ? TEXT("true") : TEXT("false"));
}

void USimTraceGameInstance::Shutdown()
{
	GetTimerManager().ClearTimer(ReplayCameraRetryHandle);
	FNetworkReplayDelegates::OnReplayPlaybackComplete.Remove(ReplayCompleteHandle);
	Super::Shutdown();
}

void USimTraceGameInstance::TryAttachReplayCamera()
{
	UWorld* ReplayWorld = GetWorld();
	UDemoNetDriver* DemoNetDriver = ReplayWorld ? ReplayWorld->GetDemoNetDriver() : nullptr;
	APlayerController* SpectatorController =
		DemoNetDriver ? DemoNetDriver->GetSpectatorController() : nullptr;
	if (!ReplayWorld || !SpectatorController)
	{
		return;
	}

	for (TActorIterator<ASimTraceCharacter> CharacterIt(ReplayWorld); CharacterIt; ++CharacterIt)
	{
		ASimTraceCharacter* ReplayCharacter = *CharacterIt;
		if (!IsValid(ReplayCharacter) || ReplayCharacter->IsActorBeingDestroyed())
		{
			continue;
		}

		if (SpectatorController->GetViewTarget() != ReplayCharacter)
		{
			SpectatorController->bAutoManageActiveCameraTarget = false;
			if (UCameraComponent* FirstPersonCamera = ReplayCharacter->GetFirstPersonCamera())
			{
				FirstPersonCamera->SetActive(true);
			}
			SpectatorController->SetViewTarget(ReplayCharacter);
		}

		if (SpectatorController->GetViewTarget() == ReplayCharacter)
		{
			SpectatorController->SetIgnoreMoveInput(true);
			SpectatorController->SetIgnoreLookInput(true);
			GetTimerManager().ClearTimer(ReplayCameraRetryHandle);
			UE_LOG(
				LogSimTrace,
				Display,
				TEXT("Replay camera attached to recorded character: %s"),
				*ReplayCharacter->GetName());
			return;
		}
	}
}

void USimTraceGameInstance::OnStart()
{
	Super::OnStart();
	if (RuntimeConfig.Mode != ESimTraceMode::NativeReplay)
	{
		return;
	}
	if (RuntimeConfig.ReplayName.IsEmpty())
	{
		UE_LOG(LogSimTrace, Error, TEXT("Native replay mode requires -SimTraceReplay"));
		FPlatformMisc::RequestExitWithStatus(false, 1);
		return;
	}
	if (!RestoreReplayArchive(RuntimeConfig.ReplayName))
	{
		UE_LOG(
			LogSimTrace,
			Error,
			TEXT("Unable to restore native replay archive: %s"),
			*RuntimeConfig.ReplayName);
		FPlatformMisc::RequestExitWithStatus(false, 1);
		return;
	}
	if (!PlayReplay(RuntimeConfig.ReplayName))
	{
		UE_LOG(
			LogSimTrace,
			Error,
			TEXT("Unable to start native replay: %s"),
			*RuntimeConfig.ReplayName);
		FPlatformMisc::RequestExitWithStatus(false, 1);
		return;
	}

	GetTimerManager().SetTimer(
		ReplayCameraRetryHandle,
		this,
		&USimTraceGameInstance::TryAttachReplayCamera,
		0.05f,
		true,
		0.0f);
}

void USimTraceGameInstance::StartEpisodeReplay(const FString& ReplayName)
{
	StartRecordingReplay(ReplayName, ReplayName);
}

void USimTraceGameInstance::StopEpisodeReplay()
{
	StopRecordingReplay();
}

bool USimTraceGameInstance::ArchiveReplay(
	const FString& ReplayName,
	const FString& EpisodeDirectory) const
{
	const FString Source = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("Demos"), ReplayName + TEXT(".replay"));
	const FString DestinationDirectory = FPaths::Combine(EpisodeDirectory, TEXT("replay"));
	const FString Destination = FPaths::Combine(DestinationDirectory, ReplayName + TEXT(".replay"));
	IFileManager::Get().MakeDirectory(*DestinationDirectory, true);

	if (!IFileManager::Get().FileExists(*Source))
	{
		UE_LOG(LogSimTrace, Warning, TEXT("Replay archive source is not ready: %s"), *Source);
		return false;
	}

	return IFileManager::Get().Copy(*Destination, *Source, true, true) == COPY_OK;
}

bool USimTraceGameInstance::RestoreReplayArchive(const FString& ReplayName) const
{
	const FString DemoDirectory = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("Demos"));
	const FString DemoPath = FPaths::Combine(DemoDirectory, ReplayName + TEXT(".replay"));
	if (IFileManager::Get().FileExists(*DemoPath))
	{
		return true;
	}

	TArray<FString> Matches;
	const FString EpisodesDirectory =
		FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("SimTrace"), TEXT("episodes"));
	IFileManager::Get().FindFilesRecursive(
		Matches,
		*EpisodesDirectory,
		*(ReplayName + TEXT(".replay")),
		true,
		false);
	if (Matches.IsEmpty())
	{
		return false;
	}

	IFileManager::Get().MakeDirectory(*DemoDirectory, true);
	return IFileManager::Get().Copy(*DemoPath, *Matches[0], true, true) == COPY_OK;
}

void USimTraceGameInstance::HandleReplayPlaybackComplete(UWorld*)
{
	if (RuntimeConfig.Mode == ESimTraceMode::NativeReplay)
	{
		GetTimerManager().ClearTimer(ReplayCameraRetryHandle);
		FPlatformMisc::RequestExit(false);
	}
}
