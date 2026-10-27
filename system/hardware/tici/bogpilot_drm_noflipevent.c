// BogPilot: page flips without a DRM flip event, for the Qt UI on AGNOS 17.2 (LD_PRELOAD).
//
// Qt's eglfs_kms backend asks for DRM_MODE_PAGE_FLIP_EVENT on every frame. On the AGNOS 17.2
// kernel (msm SDE driver, command-mode DSI panel) completing those flip events from the panel's
// read-pointer interrupt crashes the device: an oops in drm_send_event_locked() called from
// sde_crtc_vblank_cb(), or a silent hard hang. The stock 17.2 UI (comma's raylib) never asks
// for a flip event: it flips with flags 0 and paces itself with drmWaitVBlank(). Do the same for
// Qt: strip the flip event, then request a one-shot vblank event for the next vblank and hand it
// to Qt's page-flip handler as if it were the flip completion. Vblank events carry no completion,
// so the crashing path is never used.
//
// Built on the device by bogpilot_qt_eglfs.sh; only the Qt UI on AGNOS with magic preloads it.
// Everything else (flags without the event, other DRM clients) passes straight through.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

typedef void (*flip_cb_t)(int, unsigned int, unsigned int, unsigned int, void *);

static int (*real_page_flip)(int, uint32_t, uint32_t, uint32_t, void *);
static int (*real_handle_event)(int, drmEventContextPtr);
static int (*real_wait_vblank)(int, drmVBlankPtr);
static drmModeResPtr (*real_get_resources)(int);
static void (*real_free_resources)(drmModeResPtr);
static flip_cb_t flip_cb;
static uint32_t cached_crtc, cached_type;

// The shim links libdrm, so RTLD_NEXT finds it even when Qt's KMS plugin loads libdrm with
// RTLD_LOCAL. If that ever fails, ask libdrm directly instead of calling through NULL.
static void *drm_sym(const char *name) {
  void *f = dlsym(RTLD_NEXT, name);
  if (!f) {
    void *h = dlopen("libdrm.so.2", RTLD_LAZY | RTLD_NOLOAD);
    if (!h) h = dlopen("libdrm.so.2", RTLD_LAZY);
    if (h) f = dlsym(h, name);
  }
  return f;
}

static void resolve(void) {
  if (!real_page_flip) real_page_flip = drm_sym("drmModePageFlip");
  if (!real_handle_event) real_handle_event = drm_sym("drmHandleEvent");
  if (!real_wait_vblank) real_wait_vblank = drm_sym("drmWaitVBlank");
  if (!real_get_resources) real_get_resources = drm_sym("drmModeGetResources");
  if (!real_free_resources) real_free_resources = drm_sym("drmModeFreeResources");
}

// vblank request type for the pipe that drives crtc_id
static uint32_t vblank_type(int fd, uint32_t crtc_id) {
  if (cached_type && crtc_id == cached_crtc) return cached_type;
  uint32_t t = DRM_VBLANK_RELATIVE;
  drmModeResPtr r = real_get_resources ? real_get_resources(fd) : NULL;
  if (r) {
    for (int i = 0; i < r->count_crtcs; i++) {
      if (r->crtcs[i] != crtc_id) continue;
      if (i == 1) t |= DRM_VBLANK_SECONDARY;
      else if (i > 1) t |= ((uint32_t)i << DRM_VBLANK_HIGH_CRTC_SHIFT) & DRM_VBLANK_HIGH_CRTC_MASK;
      break;
    }
    if (real_free_resources) real_free_resources(r);
  }
  cached_crtc = crtc_id;
  cached_type = t;
  return t;
}

static int next_vblank(int fd, uint32_t crtc_id, int send_event, void *data) {
  drmVBlank v;
  memset(&v, 0, sizeof(v));
  v.request.type = vblank_type(fd, crtc_id) | (send_event ? DRM_VBLANK_EVENT : 0);
  v.request.sequence = 1;
  v.request.signal = (unsigned long)data;
  return real_wait_vblank ? real_wait_vblank(fd, &v) : -1;
}

int drmModePageFlip(int fd, uint32_t crtc_id, uint32_t fb_id, uint32_t flags, void *user_data) {
  resolve();
  if (!real_page_flip) { errno = ENOSYS; return -ENOSYS; }
  if (!(flags & DRM_MODE_PAGE_FLIP_EVENT) || !real_wait_vblank)
    return real_page_flip(fd, crtc_id, fb_id, flags, user_data);

  int ret = -1;
  for (int tries = 0; tries < 4; tries++) {
    ret = real_page_flip(fd, crtc_id, fb_id, flags & ~DRM_MODE_PAGE_FLIP_EVENT, NULL);
    if (ret == 0 || errno != EBUSY) break;
    next_vblank(fd, crtc_id, 0, NULL);  // the previous flip has not latched yet
  }
  if (ret != 0) return ret;
  if (next_vblank(fd, crtc_id, 1, user_data) == 0) return 0;
  next_vblank(fd, crtc_id, 0, NULL);
  return next_vblank(fd, crtc_id, 1, user_data);
}

static void vblank_as_flip(int fd, unsigned int seq, unsigned int sec, unsigned int usec, void *data) {
  if (flip_cb) flip_cb(fd, seq, sec, usec, data);
}

int drmHandleEvent(int fd, drmEventContextPtr ctx) {
  resolve();
  if (!real_handle_event) { errno = ENOSYS; return -1; }
  if (!ctx || ctx->version < 2 || !ctx->page_flip_handler || ctx->vblank_handler)
    return real_handle_event(fd, ctx);
  drmEventContext c;
  memset(&c, 0, sizeof(c));
  c.version = 2;  // only the vblank and page-flip handlers are used
  c.page_flip_handler = ctx->page_flip_handler;
  c.vblank_handler = vblank_as_flip;
  flip_cb = ctx->page_flip_handler;
  return real_handle_event(fd, &c);
}
