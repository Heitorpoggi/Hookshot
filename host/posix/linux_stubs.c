/* (linux) What the Windows build has and Linux does without:

   - Media Foundation's AV1 decoder (host/mf_av1.c): FFmpeg decodes AV1 itself here, with dav1d (host/nn_video.c asks
     FFmpeg first), so these are never reached with a dav1d build; without one, AV1 movies are skipped.
   - The WebGPU renderer (gpu/render, wgr_*): the Vulkan back end is the only renderer on Linux. Should it not start,
     these leave the window black, as a WebGPU renderer without a device does. */
#include "../hos.h"
#include "../../gpu/vk/vk_api.h"

#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <dav1d/dav1d.h>

typedef void (*mf_av1_picture_fn)(void *user, const uint8_t *y, const uint8_t *uv, int w, int h, int stride, int64_t pts);

typedef struct {
    void (*default_settings)(Dav1dSettings *);
    int (*open)(Dav1dContext **, const Dav1dSettings *);
    uint8_t *(*data_create)(Dav1dData *, size_t);
    void (*data_unref)(Dav1dData *);
    int (*send_data)(Dav1dContext *, Dav1dData *);
    int (*get_picture)(Dav1dContext *, Dav1dPicture *);
    void (*picture_unref)(Dav1dPicture *);
    void (*flush)(Dav1dContext *);
    void (*close)(Dav1dContext **);
} Dav1dApi;

static Dav1dApi d1d;
static int d1d_loaded;

static int load_dav1d(void) {
    if (d1d_loaded) return d1d.open != NULL;
    d1d_loaded = 1;
    void *lib = dlopen("libdav1d.so.7", RTLD_LAZY);
    if (!lib) lib = dlopen("libdav1d.so", RTLD_LAZY);
    if (!lib) {
        hos_log("video: libdav1d.so not found; AV1 movies will be skipped");
        return 0;
    }
    d1d.default_settings = (void (*)(Dav1dSettings *))dlsym(lib, "dav1d_default_settings");
    d1d.open = (int (*)(Dav1dContext **, const Dav1dSettings *))dlsym(lib, "dav1d_open");
    d1d.data_create = (uint8_t *(*)(Dav1dData *, size_t))dlsym(lib, "dav1d_data_create");
    d1d.data_unref = (void (*)(Dav1dData *))dlsym(lib, "dav1d_data_unref");
    d1d.send_data = (int (*)(Dav1dContext *, Dav1dData *))dlsym(lib, "dav1d_send_data");
    d1d.get_picture = (int (*)(Dav1dContext *, Dav1dPicture *))dlsym(lib, "dav1d_get_picture");
    d1d.picture_unref = (void (*)(Dav1dPicture *))dlsym(lib, "dav1d_picture_unref");
    d1d.flush = (void (*)(Dav1dContext *))dlsym(lib, "dav1d_flush");
    d1d.close = (void (*)(Dav1dContext **))dlsym(lib, "dav1d_close");
    if (!d1d.default_settings || !d1d.open || !d1d.data_create || !d1d.send_data || !d1d.get_picture || !d1d.picture_unref || !d1d.flush || !d1d.close) {
        memset(&d1d, 0, sizeof d1d);
        return 0;
    }
    return 1;
}

typedef struct {
    Dav1dContext *ctx;
    int want_w, want_h;
    uint8_t *y_buf, *uv_buf;
    size_t buf_w, buf_h;
} LinuxAv1;

void *mf_av1_open(int w, int h) {
    if (!load_dav1d()) return NULL;
    Dav1dSettings s;
    d1d.default_settings(&s);
    s.n_threads = 4;
    s.max_frame_delay = 1;
    Dav1dContext *ctx = NULL;
    if (d1d.open(&ctx, &s) < 0 || !ctx) return NULL;
    LinuxAv1 *m = (LinuxAv1 *)calloc(1, sizeof *m);
    m->ctx = ctx;
    m->want_w = w;
    m->want_h = h;
    hos_log("video: AV1 decoder ready on libdav1d (%dx%d)", w, h);
    return m;
}

static void pull_dav1d(LinuxAv1 *m, mf_av1_picture_fn fn, void *user) {
    for (;;) {
        Dav1dPicture pic = {0};
        int res = d1d.get_picture(m->ctx, &pic);
        if (res < 0) break;
        const int w = m->want_w > 0 && m->want_w < pic.p.w ? m->want_w : pic.p.w;
        const int h = m->want_h > 0 && m->want_h < pic.p.h ? m->want_h : pic.p.h;
        if (w > 0 && h > 0 && pic.p.bpc == 8 && pic.data[0]) {
            if ((size_t)w != m->buf_w || (size_t)h != m->buf_h) {
                free(m->y_buf);
                free(m->uv_buf);
                m->y_buf = (uint8_t *)malloc((size_t)w * h);
                m->uv_buf = (uint8_t *)calloc(1, (size_t)w * (h / 2));
                m->buf_w = (size_t)w;
                m->buf_h = (size_t)h;
            }
            const uint8_t *ysrc = (const uint8_t *)pic.data[0];
            const ptrdiff_t ystride = pic.stride[0];
            for (int r = 0; r < h; r++)
                memcpy(m->y_buf + (size_t)r * w, ysrc + (ptrdiff_t)r * ystride, (size_t)w);
            if (pic.data[1] && pic.data[2] && pic.p.layout == DAV1D_PIXEL_LAYOUT_I420) {
                const uint8_t *usrc = (const uint8_t *)pic.data[1];
                const uint8_t *vsrc = (const uint8_t *)pic.data[2];
                const ptrdiff_t uvstride = pic.stride[1];
                const int cw = w / 2, ch = h / 2;
                for (int r = 0; r < ch; r++) {
                    uint8_t *dst = m->uv_buf + (size_t)r * w;
                    const uint8_t *u = usrc + (ptrdiff_t)r * uvstride;
                    const uint8_t *v = vsrc + (ptrdiff_t)r * uvstride;
                    for (int c = 0; c < cw; c++) {
                        dst[2 * c] = u[c];
                        dst[2 * c + 1] = v[c];
                    }
                }
            }
            fn(user, m->y_buf, m->uv_buf, w, h, w, pic.m.timestamp);
        }
        d1d.picture_unref(&pic);
    }
}

void mf_av1_decode(void *h, const uint8_t *data, size_t n, int64_t pts, mf_av1_picture_fn fn, void *user) {
    LinuxAv1 *m = (LinuxAv1 *)h;
    if (!m || !m->ctx) return;
    if (!data || !n) {
        pull_dav1d(m, fn, user);
        return;
    }
    Dav1dData d = {0};
    uint8_t *buf = d1d.data_create(&d, n);
    if (!buf) return;
    memcpy(buf, data, n);
    d.m.timestamp = pts;
    for (int tries = 0; d.sz > 0 && tries < 16; tries++) {
        int res = d1d.send_data(m->ctx, &d);
        pull_dav1d(m, fn, user);
        if (res < 0 && res != DAV1D_ERR(EAGAIN)) {
            d1d.data_unref(&d);
            break;
        }
    }
    if (d.sz > 0) d1d.data_unref(&d);
}

void mf_av1_flush(void *h) {
    LinuxAv1 *m = (LinuxAv1 *)h;
    if (m && m->ctx) d1d.flush(m->ctx);
}

void mf_av1_close(void *h) {
    LinuxAv1 *m = (LinuxAv1 *)h;
    if (!m) return;
    if (m->ctx) d1d.close(&m->ctx);
    free(m->y_buf);
    free(m->uv_buf);
    free(m);
}

int wgr_start(void *hwnd) { (void)hwnd; hos_log("rx: no WebGPU renderer in the Linux build: nothing is drawn"); return 0; }
void wgr_resize(int width, int height) { (void)width, (void)height; }
const char *wgr_describe(void) { return "none"; }
void wgr_stats(int64_t *draws, int64_t *skipped, int64_t *pipelines) { *draws = *skipped = *pipelines = 0; }
void wgr_nvn_submit(const uint64_t *handles, int n) { (void)handles, (void)n; }
uint64_t wgr_nvn_fence(void) { return 0; }
uint64_t wgr_nvn_pushed(void) { return 0; }
uint64_t wgr_nvn_replayed(void) { return 0; }
int wgr_nvn_wait(uint64_t fence, uint64_t timeout_ns) { (void)fence, (void)timeout_ns; return 1; }
void wgr_wait_writebacks_reading(const void *p, uint64_t n) { (void)p, (void)n; }
void wgr_nvn_finish(void) {}
void wgr_nvn_present(const nvn_tex *t, const int crop[4]) { (void)t, (void)crop; }
void wgr_nvn_register_texture(int id, const nvn_tex *t, const nvn_view *v) { (void)id, (void)t, (void)v; }
void wgr_nvn_register_sampler(int id, const nvn_samp_par *p) { (void)id, (void)p; }
void wgr_nvn_set_texture_pool_size(int n) { (void)n; }
void wgr_nvn_texture_released(const nvn_tex *t) { (void)t; }
void wgr_nvn_program_released(const nvn_prog *p) { (void)p; }
void wgr_nvn_read_texels(const nvn_tex *t, unsigned level, unsigned layer0, const NVNcopyRegion *r, uint8_t *dst,
                         uint64_t row, uint64_t img) {
    (void)t, (void)level, (void)layer0, (void)r, (void)dst, (void)row, (void)img;
}
void wgr_nvn_write_texels(const nvn_tex *t, unsigned level, unsigned layer0, const NVNcopyRegion *r, const uint8_t *src,
                          uint64_t row, uint64_t img) {
    (void)t, (void)level, (void)layer0, (void)r, (void)src, (void)row, (void)img;
}
void wgr_nvn_invalidate(uint64_t gpu, uint64_t size) { (void)gpu, (void)size; }
/* (gpu/render/rx_core.cpp's, for the soft write watch's other consumer) */
uint32_t *rx_collect_written(int64_t *count) { *count = -1; return NULL; }
/* (gpu/render's debugging commands: host/nn_hid.c's live input "!dump", "!shots", "!rdoc", "!flasharm") */
void rx_request_frame_dump(void) { hos_log("rx: frame dumps are the WebGPU renderer's: not in the Linux build"); }
void rx_set_screenshot_ms(int64_t ms) { (void)ms; hos_log("rx: !shots is the WebGPU renderer's: RX_VK_SHOTS instead"); }
void rx_request_rdoc_capture(int frames) { (void)frames; }
void rx_flash_dark_arm(void) {}
