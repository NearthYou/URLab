#include "SimTraceCourseLayout.h"

#include "Misc/Crc.h"

namespace
{
	FSimTraceCourseElement MakeElement(
		const FName Name,
		const FVector& Location,
		const FVector& Dimensions,
		const FRotator& Rotation = FRotator::ZeroRotator)
	{
		FSimTraceCourseElement Element;
		Element.Name = Name;
		Element.Transform = FTransform(Rotation, Location);
		Element.Dimensions = Dimensions;
		return Element;
	}

	FString BuildCourseHash(const FSimTraceCourseLayout& Layout)
	{
		FString Canonical = FString::Printf(
			TEXT("seed=%d|start=%s|goal=%s|target=%s|gate=%.3f"),
			Layout.Seed,
			*Layout.StartTransform.ToString(),
			*Layout.GoalTransform.ToString(),
			*Layout.TargetTransform.ToString(),
			Layout.GateCenterY);

		for (const FSimTraceCourseElement& Element : Layout.Elements)
		{
			Canonical += FString::Printf(
				TEXT("|%s|%s|%.3f,%.3f,%.3f"),
				*Element.Name.ToString(),
				*Element.Transform.ToString(),
				Element.Dimensions.X,
				Element.Dimensions.Y,
				Element.Dimensions.Z);
		}

		return FString::Printf(TEXT("%08x"), FCrc::StrCrc32(*Canonical));
	}
}

FSimTraceCourseLayout FSimTraceCourseLayout::Generate(const int32 InSeed)
{
	FSimTraceCourseLayout Layout;
	Layout.Seed = InSeed;
	Layout.StartTransform = FTransform(FRotator::ZeroRotator, FVector(100.0, 0.0, 96.0));
	Layout.GoalTransform = FTransform(FRotator::ZeroRotator, FVector(3100.0, 0.0, 100.0));
	Layout.TargetTransform = FTransform(
		FRotator::ZeroRotator,
		FVector(2940.0, 0.0, 170.0));

	FRandomStream Random(InSeed);
	const double SlalomY1 = 160.0 + Random.RandRange(-30, 30);
	const double SlalomY2 = -160.0 + Random.RandRange(-30, 30);
	const double SlalomY3 = 160.0 + Random.RandRange(-30, 30);
	Layout.GateCenterY = Random.RandRange(-100, 100);

	Layout.Elements = {
		MakeElement(TEXT("floor"), FVector(1600.0, 0.0, -10.0), FVector(3400.0, 800.0, 20.0)),
		MakeElement(TEXT("wall_left"), FVector(1600.0, -410.0, 125.0), FVector(3400.0, 20.0, 250.0)),
		MakeElement(TEXT("wall_right"), FVector(1600.0, 410.0, 125.0), FVector(3400.0, 20.0, 250.0)),
		MakeElement(TEXT("slalom_1"), FVector(700.0, SlalomY1, 90.0), FVector(120.0, 120.0, 180.0)),
		MakeElement(TEXT("slalom_2"), FVector(1050.0, SlalomY2, 90.0), FVector(120.0, 120.0, 180.0)),
		MakeElement(TEXT("slalom_3"), FVector(1400.0, SlalomY3, 90.0), FVector(120.0, 120.0, 180.0)),
		MakeElement(TEXT("jump"), FVector(1800.0, 0.0, 30.0), FVector(40.0, 650.0, 60.0)),
		MakeElement(TEXT("gate"), FVector(2400.0, Layout.GateCenterY, 100.0), FVector(80.0, 800.0, 200.0)),
		MakeElement(
			TEXT("target_alpha"),
			Layout.TargetTransform.GetLocation(),
			FVector(20.0, 120.0, 120.0)),
		MakeElement(
			TEXT("dressing_cover_left"),
			FVector(430.0, -340.0, 55.0),
			FVector(220.0, 100.0, 110.0)),
		MakeElement(
			TEXT("dressing_cover_right"),
			FVector(1180.0, 340.0, 55.0),
			FVector(220.0, 100.0, 110.0)),
		MakeElement(
			TEXT("dressing_crates_left"),
			FVector(2080.0, -340.0, 45.0),
			FVector(150.0, 100.0, 90.0)),
		MakeElement(
			TEXT("dressing_crates_right"),
			FVector(2700.0, 340.0, 45.0),
			FVector(150.0, 100.0, 90.0))
	};

	Layout.Waypoints = {
		FVector(560.0, -FMath::Sign(SlalomY1) * 170.0, 96.0),
		FVector(840.0, -FMath::Sign(SlalomY1) * 170.0, 96.0),
		FVector(920.0, -FMath::Sign(SlalomY2) * 170.0, 96.0),
		FVector(1190.0, -FMath::Sign(SlalomY2) * 170.0, 96.0),
		FVector(1270.0, -FMath::Sign(SlalomY3) * 170.0, 96.0),
		FVector(1580.0, -FMath::Sign(SlalomY3) * 170.0, 96.0),
		FVector(1700.0, 0.0, 96.0),
		FVector(1980.0, 0.0, 96.0),
		FVector(2400.0, Layout.GateCenterY, 96.0),
		FVector(2750.0, 0.0, 96.0),
		FVector(3100.0, 0.0, 96.0)
	};

	Layout.CourseHash = BuildCourseHash(Layout);
	return Layout;
}
