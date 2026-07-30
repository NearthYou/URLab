#include "EpisodeRecorderComponent.h"

#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "HAL/FileManager.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "SimTraceCharacter.h"
#include "SimTraceCourseActor.h"
#include "UnrealSimTrace.h"

namespace
{
	TArray<TSharedPtr<FJsonValue>> VectorToJson(const FVector& Vector)
	{
		return {
			MakeShared<FJsonValueNumber>(Vector.X),
			MakeShared<FJsonValueNumber>(Vector.Y),
			MakeShared<FJsonValueNumber>(Vector.Z)
		};
	}

	TArray<TSharedPtr<FJsonValue>> RotatorToJson(const FRotator& Rotator)
	{
		return {
			MakeShared<FJsonValueNumber>(Rotator.Pitch),
			MakeShared<FJsonValueNumber>(Rotator.Yaw),
			MakeShared<FJsonValueNumber>(Rotator.Roll)
		};
	}
}

UEpisodeRecorderComponent::UEpisodeRecorderComponent()
{
	PrimaryComponentTick.bCanEverTick = false;
}

bool UEpisodeRecorderComponent::BeginEpisode(
	ASimTraceCharacter* InCharacter,
	ASimTraceCourseActor* InCourse,
	const FSimTraceRuntimeConfig& InConfig,
	const int32 EpisodeIndex,
	const FString& InParentEpisodeId)
{
	if (!InCharacter || !InCourse || bRecording)
	{
		return false;
	}

	Character = InCharacter;
	Course = InCourse;
	RuntimeConfig = InConfig;
	ParentEpisodeId = InParentEpisodeId;
	StartedUtc = FDateTime::UtcNow();
	SamplesWritten = 0;
	CapturesWritten = 0;
	CapturesDropped = 0;
	LastEndReason = ESimTraceEndReason::None;

	const FString UtcId = StartedUtc.ToString(TEXT("%Y%m%dT%H%M%S%fZ"));
	EpisodeId = FString::Printf(
		TEXT("episode_%s_%s_s%d_%02d"),
		*UtcId,
		*LexToString(RuntimeConfig.Mode),
		Course->GetLayout().Seed,
		EpisodeIndex);
	ReplayName = TEXT("simtrace_") + EpisodeId;
	EpisodeDirectory = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("SimTrace"),
		TEXT("episodes"),
		EpisodeId);

	IFileManager& Files = IFileManager::Get();
	Files.MakeDirectory(*FPaths::Combine(EpisodeDirectory, TEXT("rgb")), true);
	Files.MakeDirectory(*FPaths::Combine(EpisodeDirectory, TEXT("depth")), true);
	Files.MakeDirectory(*FPaths::Combine(EpisodeDirectory, TEXT("replay")), true);

	const FString TrajectoryPath = FPaths::Combine(EpisodeDirectory, TEXT("trajectory.jsonl"));
	TrajectoryWriter.Reset(Files.CreateFileWriter(*TrajectoryPath));
	if (!TrajectoryWriter)
	{
		return false;
	}

	bRecording = true;
	return WriteManifest(false, ESimTraceEndReason::None);
}

bool UEpisodeRecorderComponent::RecordFrame(
	const bool bDone,
	const ESimTraceEndReason EndReason,
	const double FrameTimeMilliseconds,
	const bool bCaptured,
	const bool bCaptureDropped,
	const FString& RgbRelativePath,
	const FString& DepthRelativePath)
{
	if (!bRecording || !TrajectoryWriter || !Character.IsValid() || !Course.IsValid())
	{
		return false;
	}

	FSimTraceTrajectorySample Sample;
	Sample.SimFrame = SamplesWritten;
	Sample.TimestampSeconds = FSimTraceTrajectorySample::TimestampForFrame(SamplesWritten);
	Sample.Position = Character->GetActorLocation();
	Sample.Rotation = Character->GetController()
		? Character->GetController()->GetControlRotation()
		: Character->GetActorRotation();
	Sample.Velocity = Character->GetVelocity();
	Sample.GoalRelative =
		Character->GetActorTransform().InverseTransformPosition(Course->GetGoalLocation());
	Sample.MoveInput = Character->GetCurrentAction().Move;
	Sample.LookInput = Character->GetCurrentAction().Look;
	Sample.bJumpPressed = Character->GetCurrentAction().bJumpPressed;
	Sample.bCollision = Character->ConsumeCollision();
	Sample.bCaptured = bCaptured;
	Sample.bCaptureDropped = bCaptureDropped;
	Sample.RgbRelativePath = RgbRelativePath;
	Sample.DepthRelativePath = DepthRelativePath;
	Sample.FrameTimeMilliseconds = FrameTimeMilliseconds;
	Sample.bDone = bDone;
	Sample.EndReason = EndReason;

	const FString Line = Sample.ToJsonLine() + LINE_TERMINATOR;
	FTCHARToUTF8 Utf8(*Line);
	TrajectoryWriter->Serialize(const_cast<ANSICHAR*>(Utf8.Get()), Utf8.Length());
	TrajectoryWriter->Flush();
	if (TrajectoryWriter->IsError())
	{
		return false;
	}

	++SamplesWritten;
	if (bCaptured)
	{
		++CapturesWritten;
	}
	if (bCaptureDropped)
	{
		++CapturesDropped;
	}
	return true;
}

bool UEpisodeRecorderComponent::FinishEpisode(const ESimTraceEndReason EndReason)
{
	if (!bRecording)
	{
		return false;
	}

	if (TrajectoryWriter)
	{
		TrajectoryWriter->Close();
		TrajectoryWriter.Reset();
	}
	bRecording = false;
	LastEndReason = EndReason;
	return WriteManifest(true, LastEndReason);
}

bool UEpisodeRecorderComponent::RefreshCompletedManifest()
{
	return !bRecording && !EpisodeDirectory.IsEmpty() && WriteManifest(true, LastEndReason);
}

bool UEpisodeRecorderComponent::WriteManifest(
	const bool bComplete,
	const ESimTraceEndReason EndReason)
{
	if (!Course.IsValid())
	{
		return false;
	}

	int32 FileCount = 0;
	int64 TotalBytes = 0;
	CountEpisodeFiles(FileCount, TotalBytes);

	const FSimTraceCourseLayout& Layout = Course->GetLayout();
	const TSharedRef<FJsonObject> Manifest = MakeShared<FJsonObject>();
	Manifest->SetNumberField(TEXT("schema_version"), 1);
	Manifest->SetStringField(TEXT("episode_id"), EpisodeId);
	Manifest->SetStringField(TEXT("mode"), LexToString(RuntimeConfig.Mode));
	Manifest->SetNumberField(TEXT("seed"), Layout.Seed);
	Manifest->SetStringField(TEXT("parent_episode_id"), ParentEpisodeId);
	Manifest->SetStringField(TEXT("course_hash"), Layout.CourseHash);
	Manifest->SetArrayField(TEXT("start_position_cm"), VectorToJson(Layout.StartTransform.GetLocation()));
	Manifest->SetArrayField(TEXT("start_rotation_deg"), RotatorToJson(Layout.StartTransform.Rotator()));
	Manifest->SetArrayField(TEXT("goal_position_cm"), VectorToJson(Layout.GoalTransform.GetLocation()));
	Manifest->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());

	FString GitRevision = FPlatformMisc::GetEnvironmentVariable(TEXT("SIMTRACE_GIT_REVISION"));
	if (GitRevision.IsEmpty())
	{
		GitRevision = TEXT("unavailable");
	}
	Manifest->SetStringField(TEXT("git_revision"), GitRevision);
	Manifest->SetNumberField(TEXT("simulation_hz"), 30);
	Manifest->SetNumberField(TEXT("capture_hz"), RuntimeConfig.bCapture ? 10 : 0);
	Manifest->SetNumberField(TEXT("image_width"), 320);
	Manifest->SetNumberField(TEXT("image_height"), 180);
	Manifest->SetStringField(TEXT("sensor_type"), TEXT("scene_depth"));
	Manifest->SetStringField(TEXT("depth_encoding"), TEXT("uint16_linear_cm"));
	Manifest->SetNumberField(TEXT("depth_max_cm"), 2000);
	Manifest->SetNumberField(TEXT("trajectory_frames"), SamplesWritten);
	Manifest->SetNumberField(TEXT("capture_frames"), CapturesWritten);
	Manifest->SetNumberField(TEXT("capture_dropped"), CapturesDropped);
	Manifest->SetNumberField(TEXT("file_count"), FileCount);
	Manifest->SetNumberField(TEXT("total_bytes"), static_cast<double>(TotalBytes));
	Manifest->SetStringField(TEXT("started_utc"), StartedUtc.ToIso8601());
	Manifest->SetNumberField(
		TEXT("duration_s"),
		static_cast<double>(SamplesWritten) / FSimTraceTrajectorySample::SimulationHz);
	Manifest->SetStringField(TEXT("end_reason"), LexToString(EndReason));
	Manifest->SetStringField(TEXT("replay_name"), ReplayName);
	Manifest->SetStringField(
		TEXT("replay_archive_path"),
		FString::Printf(TEXT("replay/%s.replay"), *ReplayName));
	Manifest->SetBoolField(TEXT("complete"), bComplete);

	FString Json;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
	FJsonSerializer::Serialize(Manifest, Writer);

	const FString PartialPath = FPaths::Combine(EpisodeDirectory, TEXT("manifest.partial.json"));
	if (!bComplete)
	{
		return FFileHelper::SaveStringToFile(Json, *PartialPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	const FString TemporaryPath = FPaths::Combine(EpisodeDirectory, TEXT("manifest.json.tmp"));
	const FString FinalPath = FPaths::Combine(EpisodeDirectory, TEXT("manifest.json"));
	if (!FFileHelper::SaveStringToFile(
			Json,
			*TemporaryPath,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		return false;
	}

	const bool bMoved = IFileManager::Get().Move(*FinalPath, *TemporaryPath, true, true);
	if (bMoved)
	{
		IFileManager::Get().Delete(*PartialPath, false, true);
	}
	return bMoved;
}

void UEpisodeRecorderComponent::CountEpisodeFiles(int32& OutFileCount, int64& OutTotalBytes) const
{
	OutFileCount = 0;
	OutTotalBytes = 0;
	IFileManager::Get().IterateDirectoryRecursively(
		*EpisodeDirectory,
		[&OutFileCount, &OutTotalBytes](const TCHAR* Path, const bool bIsDirectory)
		{
			if (!bIsDirectory)
			{
				++OutFileCount;
				OutTotalBytes += FMath::Max<int64>(0, IFileManager::Get().FileSize(Path));
			}
			return true;
		});
}
