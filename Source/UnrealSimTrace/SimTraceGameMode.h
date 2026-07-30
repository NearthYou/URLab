#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "SimTraceTypes.h"
#include "SimTraceGameMode.generated.h"

class ASimTraceCharacter;
class ASimTraceCourseActor;
class UEpisodeRecorderComponent;

UCLASS()
class UNREALSIMTRACE_API ASimTraceGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ASimTraceGameMode();

	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	FSimTraceActionState GetSyntheticAction() const;
	void RequestManualAbort();

private:
	UPROPERTY()
	TObjectPtr<ASimTraceCourseActor> Course;

	UPROPERTY()
	TObjectPtr<ASimTraceCharacter> SimTraceCharacter;

	UPROPERTY()
	TObjectPtr<UEpisodeRecorderComponent> Recorder;

	TArray<FSimTraceActionState> ReplayActions;
	FString ParentEpisodeId;
	int32 EpisodeIndex = 0;
	mutable int32 BotWaypointIndex = 0;
	bool bEpisodeActive = false;
	bool bManualAbortRequested = false;
	FTimerHandle EpisodeTimer;

	void StartEpisode();
	void FinishEpisode(ESimTraceEndReason EndReason);
	void ArchiveAndContinue(FString EpisodeDirectory, FString ReplayName);
	bool LoadReplayInput(int32& OutSeed);
	FSimTraceActionState BuildBotAction() const;
};

