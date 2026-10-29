#pragma once

// On/off switches stored as files in /data/params_bogpilot.
//
// The comma runs a prebuilt params_pyx.so that cannot store new Params keys (a new key in
// frogpilot_default_params stops boot), so BogPilot's own settings live in plain files that the
// car code reads directly. The read rules match the Python readers exactly
// (selfdrive/car/tesla/toggles.py enable_ic_integration, regen_brake.py
// regen_comfort_brake_enabled): text is trimmed, "1"/"true"/"on" is on, "0"/"false"/"off" is off,
// and a missing, empty or unknown value means the default. Writes are "1" or "0", like the
// Python setters.

#include <QDir>
#include <QFile>
#include <QString>

#include "selfdrive/ui/qt/widgets/controls.h"

namespace bogpilot {

inline QString fileTogglePath(const QString &key) {
  return QStringLiteral("/data/params_bogpilot/") + key;
}

inline bool readFileToggle(const QString &key, bool fallback) {
  QFile file(fileTogglePath(key));
  if (!file.open(QIODevice::ReadOnly)) {
    return fallback;
  }
  const QByteArray text = file.read(64).trimmed();
  if (text == "1" || text == "true" || text == "on") {
    return true;
  }
  if (text == "0" || text == "false" || text == "off") {
    return false;
  }
  return fallback;
}

inline void writeFileToggle(const QString &key, bool on) {
  QDir().mkpath(QStringLiteral("/data/params_bogpilot"));
  QFile file(fileTogglePath(key));
  if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    file.write(on ? "1" : "0");
  }
}

// A settings switch backed by one file. It re-reads the file each time it is shown, so a change
// made over SSH (echo 0 > /data/params_bogpilot/<key>) shows up the next time the panel opens.
class FileToggleControl : public ToggleControl {
public:
  FileToggleControl(const QString &key, bool fallback, const QString &title, const QString &desc, const QString &icon = "")
      : ToggleControl(title, desc, icon, readFileToggle(key, fallback)), key(key), fallback(fallback) {
    QObject::connect(this, &ToggleControl::toggleFlipped, [key](bool state) {
      writeFileToggle(key, state);
    });
  }

  void refresh() {
    if (readFileToggle(key, fallback) != toggle.on) {
      toggle.togglePosition();
    }
  }

protected:
  void showEvent(QShowEvent *event) override {
    ToggleControl::showEvent(event);
    refresh();
  }

private:
  const QString key;
  const bool fallback;
};

}  // namespace bogpilot
