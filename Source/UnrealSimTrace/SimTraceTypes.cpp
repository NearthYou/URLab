#include "SimTraceTypes.h"

#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

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

	TArray<TSharedPtr<FJsonValue>> Vector2DToJson(const FVector2D& Vector)
	{
		return {
			MakeShared<FJsonValueNumber>(Vector.X),
			MakeShared<FJsonValueNumber>(Vector.Y)
		};
	}

	void SetOptionalString(
		const TSharedRef<FJsonObject>& Object,
		const FString& FieldName,
		const FString& Value)
	{
		if (Value.IsEmpty())
		{
			Object->SetField(FieldName, MakeShared<FJsonValueNull>());
			return;
		}

		Object->SetStringField(FieldName, Value);
	}
}

FString LexToString(const ESimTraceMode Mode)
{
	switch (Mode)
	{
	case ESimTraceMode::Human:
		return TEXT("human");
	case ESimTraceMode::Bot:
		return TEXT("bot");
	case ESimTraceMode::InputReplay:
		return TEXT("input-replay");
	case ESimTraceMode::NativeReplay:
		return TEXT("native-replay");
	default:
		return TEXT("human");
	}
}

FString LexToString(const ESimTraceEndReason Reason)
{
	switch (Reason)
	{
	case ESimTraceEndReason::Goal:
		return TEXT("goal");
	case ESimTraceEndReason::Timeout:
		return TEXT("timeout");
	case ESimTraceEndReason::Fell:
		return TEXT("fell");
	case ESimTraceEndReason::ManualAbort:
		return TEXT("manual_abort");
	case ESimTraceEndReason::CaptureError:
		return TEXT("capture_error");
	case ESimTraceEndReason::IoError:
		return TEXT("io_error");
	case ESimTraceEndReason::ReplaySourceEnd:
		return TEXT("replay_source_end");
	default:
		return TEXT("");
	}
}

FString FSimTraceTrajectorySample::ToJsonLine() const
{
	const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
	const double RoundedTimestamp = FMath::RoundToDouble(TimestampSeconds * 1000000.0) / 1000000.0;
	const double RoundedDelta = FMath::RoundToDouble(DeltaSeconds * 1000000.0) / 1000000.0;
	Object->SetNumberField(TEXT("schema_version"), 1);
	Object->SetNumberField(TEXT("sim_frame"), SimFrame);
	Object->SetNumberField(TEXT("timestamp_s"), RoundedTimestamp);
	Object->SetNumberField(TEXT("delta_s"), RoundedDelta);
	Object->SetArrayField(TEXT("position_cm"), VectorToJson(Position));
	Object->SetArrayField(TEXT("rotation_deg"), RotatorToJson(Rotation));
	Object->SetArrayField(TEXT("velocity_cm_s"), VectorToJson(Velocity));
	Object->SetArrayField(TEXT("goal_relative_cm"), VectorToJson(GoalRelative));
	Object->SetArrayField(TEXT("move_input"), Vector2DToJson(MoveInput));
	Object->SetArrayField(TEXT("look_input"), Vector2DToJson(LookInput));
	Object->SetBoolField(TEXT("jump_pressed"), bJumpPressed);
	Object->SetBoolField(TEXT("collision"), bCollision);
	Object->SetBoolField(TEXT("captured"), bCaptured);
	Object->SetBoolField(TEXT("capture_dropped"), bCaptureDropped);
	SetOptionalString(Object, TEXT("rgb_path"), RgbRelativePath);
	SetOptionalString(Object, TEXT("depth_path"), DepthRelativePath);
	Object->SetNumberField(TEXT("frame_time_ms"), FrameTimeMilliseconds);
	Object->SetBoolField(TEXT("done"), bDone);
	Object->SetStringField(TEXT("end_reason"), LexToString(EndReason));

	FString Output;
	const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Output);
	FJsonSerializer::Serialize(Object, Writer);
	return Output;
}
