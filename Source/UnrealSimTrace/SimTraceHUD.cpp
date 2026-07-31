#include "SimTraceHUD.h"

#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "SimTraceCharacter.h"
#include "SimTraceGameInstance.h"
#include "SimTraceGameMode.h"

void ASimTraceHUD::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas)
	{
		return;
	}

	const float CenterX = Canvas->SizeX * 0.5f;
	const float CenterY = Canvas->SizeY * 0.5f;
	DrawCrosshair(CenterX, CenterY);

	const ASimTraceCharacter* Character = PlayerOwner
		? Cast<ASimTraceCharacter>(PlayerOwner->GetPawn())
		: nullptr;
	if (Character &&
		Character->GetLastShotOutcome().bHit &&
		Character->GetLastShotWorldTimeSeconds() >= 0.0 &&
		GetWorld()->GetTimeSeconds() -
				Character->GetLastShotWorldTimeSeconds() <=
			0.3)
	{
		DrawHitMarker(CenterX, CenterY);
	}

	const ASimTraceGameMode* GameMode =
		GetWorld()->GetAuthGameMode<ASimTraceGameMode>();
	const USimTraceGameInstance* GameInstance =
		GetWorld()->GetGameInstance<USimTraceGameInstance>();
	const FString Mode = GameInstance
		? LexToString(GameInstance->GetRuntimeConfig().Mode).ToUpper()
		: TEXT("UNKNOWN");
	const int32 Seed = GameMode ? GameMode->GetCurrentSeed() : 0;
	const int32 Frame = GameMode ? GameMode->GetCurrentSimFrame() : 0;
	const int32 Shots = Character ? Character->GetShotsFired() : 0;
	const int32 Hits = Character ? Character->GetHitsConfirmed() : 0;

	DrawRect(FLinearColor(0.015f, 0.02f, 0.018f, 0.82f), 22.0f, 22.0f, 330.0f, 118.0f);
	DrawRect(FLinearColor(0.72f, 0.55f, 0.18f, 1.0f), 22.0f, 22.0f, 5.0f, 118.0f);
	DrawText(
		TEXT("SIMTRACE RANGE CONTROL"),
		FLinearColor(0.93f, 0.82f, 0.44f),
		40.0f,
		34.0f,
		GEngine ? GEngine->GetSmallFont() : nullptr,
		1.15f);
	DrawText(
		FString::Printf(TEXT("MODE  %s     SEED  %d"), *Mode, Seed),
		FLinearColor::White,
		40.0f,
		67.0f,
		GEngine ? GEngine->GetSmallFont() : nullptr);
	DrawText(
		FString::Printf(TEXT("FRAME %06d     SHOT %d  HIT %d"), Frame, Shots, Hits),
		FLinearColor(0.78f, 0.82f, 0.76f),
		40.0f,
		93.0f,
		GEngine ? GEngine->GetSmallFont() : nullptr);

	const FString Outcome = Character && Character->GetLastShotOutcome().bShotFired
		? FString::Printf(
			TEXT("%s  %s  %.1f cm"),
			Character->GetLastShotOutcome().bHit ? TEXT("HIT") : TEXT("MISS"),
			*Character->GetLastShotOutcome().TargetId,
			Character->GetLastShotOutcome().DistanceCentimeters)
		: TEXT("LMB: FIRE ONE TRACE");
	DrawRect(
		FLinearColor(0.015f, 0.02f, 0.018f, 0.78f),
		Canvas->SizeX - 340.0f,
		Canvas->SizeY - 92.0f,
		318.0f,
		66.0f);
	DrawText(
		TEXT("ONE BULLET OUTCOME LEDGER"),
		FLinearColor(0.93f, 0.82f, 0.44f),
		Canvas->SizeX - 325.0f,
		Canvas->SizeY - 78.0f,
		GEngine ? GEngine->GetSmallFont() : nullptr);
	DrawText(
		Outcome,
		FLinearColor::White,
		Canvas->SizeX - 325.0f,
		Canvas->SizeY - 52.0f,
		GEngine ? GEngine->GetSmallFont() : nullptr);

	DrawText(
		TEXT("WASD MOVE   MOUSE AIM   LMB FIRE   SPACE JUMP   R RESET"),
		FLinearColor(0.82f, 0.84f, 0.80f),
		26.0f,
		Canvas->SizeY - 38.0f,
		GEngine ? GEngine->GetSmallFont() : nullptr);
}

void ASimTraceHUD::DrawCrosshair(const float CenterX, const float CenterY)
{
	const FLinearColor Color(0.95f, 0.95f, 0.9f, 0.92f);
	constexpr float Gap = 7.0f;
	constexpr float Length = 10.0f;
	constexpr float Thickness = 1.5f;
	DrawLine(CenterX - Gap - Length, CenterY, CenterX - Gap, CenterY, Color, Thickness);
	DrawLine(CenterX + Gap, CenterY, CenterX + Gap + Length, CenterY, Color, Thickness);
	DrawLine(CenterX, CenterY - Gap - Length, CenterX, CenterY - Gap, Color, Thickness);
	DrawLine(CenterX, CenterY + Gap, CenterX, CenterY + Gap + Length, Color, Thickness);
	DrawRect(Color, CenterX - 1.0f, CenterY - 1.0f, 2.0f, 2.0f);
}

void ASimTraceHUD::DrawHitMarker(const float CenterX, const float CenterY)
{
	const FLinearColor Color(0.88f, 0.28f, 0.18f, 1.0f);
	constexpr float Inner = 9.0f;
	constexpr float Outer = 17.0f;
	constexpr float Thickness = 2.0f;
	DrawLine(CenterX - Outer, CenterY - Outer, CenterX - Inner, CenterY - Inner, Color, Thickness);
	DrawLine(CenterX + Inner, CenterY - Inner, CenterX + Outer, CenterY - Outer, Color, Thickness);
	DrawLine(CenterX - Outer, CenterY + Outer, CenterX - Inner, CenterY + Inner, Color, Thickness);
	DrawLine(CenterX + Inner, CenterY + Inner, CenterX + Outer, CenterY + Outer, Color, Thickness);
}
