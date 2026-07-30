#include "SimTraceCaptureComponent.h"

#include "Async/Async.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "ImageCore.h"
#include "ImageUtils.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

uint16 FSimTraceDepthEncoding::EncodeCentimeters(const float DepthCentimeters)
{
	if (!FMath::IsFinite(DepthCentimeters) || DepthCentimeters <= 0.0f)
	{
		return 0;
	}

	const float Normalized = FMath::Clamp(
		DepthCentimeters / MaximumCentimeters,
		0.0f,
		1.0f);
	return static_cast<uint16>(FMath::RoundToInt(Normalized * 65535.0f));
}

USimTraceCaptureComponent::USimTraceCaptureComponent()
{
	PrimaryComponentTick.bCanEverTick = false;

	RgbCapture = CreateDefaultSubobject<USceneCaptureComponent2D>(TEXT("RgbSceneCapture"));
	RgbCapture->SetupAttachment(this);
	RgbCapture->bCaptureEveryFrame = false;
	RgbCapture->bCaptureOnMovement = false;
	RgbCapture->bAlwaysPersistRenderingState = true;
	RgbCapture->CaptureSource = SCS_FinalColorLDR;
	RgbCapture->FOVAngle = 90.0f;
	RgbCapture->ShowFlags.SetMotionBlur(false);

	DepthCapture = CreateDefaultSubobject<USceneCaptureComponent2D>(TEXT("DepthSceneCapture"));
	DepthCapture->SetupAttachment(this);
	DepthCapture->bCaptureEveryFrame = false;
	DepthCapture->bCaptureOnMovement = false;
	DepthCapture->bAlwaysPersistRenderingState = true;
	DepthCapture->CaptureSource = SCS_SceneDepth;
	DepthCapture->FOVAngle = 90.0f;
	DepthCapture->ShowFlags.SetMotionBlur(false);
}

bool USimTraceCaptureComponent::InitializeCapture()
{
	if (bInitialized)
	{
		return true;
	}

	RgbTarget = NewObject<UTextureRenderTarget2D>(this, TEXT("RgbRenderTarget"));
	RgbTarget->RenderTargetFormat = RTF_RGBA8_SRGB;
	RgbTarget->ClearColor = FLinearColor::Black;
	RgbTarget->InitAutoFormat(CaptureWidth, CaptureHeight);
	RgbTarget->UpdateResourceImmediate(true);

	DepthTarget = NewObject<UTextureRenderTarget2D>(this, TEXT("DepthRenderTarget"));
	DepthTarget->RenderTargetFormat = RTF_R32f;
	DepthTarget->ClearColor = FLinearColor::Black;
	DepthTarget->bForceLinearGamma = true;
	DepthTarget->InitAutoFormat(CaptureWidth, CaptureHeight);
	DepthTarget->UpdateResourceImmediate(true);

	RgbCapture->TextureTarget = RgbTarget;
	DepthCapture->TextureTarget = DepthTarget;
	bInitialized = RgbTarget->GetResource() != nullptr && DepthTarget->GetResource() != nullptr;
	return bInitialized;
}

FSimTraceCaptureResult USimTraceCaptureComponent::CaptureFrame(
	const int32 SimFrame,
	const FString& EpisodeDirectory)
{
	FSimTraceCaptureResult Result;
	PruneFinishedWrites();
	if (!bInitialized || bWriteError)
	{
		Result.bError = true;
		return Result;
	}
	if (PendingWrites.Num() >= MaximumPendingPairs)
	{
		Result.bDropped = true;
		return Result;
	}

	RgbCapture->CaptureScene();
	DepthCapture->CaptureScene();

	FImage RgbImage;
	FImage DepthImage;
	if (!FImageUtils::GetRenderTargetImage(RgbTarget, RgbImage) ||
		!FImageUtils::GetRenderTargetImage(DepthTarget, DepthImage))
	{
		Result.bError = true;
		return Result;
	}

	Result.RgbRelativePath = FString::Printf(TEXT("rgb/%06d.png"), SimFrame);
	Result.DepthRelativePath = FString::Printf(TEXT("depth/%06d.png"), SimFrame);
	const FString RgbPath = FPaths::Combine(EpisodeDirectory, Result.RgbRelativePath);
	const FString DepthPath = FPaths::Combine(EpisodeDirectory, Result.DepthRelativePath);

	TFuture<bool> WriteFuture = Async(
		EAsyncExecution::ThreadPool,
		[Rgb = MoveTemp(RgbImage),
		 Depth = MoveTemp(DepthImage),
		 RgbPath,
		 DepthPath]() mutable
		{
			return EncodeAndWriteImages(
				MoveTemp(Rgb),
				MoveTemp(Depth),
				RgbPath,
				DepthPath);
		});
	PendingWrites.Add(MoveTemp(WriteFuture));
	Result.bCaptured = true;
	return Result;
}

bool USimTraceCaptureComponent::FlushPendingWrites()
{
	for (TFuture<bool>& Future : PendingWrites)
	{
		if (!Future.Get())
		{
			bWriteError = true;
		}
	}
	PendingWrites.Reset();
	return !bWriteError;
}

void USimTraceCaptureComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	FlushPendingWrites();
	Super::EndPlay(EndPlayReason);
}

void USimTraceCaptureComponent::PruneFinishedWrites()
{
	for (int32 Index = PendingWrites.Num() - 1; Index >= 0; --Index)
	{
		if (PendingWrites[Index].IsReady())
		{
			if (!PendingWrites[Index].Get())
			{
				bWriteError = true;
			}
			PendingWrites.RemoveAtSwap(Index);
		}
	}
}

bool USimTraceCaptureComponent::EncodeAndWriteImages(
	FImage RgbImage,
	FImage DepthImage,
	const FString& RgbPath,
	const FString& DepthPath)
{
	if (RgbImage.Format != ERawImageFormat::BGRA8)
	{
		RgbImage.ChangeFormat(ERawImageFormat::BGRA8, EGammaSpace::sRGB);
	}
	if (DepthImage.Format != ERawImageFormat::RGBA32F)
	{
		DepthImage.ChangeFormat(ERawImageFormat::RGBA32F, EGammaSpace::Linear);
	}

	FImage EncodedDepth(
		DepthImage.SizeX,
		DepthImage.SizeY,
		ERawImageFormat::G16,
		EGammaSpace::Linear);
	const TArrayView64<const FLinearColor> DepthPixels = DepthImage.AsRGBA32F();
	TArrayView64<uint16> EncodedPixels = EncodedDepth.AsG16();
	if (DepthPixels.Num() != EncodedPixels.Num())
	{
		return false;
	}

	for (int64 PixelIndex = 0; PixelIndex < DepthPixels.Num(); ++PixelIndex)
	{
		EncodedPixels[PixelIndex] =
			FSimTraceDepthEncoding::EncodeCentimeters(DepthPixels[PixelIndex].R);
	}

	TArray64<uint8> RgbPng;
	TArray64<uint8> DepthPng;
	if (!FImageUtils::CompressImage(RgbPng, TEXT(".png"), RgbImage) ||
		!FImageUtils::CompressImage(DepthPng, TEXT(".png"), EncodedDepth))
	{
		return false;
	}

	return FFileHelper::SaveArrayToFile(RgbPng, *RgbPath) &&
		FFileHelper::SaveArrayToFile(DepthPng, *DepthPath);
}

