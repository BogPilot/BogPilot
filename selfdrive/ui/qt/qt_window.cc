#include "selfdrive/ui/qt/qt_window.h"

void setMainWindow(QWidget *w) {
  const float scale = util::getenv("SCALE", 1.0f);
  const QSize sz = QGuiApplication::primaryScreen()->size();

  if (Hardware::PC() && scale == 1.0 && !(sz - DEVICE_SCREEN_SIZE).isValid()) {
    w->setMinimumSize(QSize(640, 480)); // allow resize smaller than fullscreen
    w->setMaximumSize(DEVICE_SCREEN_SIZE);
    w->resize(sz);
  } else {
    w->setFixedSize(DEVICE_SCREEN_SIZE * scale);
  }
  w->show();

#ifdef QCOM2
  QPlatformNativeInterface *native = QGuiApplication::platformNativeInterface();
  // BogPilot: on AGNOS 17.2 the UI runs on eglfs (no weston), which has no wl_surface;
  // the rotation is done by QT_QPA_EGLFS_ROTATION there.
  if (QGuiApplication::platformName().startsWith("wayland")) {
    wl_surface *s = reinterpret_cast<wl_surface*>(native->nativeResourceForWindow("surface", w->windowHandle()));
    if (s != nullptr) {
      wl_surface_set_buffer_transform(s, WL_OUTPUT_TRANSFORM_270);
      wl_surface_commit(s);
    }
  }

  w->setWindowState(Qt::WindowFullScreen);
  w->setVisible(true);

  // ensure we have a valid eglDisplay, otherwise the ui will silently fail
  void *egl = native->nativeResourceForWindow("egldisplay", w->windowHandle());
  assert(egl != nullptr);
#endif
}


extern "C" {
  void set_main_window(void *w) {
    setMainWindow((QWidget*)w);
  }
}
