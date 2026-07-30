#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "SimTraceCourseLayout.h"
#include "SimTraceRuntimeConfig.h"
#include "SimTraceTypes.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

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
	TestEqual(TEXT("Course has three slalom obstacles, a jump and a gate"), First.Elements.Num(), 8);
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

#endif
