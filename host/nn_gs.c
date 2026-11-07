/* nn::gs::sdk: the Switch 2 graphics system the game boots before NVN (native memory, native windows and their
   buffers, presentation). On hardware it talks to the graphics server (nn::gsv over nn::am's graphics supervisor
   handle); natively it is the boundary the NVN2 layer will take over. For now: booted as a no-op, so the rest of
   the game's start can run headless. */
#include "hos.h"
#include <string.h>

/* nn::gs::sdk::BootGraphicsSystem(void* memory, unsigned long size): the graphics stack's working memory */
HLE(_ZN2nn2gs3sdk18BootGraphicsSystemEPvm) {
    hos_log("gs: BootGraphicsSystem(%#llx, %#llx): no graphics server yet (headless)", (unsigned long long)ARG(0),
            (unsigned long long)ARG(1));
    RET(0);
}

/* nn::gs::sdk::CheckGraphicsSystemInitialized(char const* who, unsigned long length) */
HLE(_ZN2nn2gs3sdk30CheckGraphicsSystemInitializedEPKcm) { RET(1); }

/* nv::GetGraphicsMemoryInfo(nv::MemoryInfo*): the GPU driver's memory statistics, from the nvdrv service the native
   build does not have (its lock is never set up). Zeros, as the SDK reports with no driver attached. */
HLE(_ZN2nv21GetGraphicsMemoryInfoEPNS_10MemoryInfoE) { memset(GP(ARG(0)), 0, 0x40); }

/* Native syncs (GPU fences between the graphics server, the video decoders and the game): the native NVN layer and
   nn_video.c complete their work in order, so every one is reached. MKW's movie library asserts TryWaitNativeSync
   on each decoded frame before it takes it. */
HLE(_ZN2nn2gs14WaitNativeSyncERKNS0_16NativeSyncHandleE) { RET(0); }
HLE(_ZN2nn2gs17TryWaitNativeSyncERKNS0_16NativeSyncHandleE) {
    if (ARG(0) && G64(ARG(0) + 8) == 0xdeadbeefULL && G64(ARG(0)) && *(volatile int *)(uintptr_t)G64(ARG(0))) {
        RET(0);
        return;
    }
    RET(1);
}
HLE(_ZN2nn2gs19TimedWaitNativeSyncERKNS0_16NativeSyncHandleENS_8TimeSpanE) { RET(1); }
