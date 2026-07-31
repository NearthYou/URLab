#include "EpisodeRecorderComponent.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "SimTraceCharacter.h"
#include "SimTraceCourseActor.h"

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

	FString ResolveGitRevision()
	{
		static const FString Revision = []
		{
			FString Value = FPlatformMisc::GetEnvironmentVariable(TEXT("SIMTRACE_GIT_REVISION"));
			Value.TrimStartAndEndInline();
			if (!Value.IsEmpty())
			{
				return Value;
			}

			int32 ReturnCode = INDEX_NONE;
			FString StandardOutput;
			FString StandardError;
			const FString WorkingDirectory =
				FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
			if (FPlatformProcess::ExecProcess(
					TEXT("git.exe"),
					TEXT("rev-parse --short=12 HEAD"),
					&ReturnCode,
					&StandardOutput,
					&StandardError,
					*WorkingDirectory) &&
				ReturnCode == 0)
			{
				StandardOutput.TrimStartAndEndInline();
				if (!StandardOutput.IsEmpty())
				{
					return StandardOutput;
				}
			}

			return FString(TEXT("unavailable"));
		}();
		return Revision;
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
	ShotsFired = 0;
	ShotsHit = 0;
	LastEndReason = ESimTraceEndReason::None;

	const FString UtcId = FString::Printf(
		TEXT("%s%03dZ"),
		*StartedUtc.ToString(TEXT("%Y%m%dT%H%M%S")),
		StartedUtc.GetMillisecond());
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
	const FSimTraceActionState& CurrentAction = Character->GetCurrentAction();
	Sample.MoveInput = CurrentAction.Move;
	Sample.LookInput = CurrentAction.Look;
	Sample.bJumpPressed = CurrentAction.bJumpPressed;
	Sample.bFirePressed = CurrentAction.bFirePressed;
	Sample.ShotOutcome = Character->GetCurrentShotOutcome();
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
	if (Sample.ShotOutcome.bShotFired)
	{
		++ShotsFired;
		if (Sample.ShotOutcome.bHit)
		{
			++ShotsHit;
		}
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

	int32 PayloadFileCount = 0;
	int64 PayloadBytes = 0;
	CountEpisodePayloadFiles(PayloadFileCount, PayloadBytes);

	const FSimTraceCourseLayout& Layout = Course->GetLayout();
	const TSharedRef<FJsonObject> Manifest = MakeShared<FJsonObject>();
	Manifest->SetNumberField(TEXT("schema_version"), 2);
	Manifest->SetStringField(TEXT("episode_id"), EpisodeId);
	Manifest->SetStringField(TEXT("mode"), LexToString(RuntimeConfig.Mode));
	Manifest->SetNumberField(TEXT("seed"), Layout.Seed);
	Manifest->SetStringField(TEXT("parent_episode_id"), ParentEpisodeId);
	Manifest->SetStringField(TEXT("course_hash"), Layout.CourseHash);
	Manifest->SetArrayField(TEXT("start_position_cm"), VectorToJson(Layout.StartTransform.GetLocation()));
	Manifest->SetArrayField(TEXT("start_rotation_deg"), RotatorToJson(Layout.StartTransform.Rotator()));
	Manifest->SetArrayField(TEXT("goal_position_cm"), VectorToJson(Layout.GoalTransform.GetLocation()));
	Manifest->SetArrayField(TEXT("target_position_cm"), VectorToJson(Layout.TargetTransform.GetLocation()));
	Manifest->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());

	Manifest->SetStringField(TEXT("git_revision"), ResolveGitRevision());
	Manifest->SetNumberField(TEXT("simulation_hz"), 30);
	Manifest->SetNumberField(TEXT("capture_hz"), RuntimeConfig.bCapture ? 10 : 0);
	Manifest->SetNumberField(TEXT("capture_interval_sim_frames"), 3);
	Manifest->SetNumberField(TEXT("capture_queue_capacity"), 8);
	Manifest->SetNumberField(TEXT("image_width"), 320);
	Manifest->SetNumberField(TEXT("image_height"), 180);
	Manifest->SetStringField(TEXT("sensor_type"), TEXT("scene_depth"));
	Manifest->SetStringField(TEXT("depth_encoding"), TEXT("uint16_linear_cm"));
	Manifest->SetNumberField(TEXT("depth_max_cm"), 2000);
	Manifest->SetStringField(
		TEXT("depth_decode_cm"),
		TEXT("value == 0 ? invalid : min(value / 65535.0 * 2000.0, 2000.0)"));
	Manifest->SetNumberField(TEXT("trajectory_frames"), SamplesWritten);
	Manifest->SetNumberField(TEXT("capture_frames"), CapturesWritten);
	Manifest->SetNumberField(TEXT("capture_dropped"), CapturesDropped);
	Manifest->SetStringField(
		TEXT("combat_contract"),
		TEXT("one_bullet_outcome_ledger_v1"));
	Manifest->SetStringField(TEXT("primary_target_id"), TEXT("target_alpha"));
	Manifest->SetNumberField(TEXT("shots_fired"), ShotsFired);
	Manifest->SetNumberField(TEXT("shots_hit"), ShotsHit);
	Manifest->SetNumberField(
		TEXT("shot_hit_rate"),
		ShotsFired > 0
			? static_cast<double>(ShotsHit) / static_cast<double>(ShotsFired)
			: 0.0);
	Manifest->SetNumberField(TEXT("file_count"), PayloadFileCount + 1);
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
	if (!FSimTraceManifestAccounting::SerializeWithStableTotalBytes(
			Manifest,
			PayloadBytes,
			Json))
	{
		return false;
	}

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

void UEpisodeRecorderComponent::CountEpisodePayloadFiles(
	int32& OutFileCount,
	int64& OutTotalBytes) const
{
	OutFileCount = 0;
	OutTotalBytes = 0;
	IFileManager::Get().IterateDirectoryRecursively(
		*EpisodeDirectory,
		[&OutFileCount, &OutTotalBytes](const TCHAR* Path, const bool bIsDirectory)
		{
			if (!bIsDirectory)
			{
				const FString FileName = FPaths::GetCleanFilename(Path);
				if (FileName == TEXT("manifest.json") ||
					FileName == TEXT("manifest.partial.json") ||
					FileName == TEXT("manifest.json.tmp"))
				{
					return true;
				}
				++OutFileCount;
				OutTotalBytes += FMath::Max<int64>(0, IFileManager::Get().FileSize(Path));
			}
			return true;
		});
}
