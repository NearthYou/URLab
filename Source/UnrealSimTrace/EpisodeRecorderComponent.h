#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SimTraceRuntimeConfig.h"
#include "EpisodeRecorderComponent.generated.h"

class ASimTraceCharacter;
class ASimTraceCourseActor;
class FArchive;

UCLASS(ClassGroup=(SimTrace), meta=(BlueprintSpawnableComponent))
class UNREALSIMTRACE_API UEpisodeRecorderComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UEpisodeRecorderComponent();

	bool BeginEpisode(
		ASimTraceCharacter* InCharacter,
		ASimTraceCourseActor* InCourse,
		const FSimTraceRuntimeConfig& InConfig,
		int32 EpisodeIndex,
		const FString& InParentEpisodeId);
	bool RecordFrame(
		bool bDone,
		ESimTraceEndReason EndReason,
		double FrameTimeMilliseconds,
		bool bCaptured = false,
		bool bCaptureDropped = false,
		const FString& RgbRelativePath = FString(),
		const FString& DepthRelativePath = FString());
	bool FinishEpisode(ESimTraceEndReason EndReason);
	bool RefreshCompletedManifest();

	int32 GetNextFrameIndex() const { return SamplesWritten; }
	const FString& GetEpisodeId() const { return EpisodeId; }
	const FString& GetEpisodeDirectory() const { return EpisodeDirectory; }
	const FString& GetReplayName() const { return ReplayName; }
	bool IsRecording() const { return bRecording; }

private:
	TWeakObjectPtr<ASimTraceCharacter> Character;
	TWeakObjectPtr<ASimTraceCourseActor> Course;
	FSimTraceRuntimeConfig RuntimeConfig;
	TUniquePtr<FArchive> TrajectoryWriter;
	FDateTime StartedUtc;
	FString EpisodeId;
	FString ParentEpisodeId;
	FString EpisodeDirectory;
	FString ReplayName;
	int32 SamplesWritten = 0;
	int32 CapturesWritten = 0;
	int32 CapturesDropped = 0;
	ESimTraceEndReason LastEndReason = ESimTraceEndReason::None;
	bool bRecording = false;

	bool WriteManifest(bool bComplete, ESimTraceEndReason EndReason);
	void CountEpisodePayloadFiles(int32& OutFileCount, int64& OutTotalBytes) const;
};
