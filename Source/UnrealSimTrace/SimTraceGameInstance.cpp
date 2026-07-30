#include "SimTraceGameInstance.h"

#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "UnrealSimTrace.h"

void USimTraceGameInstance::Init()
{
	Super::Init();
	RuntimeConfig = FSimTraceRuntimeConfig::Parse(FCommandLine::Get());
	UE_LOG(
		LogSimTrace,
		Display,
		TEXT("Mode=%s Seed=%d Batch=%d Capture=%s"),
		*LexToString(RuntimeConfig.Mode),
		RuntimeConfig.Seed,
		RuntimeConfig.BatchCount,
		RuntimeConfig.bCapture ? TEXT("true") : TEXT("false"));
}

void USimTraceGameInstance::OnStart()
{
	Super::OnStart();
	if (RuntimeConfig.Mode == ESimTraceMode::NativeReplay && !RuntimeConfig.ReplayName.IsEmpty())
	{
		RestoreReplayArchive(RuntimeConfig.ReplayName);
		PlayReplay(RuntimeConfig.ReplayName);
	}
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

