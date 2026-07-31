#pragma once

#include "CoreMinimal.h"

class FJsonObject;

enum class ESimTraceMode : uint8
{
	Human,
	Bot,
	InputReplay,
	NativeReplay
};

enum class ESimTraceEndReason : uint8
{
	None,
	Goal,
	Timeout,
	Fell,
	ManualAbort,
	CaptureError,
	IoError,
	ReplaySourceEnd
};

UNREALSIMTRACE_API FString LexToString(ESimTraceMode Mode);
UNREALSIMTRACE_API FString LexToString(ESimTraceEndReason Reason);

struct UNREALSIMTRACE_API FSimTraceManifestAccounting
{
	static bool SerializeWithStableTotalBytes(
		const TSharedRef<FJsonObject>& Manifest,
		int64 PayloadBytes,
		FString& OutJson);
};

struct UNREALSIMTRACE_API FSimTraceActionState
{
	FVector2D Move = FVector2D::ZeroVector;
	FVector2D Look = FVector2D::ZeroVector;
	bool bJumpPressed = false;
	bool bFirePressed = false;
};

struct UNREALSIMTRACE_API FSimTraceShotOutcome
{
	bool bShotFired = false;
	int32 ShotId = INDEX_NONE;
	FVector Origin = FVector::ZeroVector;
	FVector Direction = FVector::ForwardVector;
	bool bHit = false;
	FString TargetId;
	FVector ImpactPosition = FVector::ZeroVector;
	double DistanceCentimeters = 0.0;
};

struct UNREALSIMTRACE_API FSimTraceTrajectorySample
{
	int32 SimFrame = 0;
	double TimestampSeconds = 0.0;
	double DeltaSeconds = 1.0 / 30.0;
	FVector Position = FVector::ZeroVector;
	FRotator Rotation = FRotator::ZeroRotator;
	FVector Velocity = FVector::ZeroVector;
	FVector GoalRelative = FVector::ZeroVector;
	FVector2D MoveInput = FVector2D::ZeroVector;
	FVector2D LookInput = FVector2D::ZeroVector;
	bool bJumpPressed = false;
	bool bFirePressed = false;
	FSimTraceShotOutcome ShotOutcome;
	bool bCollision = false;
	bool bCaptured = false;
	bool bCaptureDropped = false;
	FString RgbRelativePath;
	FString DepthRelativePath;
	double FrameTimeMilliseconds = 0.0;
	bool bDone = false;
	ESimTraceEndReason EndReason = ESimTraceEndReason::None;

	static constexpr double SimulationHz = 30.0;
	static double TimestampForFrame(const int32 Frame)
	{
		return static_cast<double>(Frame) / SimulationHz;
	}

	FString ToJsonLine() const;
};
