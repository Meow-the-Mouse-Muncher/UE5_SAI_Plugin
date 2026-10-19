#include "MatrixCityRenderSyncLibrary.h"

#include "DynamicRHI.h"
#include "RHICommandList.h"
#include "RHIGlobals.h"
#include "RenderingThread.h"

bool UMatrixCityRenderSyncLibrary::SynchronizeRendering(bool bWaitForGPU)
{
    if (!IsInGameThread())
    {
        UE_LOG(LogTemp, Error, TEXT("MatrixCity render synchronization requires the game thread."));
        return false;
    }
    if (!GIsRHIInitialized || GDynamicRHI == nullptr)
    {
        UE_LOG(LogTemp, Error, TEXT("MatrixCity render synchronization requires an initialized RHI."));
        return false;
    }

    if (bWaitForGPU)
    {
        ENQUEUE_RENDER_COMMAND(MatrixCitySubmitAndWaitForGPU)(
            [](FRHICommandListImmediate& RHICmdList)
            {
                RHICmdList.SubmitAndBlockUntilGPUIdle();
            });
    }

    // Also waits for the enqueued GPU-idle command when bWaitForGPU is true.
    FlushRenderingCommands();
    return true;
}
