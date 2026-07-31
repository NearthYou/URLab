#include "SimTraceCourseActor.h"

#include "Components/BoxComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
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

	SkyAtmosphere = CreateDefaultSubobject<USkyAtmosphereComponent>(TEXT("SkyAtmosphere"));
	SkyAtmosphere->SetupAttachment(SceneRoot);

	SunLight = CreateDefaultSubobject<UDirectionalLightComponent>(TEXT("SunLight"));
	SunLight->SetupAttachment(SceneRoot);
	SunLight->SetRelativeRotation(FRotator(-45.0, -35.0, 0.0));
	SunLight->SetIntensity(8.0f);
	SunLight->SetAtmosphereSunLight(true);
	SunLight->SetLightColor(FLinearColor(1.0f, 0.86f, 0.68f));

	HeightFog = CreateDefaultSubobject<UExponentialHeightFogComponent>(TEXT("HeightFog"));
	HeightFog->SetupAttachment(SceneRoot);
	HeightFog->SetFogDensity(0.008f);
	HeightFog->SetFogHeightFalloff(0.24f);
	HeightFog->SetFogInscatteringColor(FLinearColor(0.52f, 0.56f, 0.48f));

	FillLightNear = CreateDefaultSubobject<UPointLightComponent>(TEXT("FillLightNear"));
	FillLightNear->SetupAttachment(SceneRoot);
	FillLightNear->SetRelativeLocation(FVector(600.0, 0.0, 650.0));
	FillLightNear->SetIntensity(20000.0f);
	FillLightNear->SetAttenuationRadius(1800.0f);

	FillLightMiddle = CreateDefaultSubobject<UPointLightComponent>(TEXT("FillLightMiddle"));
	FillLightMiddle->SetupAttachment(SceneRoot);
	FillLightMiddle->SetRelativeLocation(FVector(1600.0, 0.0, 650.0));
	FillLightMiddle->SetIntensity(20000.0f);
	FillLightMiddle->SetAttenuationRadius(1800.0f);

	FillLightFar = CreateDefaultSubobject<UPointLightComponent>(TEXT("FillLightFar"));
	FillLightFar->SetupAttachment(SceneRoot);
	FillLightFar->SetRelativeLocation(FVector(2700.0, 0.0, 650.0));
	FillLightFar->SetIntensity(20000.0f);
	FillLightFar->SetAttenuationRadius(1800.0f);

	SkyLight = CreateDefaultSubobject<USkyLightComponent>(TEXT("SkyLight"));
	SkyLight->SetupAttachment(SceneRoot);
	SkyLight->SetMobility(EComponentMobility::Movable);
	SkyLight->SetIntensity(1.4f);
	SkyLight->SetRealTimeCapture(false);

	static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeFinder(TEXT("/Engine/BasicShapes/Cube.Cube"));
	CubeMesh = CubeFinder.Object;
	static ConstructorHelpers::FObjectFinder<UMaterialInterface> MaterialFinder(
		TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
	BaseShapeMaterial = MaterialFinder.Object;
}

void ASimTraceCourseActor::SetCourseSeed(const int32 InSeed)
{
	CourseSeed = InSeed;
	BuildCourse();
}

void ASimTraceCourseActor::BeginPlay()
{
	Super::BeginPlay();
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
	Layout = FSimTraceCourseLayout::Generate(CourseSeed);

	for (const FSimTraceCourseElement& Element : Layout.Elements)
	{
		if (Element.Name == TEXT("gate"))
		{
			AddGate();
			continue;
		}
		if (Element.Name == TEXT("target_alpha"))
		{
			AddRangeTarget(Element);
			continue;
		}
		if (Element.Name.ToString().StartsWith(TEXT("dressing_")))
		{
			AddBox(Element.Name, Element.Transform, Element.Dimensions, false);
			continue;
		}

		AddBox(Element.Name, Element.Transform, Element.Dimensions);
	}

	GoalTrigger->SetWorldLocation(Layout.GoalTransform.GetLocation());
	AddGoalVisuals();
	SkyLight->RecaptureSky();
	ResetGoal();
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

	UStaticMeshComponent* Mesh = RuntimeMeshes.FindRef(Name);
	if (!IsValid(Mesh))
	{
		Mesh = NewObject<UStaticMeshComponent>(this, Name);
		Mesh->SetNetAddressable();
		AddInstanceComponent(Mesh);
		Mesh->SetupAttachment(SceneRoot);
		Mesh->SetStaticMesh(CubeMesh);
		Mesh->SetMobility(EComponentMobility::Movable);
		Mesh->RegisterComponent();
		RuntimeMeshes.Add(Name, Mesh);
		ApplyPalette(Name, Mesh);
	}

	Mesh->SetWorldTransform(Transform);
	Mesh->SetWorldScale3D(Dimensions / 100.0);
	Mesh->SetCollisionEnabled(bCollisionEnabled ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
	Mesh->SetCollisionProfileName(bCollisionEnabled ? TEXT("BlockAll") : TEXT("NoCollision"));
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

void ASimTraceCourseActor::AddRangeTarget(
	const FSimTraceCourseElement& Element)
{
	UStaticMeshComponent* Target =
		AddBox(Element.Name, Element.Transform, Element.Dimensions, false);
	if (!Target)
	{
		return;
	}

	Target->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
	Target->SetCollisionResponseToAllChannels(ECR_Ignore);
	Target->SetCollisionResponseToChannel(ECC_Visibility, ECR_Block);
	Target->ComponentTags.AddUnique(TEXT("SimTraceTarget"));
}

void ASimTraceCourseActor::ApplyPalette(
	const FName Name,
	UStaticMeshComponent* Mesh)
{
	if (!BaseShapeMaterial || !Mesh)
	{
		return;
	}

	UMaterialInstanceDynamic* Material =
		UMaterialInstanceDynamic::Create(
			BaseShapeMaterial,
			this,
			*FString::Printf(TEXT("MID_%s"), *Name.ToString()));
	if (!Material)
	{
		return;
	}
	Material->SetVectorParameterValue(TEXT("Color"), ColorForElement(Name));
	Material->SetScalarParameterValue(TEXT("Roughness"), 0.82f);
	Mesh->SetMaterial(0, Material);
	RuntimeMaterials.Add(Name, Material);
}

FLinearColor ASimTraceCourseActor::ColorForElement(const FName Name) const
{
	const FString Value = Name.ToString();
	if (Value == TEXT("floor"))
	{
		return FLinearColor(0.16f, 0.19f, 0.12f);
	}
	if (Value.StartsWith(TEXT("wall_")))
	{
		return FLinearColor(0.27f, 0.29f, 0.27f);
	}
	if (Value.StartsWith(TEXT("slalom_")) ||
		Value.Contains(TEXT("crates")))
	{
		return FLinearColor(0.42f, 0.28f, 0.13f);
	}
	if (Value.Contains(TEXT("cover")))
	{
		return FLinearColor(0.22f, 0.31f, 0.19f);
	}
	if (Value.StartsWith(TEXT("gate_")) || Value == TEXT("jump"))
	{
		return FLinearColor(0.32f, 0.34f, 0.31f);
	}
	if (Value == TEXT("target_alpha"))
	{
		return FLinearColor(0.78f, 0.16f, 0.08f);
	}
	if (Value.StartsWith(TEXT("goal_")))
	{
		return FLinearColor(0.86f, 0.62f, 0.12f);
	}
	return FLinearColor(0.35f, 0.36f, 0.32f);
}
