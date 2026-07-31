#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SimTraceCourseLayout.h"
#include "SimTraceCourseActor.generated.h"

class UBoxComponent;
class UDirectionalLightComponent;
class UExponentialHeightFogComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class UPointLightComponent;
class USkyLightComponent;
class USkyAtmosphereComponent;
class UStaticMesh;
class UStaticMeshComponent;
class UPrimitiveComponent;

UCLASS()
class UNREALSIMTRACE_API ASimTraceCourseActor : public AActor
{
	GENERATED_BODY()

public:
	ASimTraceCourseActor();

	void SetCourseSeed(int32 InSeed);
	void ResetGoal();

	const FSimTraceCourseLayout& GetLayout() const { return Layout; }
	FVector GetGoalLocation() const { return Layout.GoalTransform.GetLocation(); }
	FVector GetRangeTargetLocation() const { return Layout.TargetTransform.GetLocation(); }
	bool WasGoalReached() const { return bGoalReached; }

protected:
	virtual void BeginPlay() override;
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

private:
	UPROPERTY(ReplicatedUsing=OnRep_CourseSeed)
	int32 CourseSeed = 1000;

	UPROPERTY()
	TObjectPtr<USceneComponent> SceneRoot;

	UPROPERTY()
	TObjectPtr<UBoxComponent> GoalTrigger;

	UPROPERTY()
	TObjectPtr<UDirectionalLightComponent> SunLight;

	UPROPERTY()
	TObjectPtr<USkyAtmosphereComponent> SkyAtmosphere;

	UPROPERTY()
	TObjectPtr<UPointLightComponent> FillLightNear;

	UPROPERTY()
	TObjectPtr<UPointLightComponent> FillLightMiddle;

	UPROPERTY()
	TObjectPtr<UPointLightComponent> FillLightFar;

	UPROPERTY()
	TObjectPtr<USkyLightComponent> SkyLight;

	UPROPERTY()
	TObjectPtr<UExponentialHeightFogComponent> HeightFog;

	UPROPERTY()
	TObjectPtr<UStaticMesh> CubeMesh;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> BaseShapeMaterial;

	UPROPERTY()
	TMap<FName, TObjectPtr<UStaticMeshComponent>> RuntimeMeshes;

	UPROPERTY()
	TMap<FName, TObjectPtr<UMaterialInstanceDynamic>> RuntimeMaterials;

	FSimTraceCourseLayout Layout;
	bool bGoalReached = false;

	UFUNCTION()
	void OnRep_CourseSeed();

	UFUNCTION()
	void OnGoalBeginOverlap(
		UPrimitiveComponent* OverlappedComponent,
		AActor* OtherActor,
		UPrimitiveComponent* OtherComponent,
		int32 OtherBodyIndex,
		bool bFromSweep,
		const FHitResult& SweepResult);

	void BuildCourse();
	UStaticMeshComponent* AddBox(
		FName Name,
		const FTransform& Transform,
		const FVector& Dimensions,
		bool bCollisionEnabled = true);
	void AddGate();
	void AddGoalVisuals();
	void AddRangeTarget(const FSimTraceCourseElement& Element);
	void ApplyPalette(FName Name, UStaticMeshComponent* Mesh);
	FLinearColor ColorForElement(FName Name) const;
};
