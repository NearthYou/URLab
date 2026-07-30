#include "SimTraceCourseActor.h"

#include "Components/BoxComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Character.h"
#include "Net/UnrealNetwork.h"
#include "UObject/ConstructorHelpers.h"

ASimTraceCourseActor::ASimTraceCourseActor()
{
	bReplicates = true;
	bAlwaysRelevant = true;
	SetReplicateMovement(false);

	SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
	SetRootComponent(SceneRoot);

	GoalTrigger = CreateDefaultSubobject<UBoxComponent>(TEXT("GoalTrigger"));
	GoalTrigger->SetupAttachment(SceneRoot);
	GoalTrigger->SetBoxExtent(FVector(100.0, 300.0, 125.0));
	GoalTrigger->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
	GoalTrigger->SetCollisionResponseToAllChannels(ECR_Ignore);
	GoalTrigger->SetCollisionResponseToChannel(ECC_Pawn, ECR_Overlap);
	GoalTrigger->OnComponentBeginOverlap.AddDynamic(this, &ASimTraceCourseActor::OnGoalBeginOverlap);

	static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeFinder(TEXT("/Engine/BasicShapes/Cube.Cube"));
	CubeMesh = CubeFinder.Object;
}

void ASimTraceCourseActor::SetCourseSeed(const int32 InSeed)
{
	CourseSeed = InSeed;
	BuildCourse();
}

void ASimTraceCourseActor::ResetGoal()
{
	bGoalReached = false;
}

void ASimTraceCourseActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);
	DOREPLIFETIME(ASimTraceCourseActor, CourseSeed);
}

void ASimTraceCourseActor::OnRep_CourseSeed()
{
	BuildCourse();
}

void ASimTraceCourseActor::OnGoalBeginOverlap(
	UPrimitiveComponent* OverlappedComponent,
	AActor* OtherActor,
	UPrimitiveComponent* OtherComponent,
	int32 OtherBodyIndex,
	bool bFromSweep,
	const FHitResult& SweepResult)
{
	if (Cast<ACharacter>(OtherActor))
	{
		bGoalReached = true;
	}
}

void ASimTraceCourseActor::BuildCourse()
{
	ClearCourse();
	Layout = FSimTraceCourseLayout::Generate(CourseSeed);

	for (const FSimTraceCourseElement& Element : Layout.Elements)
	{
		if (Element.Name == TEXT("gate"))
		{
			AddGate();
			continue;
		}

		AddBox(Element.Name, Element.Transform, Element.Dimensions);
	}

	GoalTrigger->SetWorldLocation(Layout.GoalTransform.GetLocation());
	AddGoalVisuals();
	ResetGoal();
}

void ASimTraceCourseActor::ClearCourse()
{
	for (UStaticMeshComponent* Mesh : RuntimeMeshes)
	{
		if (IsValid(Mesh))
		{
			Mesh->DestroyComponent();
		}
	}
	RuntimeMeshes.Reset();
}

UStaticMeshComponent* ASimTraceCourseActor::AddBox(
	const FName Name,
	const FTransform& Transform,
	const FVector& Dimensions,
	const bool bCollisionEnabled)
{
	if (!CubeMesh)
	{
		return nullptr;
	}

	const FName UniqueName = MakeUniqueObjectName(this, UStaticMeshComponent::StaticClass(), Name);
	UStaticMeshComponent* Mesh = NewObject<UStaticMeshComponent>(this, UniqueName);
	AddInstanceComponent(Mesh);
	Mesh->SetupAttachment(SceneRoot);
	Mesh->SetStaticMesh(CubeMesh);
	Mesh->SetMobility(EComponentMobility::Static);
	Mesh->SetWorldTransform(Transform);
	Mesh->SetWorldScale3D(Dimensions / 100.0);
	Mesh->SetCollisionEnabled(bCollisionEnabled ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
	Mesh->SetCollisionProfileName(bCollisionEnabled ? TEXT("BlockAll") : TEXT("NoCollision"));
	Mesh->RegisterComponent();
	RuntimeMeshes.Add(Mesh);
	return Mesh;
}

void ASimTraceCourseActor::AddGate()
{
	constexpr double CorridorMinY = -400.0;
	constexpr double CorridorMaxY = 400.0;
	constexpr double GapHalfWidth = 120.0;
	const double LowerEnd = Layout.GateCenterY - GapHalfWidth;
	const double UpperStart = Layout.GateCenterY + GapHalfWidth;

	const double LowerWidth = LowerEnd - CorridorMinY;
	const double UpperWidth = CorridorMaxY - UpperStart;
	AddBox(
		TEXT("gate_lower"),
		FTransform(FRotator::ZeroRotator, FVector(2400.0, CorridorMinY + LowerWidth * 0.5, 100.0)),
		FVector(80.0, LowerWidth, 200.0));
	AddBox(
		TEXT("gate_upper"),
		FTransform(FRotator::ZeroRotator, FVector(2400.0, UpperStart + UpperWidth * 0.5, 100.0)),
		FVector(80.0, UpperWidth, 200.0));
}

void ASimTraceCourseActor::AddGoalVisuals()
{
	AddBox(
		TEXT("goal_left"),
		FTransform(FRotator::ZeroRotator, FVector(3100.0, -300.0, 125.0)),
		FVector(40.0, 40.0, 250.0),
		false);
	AddBox(
		TEXT("goal_right"),
		FTransform(FRotator::ZeroRotator, FVector(3100.0, 300.0, 125.0)),
		FVector(40.0, 40.0, 250.0),
		false);
	AddBox(
		TEXT("goal_top"),
		FTransform(FRotator::ZeroRotator, FVector(3100.0, 0.0, 250.0)),
		FVector(40.0, 640.0, 40.0),
		false);
}

