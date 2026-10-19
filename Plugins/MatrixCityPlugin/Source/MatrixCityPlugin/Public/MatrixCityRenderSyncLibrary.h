#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MatrixCityRenderSyncLibrary.generated.h"

/** Explicit renderer synchronization for the Python dataset worker. */
UCLASS()
class MATRIXCITYPLUGIN_API UMatrixCityRenderSyncLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    /**
     * Drain rendering commands from the game thread. If requested, also submit
     * pending RHI work and wait for the GPU before returning. Does not run GC.
     * Returns false if called off the game thread or before RHI initialization.
     */
    UFUNCTION(BlueprintCallable, Category = "MatrixCity|Rendering")
    static bool SynchronizeRendering(bool bWaitForGPU = true);
};
