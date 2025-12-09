// Fill out your copyright notice in the Description page of Project Settings.

#pragma once

#include "MoviePipelineDeferredPasses.h"
#include "Misc/StringFormatArg.h"
#include "Runtime/Launch/Resources/Version.h"

// OpenEXR includes removed as they are not used directly and cause dependency issues in UE 5.2+
// The plugin uses ImageWriteQueue which handles EXR writing internally.

#include "ImageWriteTask.h"
#include "ImagePixelData.h"

#include "HAL/PlatformFileManager.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Async/Async.h"
#include "Misc/Paths.h"
#include "HAL/PlatformTime.h"
#include "Math/Float16.h"
#include "MovieRenderPipelineCoreModule.h"
#include "MoviePipelineOutputSetting.h"
#include "ImageWriteQueue.h"
#include "MoviePipeline.h"
#include "MoviePipelineImageQuantization.h"
#include "MoviePipelinePrimaryConfig.h"
// #include "IOpenExrRTTIModule.h" // Removed
#include "Modules/ModuleManager.h"
#include "MoviePipelineUtils.h"


#include "CoreMinimal.h"
#include "Async/Future.h"
// #include "MovieRenderPipelineRenderPasses/Public/MoviePipelineImageSequenceOutput.h"
// #include "MovieRenderPipelineRenderPasses/Private/MoviePipelineEXROutput.h"
#include "MoviePipelineImageSequenceOutput.h"
#include "CustomMoviePipelineOutput.generated.h"


UENUM(BlueprintType)
enum class ECustomImageFormat : uint8
{
	/** Portable Network Graphics. */
	PNG = 0,
	/** Joint Photographic Experts Group. */
	JPEG,
	/** Windows Bitmap. */
	BMP,
	/** OpenEXR (HDR) image file format. */
	EXR
};
	
USTRUCT(BlueprintType)
struct MATRIXCITYPLUGIN_API FCustomMoviePipelineRenderPass
{
	GENERATED_BODY()
	
public:
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render Pass")
	bool bEnabled = true;
	
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render Pass")
	FString RenderPassName;
	
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render Pass")
	TSoftObjectPtr<UMaterialInterface> Material;
	// UMaterialInterface* Material;
	
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render Pass")
	ECustomImageFormat Extension = ECustomImageFormat::PNG;
	
	FString SPassName;

};

/**
 * 
 */
UCLASS()
class MATRIXCITYPLUGIN_API UCustomMoviePipelineOutput : public UMoviePipelineImageSequenceOutputBase
{
	GENERATED_BODY()
public:
#if WITH_EDITOR
	virtual FText GetDisplayText() const override { return NSLOCTEXT("MovieRenderPipeline", "ImgSequenceEXTSettingDisplayName", ".ext(custom) Sequence [8/16bit]"); }
#endif
public:
	UCustomMoviePipelineOutput(): UMoviePipelineImageSequenceOutputBase()
	{
		OutputFormat = EImageFormat::PNG;
	}
	virtual void OnReceiveImageDataImpl(FMoviePipelineMergerOutputFrame* InMergedOutputFrame) override;
	
public:
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RenderPasses|RGB")
	bool bEnableRenderPass_RGB = true;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RenderPasses|RGB")
	FString RenderPassName_RGB;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RenderPasses|RGB")
	ECustomImageFormat Extension_RGB = ECustomImageFormat::PNG;
	
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RenderPasses|Additional")
	TArray<FCustomMoviePipelineRenderPass> AdditionalRenderPasses;
};
