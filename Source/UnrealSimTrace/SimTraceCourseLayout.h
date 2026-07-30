#pragma once

#include "CoreMinimal.h"

struct UNREALSIMTRACE_API FSimTraceCourseElement
{
	FName Name;
	FTransform Transform = FTransform::Identity;
	FVector Dimensions = FVector::OneVector;
};

struct UNREALSIMTRACE_API FSimTraceCourseLayout
{
	int32 Seed = 0;
	FTransform StartTransform = FTransform::Identity;
	FTransform GoalTransform = FTransform::Identity;
	TArray<FSimTraceCourseElement> Elements;
	TArray<FVector> Waypoints;
	FString CourseHash;
	double GateCenterY = 0.0;

	static FSimTraceCourseLayout Generate(int32 InSeed);
};

