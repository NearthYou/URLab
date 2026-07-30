#pragma once

#include "CoreMinimal.h"
#include "Async/Future.h"
#include "Components/SceneComponent.h"
#include "SimTraceCaptureComponent.generated.h"

class USceneCaptureComponent2D;
class UTextureRenderTarget2D;

struct UNREALSIMTRACE_API FSimTraceDepthEncoding
{
	static constexpr float MaximumCentimeters = 2000.0f;
	static uint16 EncodeCentimeters(float DepthCentimeters);
};

struct UNREALSIMTRACE_API FSimTraceCaptureResult
{
	bool bCaptured = false;
	bool bDropped = false;
	bool bError = false;
	FString RgbRelativePath;
	FString DepthRelativePath;
};

UCLASS(ClassGroup=(SimTrace))
class UNREALSIMTRACE_API USimTraceCaptureComponent : public USceneComponent
{
	GENERATED_BODY()

public:
	USimTraceCaptureComponent();

	bool InitializeCapture();
	FSimTraceCaptureResult CaptureFrame(int32 SimFrame, const FString& EpisodeDirectory);
	bool FlushPendingWrites();
	bool HasWriteError() const { return bWriteError; }

protected:
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
	static constexpr int32 CaptureWidth = 320;
	static constexpr int32 CaptureHeight = 180;
	static constexpr int32 MaximumPendingPairs = 8;

	UPROPERTY()
	TObjectPtr<USceneCaptureComponent2D> RgbCapture;

	UPROPERTY()
	TObjectPtr<USceneCaptureComponent2D> DepthCapture;

	UPROPERTY()
	TObjectPtr<UTextureRenderTarget2D> RgbTarget;

	UPROPERTY()
	TObjectPtr<UTextureRenderTarget2D> DepthTarget;

	TArray<TFuture<bool>> PendingWrites;
	bool bInitialized = false;
	bool bWriteError = false;

	void PruneFinishedWrites();
	static bool EncodeAndWriteImages(
		FImage RgbImage,
		FImage DepthImage,
		const FString& RgbPath,
		const FString& DepthPath);
};

