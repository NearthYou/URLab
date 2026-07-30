#pragma once

#include "CoreMinimal.h"
#include "Engine/GameInstance.h"
#include "SimTraceRuntimeConfig.h"
#include "SimTraceGameInstance.generated.h"

UCLASS()
class UNREALSIMTRACE_API USimTraceGameInstance : public UGameInstance
{
	GENERATED_BODY()

public:
	virtual void Init() override;
	virtual void OnStart() override;

	const FSimTraceRuntimeConfig& GetRuntimeConfig() const { return RuntimeConfig; }
	void StartEpisodeReplay(const FString& ReplayName);
	void StopEpisodeReplay();
	bool ArchiveReplay(const FString& ReplayName, const FString& EpisodeDirectory) const;
	bool RestoreReplayArchive(const FString& ReplayName) const;

private:
	FSimTraceRuntimeConfig RuntimeConfig;
};

