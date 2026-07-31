#if WITH_DEV_AUTOMATION_TESTS

#include "Dom/JsonObject.h"
#include "EnhancedActionKeyMapping.h"
#include "InputCoreTypes.h"
#include "InputMappingContext.h"
#include "InputModifiers.h"
#include "Misc/AutomationTest.h"
#include "SimTraceCaptureComponent.h"
#include "SimTraceCharacter.h"
#include "SimTraceCourseLayout.h"
#include "SimTraceGameMode.h"
#include "SimTraceHUD.h"
#include "SimTraceRuntimeConfig.h"
#include "SimTraceTypes.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceCourseDeterminismTest,
	"SimTrace.Core.CourseDeterminism",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceCourseDeterminismTest::RunTest(const FString& Parameters)
{
	const FSimTraceCourseLayout First = FSimTraceCourseLayout::Generate(1000);
	const FSimTraceCourseLayout Second = FSimTraceCourseLayout::Generate(1000);
	const FSimTraceCourseLayout Different = FSimTraceCourseLayout::Generate(1001);

	TestEqual(TEXT("Same seed has the same course hash"), First.CourseHash, Second.CourseHash);
	TestEqual(TEXT("Same seed has the same start"), First.StartTransform.ToString(), Second.StartTransform.ToString());
	TestEqual(TEXT("Same seed has the same goal"), First.GoalTransform.ToString(), Second.GoalTransform.ToString());
	TestNotEqual(TEXT("Different seeds vary the course"), First.CourseHash, Different.CourseHash);
	TestEqual(
		TEXT("Course includes target and range dressing"),
		First.Elements.Num(),
		13);
	TestEqual(
		TEXT("Target transform is stable for the same seed"),
		First.TargetTransform.ToString(),
		Second.TargetTransform.ToString());
	TestTrue(TEXT("Bot path ends inside the goal"), First.Waypoints.Last().X >= 3000.0);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceTrajectorySerializationTest,
	"SimTrace.Core.TrajectorySerialization",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceTrajectorySerializationTest::RunTest(const FString& Parameters)
{
	FSimTraceTrajectorySample Sample;
	Sample.SimFrame = 42;
	Sample.TimestampSeconds = FSimTraceTrajectorySample::TimestampForFrame(42);
	Sample.Position = FVector(120.0, 35.0, 90.0);
	Sample.MoveInput = FVector2D(1.0, 0.0);
	Sample.bFirePressed = true;
	Sample.ShotOutcome.bShotFired = true;
	Sample.ShotOutcome.ShotId = 7;
	Sample.ShotOutcome.Origin = FVector(120.0, 35.0, 154.0);
	Sample.ShotOutcome.Direction = FVector(1.0, 0.0, 0.0);
	Sample.ShotOutcome.bHit = true;
	Sample.ShotOutcome.TargetId = TEXT("target_alpha");
	Sample.ShotOutcome.ImpactPosition = FVector(2950.0, 35.0, 154.0);
	Sample.ShotOutcome.DistanceCentimeters = 2830.0;
	Sample.bDone = true;
	Sample.EndReason = ESimTraceEndReason::Goal;

	const FString JsonLine = Sample.ToJsonLine();
	TSharedPtr<FJsonObject> Parsed;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonLine);
	TestTrue(TEXT("Trajectory is valid JSON"), FJsonSerializer::Deserialize(Reader, Parsed) && Parsed.IsValid());
	TestTrue(TEXT("Frame is serialized"), JsonLine.Contains(TEXT("\"sim_frame\":42")));
	TestTrue(
		TEXT("Timestamp uses the fixed 30 Hz clock"),
		Parsed.IsValid() && FMath::IsNearlyEqual(Parsed->GetNumberField(TEXT("timestamp_s")), 1.4, 0.000001));
	TestTrue(TEXT("Done state is serialized"), JsonLine.Contains(TEXT("\"done\":true")));
	TestTrue(TEXT("End reason is serialized"), JsonLine.Contains(TEXT("\"end_reason\":\"goal\"")));
	const TArray<TSharedPtr<FJsonValue>>* CombatEvents = nullptr;
	TestTrue(
		TEXT("Combat event ledger is serialized"),
		Parsed.IsValid() &&
			Parsed->TryGetArrayField(TEXT("combat_events"), CombatEvents) &&
			CombatEvents &&
			CombatEvents->Num() == 3);
	if (CombatEvents && CombatEvents->Num() == 3)
	{
		TestEqual(
			TEXT("Ledger begins with fire"),
			(*CombatEvents)[0]->AsObject()->GetStringField(TEXT("event")),
			FString(TEXT("fire")));
		TestEqual(
			TEXT("Ledger records the shot second"),
			(*CombatEvents)[1]->AsObject()->GetStringField(TEXT("event")),
			FString(TEXT("shot")));
		TestEqual(
			TEXT("Ledger records the hit outcome last"),
			(*CombatEvents)[2]->AsObject()->GetStringField(TEXT("event")),
			FString(TEXT("hit")));
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceRuntimePresentationTest,
	"SimTrace.Core.RuntimePresentation",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceRuntimePresentationTest::RunTest(const FString& Parameters)
{
	const ASimTraceGameMode* GameMode = GetDefault<ASimTraceGameMode>();
	TestTrue(
		TEXT("Game mode uses the runtime range HUD"),
		GameMode->HUDClass == ASimTraceHUD::StaticClass());
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceRuntimeConfigTest,
	"SimTrace.Core.RuntimeConfig",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceRuntimeConfigTest::RunTest(const FString& Parameters)
{
	const FSimTraceRuntimeConfig Config = FSimTraceRuntimeConfig::Parse(
		TEXT("-SimTraceMode=bot -SimTraceSeed=1000 -SimTraceBatchCount=10 ")
		TEXT("-SimTraceCapture=0 -SimTraceMaxSeconds=45"));

	TestEqual(TEXT("Mode parses"), Config.Mode, ESimTraceMode::Bot);
	TestEqual(TEXT("Seed parses"), Config.Seed, 1000);
	TestEqual(TEXT("Batch count parses"), Config.BatchCount, 10);
	TestFalse(TEXT("Capture toggle parses"), Config.bCapture);
	TestEqual(TEXT("Timeout parses"), Config.MaxSeconds, 45.0);

	const FSimTraceRuntimeConfig SafeDefaults = FSimTraceRuntimeConfig::Parse(
		TEXT("-SimTraceBatchCount=0 -SimTraceMaxSeconds=-1"));
	TestEqual(TEXT("Batch count is clamped"), SafeDefaults.BatchCount, 1);
	TestEqual(TEXT("Timeout is clamped"), SafeDefaults.MaxSeconds, 1.0);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceDepthEncodingTest,
	"SimTrace.Core.DepthEncoding",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceDepthEncodingTest::RunTest(const FString& Parameters)
{
	TestEqual(TEXT("Invalid depth is zero"), FSimTraceDepthEncoding::EncodeCentimeters(0.0f), uint16(0));
	TestEqual(TEXT("Half range maps to midpoint"), FSimTraceDepthEncoding::EncodeCentimeters(1000.0f), uint16(32768));
	TestEqual(TEXT("Maximum depth saturates"), FSimTraceDepthEncoding::EncodeCentimeters(2000.0f), uint16(65535));
	TestEqual(TEXT("Beyond maximum saturates"), FSimTraceDepthEncoding::EncodeCentimeters(5000.0f), uint16(65535));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceHumanLookMappingTest,
	"SimTrace.Core.HumanLookMapping",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceHumanLookMappingTest::RunTest(const FString& Parameters)
{
	ASimTraceCharacter* Character = GetMutableDefault<ASimTraceCharacter>();
	UInputMappingContext* MappingContext =
		Cast<UInputMappingContext>(Character->GetDefaultSubobjectByName(TEXT("IMC_SimTrace")));
	if (!TestNotNull(TEXT("Runtime mapping context exists"), MappingContext))
	{
		return false;
	}

	const FEnhancedActionKeyMapping* LookMapping =
		MappingContext->GetMappings().FindByPredicate(
			[Character](const FEnhancedActionKeyMapping& Mapping)
			{
				return Mapping.Action == Character->GetLookAction() && Mapping.Key == EKeys::Mouse2D;
			});
	if (!TestNotNull(TEXT("Mouse2D is mapped to the look action"), LookMapping))
	{
		return false;
	}

	const UInputModifierNegate* VerticalNegate = nullptr;
	for (const TObjectPtr<UInputModifier>& Modifier : LookMapping->Modifiers)
	{
		if (const UInputModifierNegate* Negate = Cast<UInputModifierNegate>(Modifier.Get());
			Negate && !Negate->bX && Negate->bY && !Negate->bZ)
		{
			VerticalNegate = Negate;
			break;
		}
	}

	TestNotNull(TEXT("Mouse look negates only the vertical axis"), VerticalNegate);

	const FEnhancedActionKeyMapping* FireMapping =
		MappingContext->GetMappings().FindByPredicate(
			[Character](const FEnhancedActionKeyMapping& Mapping)
			{
				return Mapping.Action == Character->GetFireAction() &&
					Mapping.Key == EKeys::LeftMouseButton;
			});
	TestNotNull(TEXT("Left mouse button is mapped to fire"), FireMapping);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FSimTraceManifestTotalBytesTest,
	"SimTrace.Core.ManifestTotalBytes",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSimTraceManifestTotalBytesTest::RunTest(const FString& Parameters)
{
	const TSharedRef<FJsonObject> Manifest = MakeShared<FJsonObject>();
	Manifest->SetStringField(TEXT("episode_id"), TEXT("episode_boundary"));
	Manifest->SetNumberField(TEXT("total_bytes"), 99999);

	FString FiveDigitJson;
	const TSharedRef<TJsonWriter<>> FiveDigitWriter =
		TJsonWriterFactory<>::Create(&FiveDigitJson);
	TestTrue(
		TEXT("Five-digit manifest serializes"),
		FJsonSerializer::Serialize(Manifest, FiveDigitWriter));

	const int64 FiveDigitBytes = FTCHARToUTF8(*FiveDigitJson).Length();
	const int64 PayloadBytes = 100000 - FiveDigitBytes;
	FString StableJson;
	TestTrue(
		TEXT("Manifest total converges across a digit boundary"),
		FSimTraceManifestAccounting::SerializeWithStableTotalBytes(
			Manifest,
			PayloadBytes,
			StableJson));

	const int64 StableJsonBytes = FTCHARToUTF8(*StableJson).Length();
	const int64 RecordedTotalBytes =
		static_cast<int64>(Manifest->GetNumberField(TEXT("total_bytes")));
	TestEqual(
		TEXT("Recorded total includes the final manifest byte size"),
		RecordedTotalBytes,
		PayloadBytes + StableJsonBytes);
	TestTrue(
		TEXT("Test crosses from five to six digits"),
		RecordedTotalBytes >= 100000);
	return true;
}

#endif
