#pragma once

// Tesla car type selector and NotAutopilot (NAP) import status for the Vehicle settings panel.
//
// Everything here is a plain file in /data/params_bogpilot (see bogpilot_file_toggle.h for why
// BogPilot settings are not Params keys):
//   TeslaCarProfile     "auto" / "ap1" / "preap"; missing or unknown = auto. Written only by this UI
//                       (the owner); read by the Tesla car code, where it wins over auto-detect.
//   NAPMigrationStatus  JSON written by the NAP import, frogpilot/system/nap_migration.py (never a VIN).
//   TeslaProfileStatus  JSON written by the Tesla car code: profile, source, reason, pedal, radar.
// The NAP import idea and the Auto / Pre-AP / AP1 choice follow NotAutopilot's NAPCarType
// (github.com/NotAutopilot/openpilot, MIT).

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QStringList>

namespace bogpilot {

inline QString teslaFilePath(const QString &key) {
  return QStringLiteral("/data/params_bogpilot/") + key;
}

inline bool teslaFileExists(const QString &key) {
  return QFile::exists(teslaFilePath(key));
}

inline QString readFileText(const QString &key, qint64 limit = 4096) {
  QFile file(teslaFilePath(key));
  if (!file.open(QIODevice::ReadOnly)) {
    return QString();
  }
  return QString::fromUtf8(file.read(limit)).trimmed();
}

inline void writeFileText(const QString &key, const QByteArray &value) {
  QDir().mkpath(QStringLiteral("/data/params_bogpilot"));
  QFile file(teslaFilePath(key));
  if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    file.write(value);
  }
}

inline QJsonObject readFileJson(const QString &key) {
  QFile file(teslaFilePath(key));
  if (!file.open(QIODevice::ReadOnly)) {
    return QJsonObject();
  }
  QJsonDocument doc = QJsonDocument::fromJson(file.read(256 * 1024));
  return doc.isObject() ? doc.object() : QJsonObject();
}

// Button order in the selector: 0 Auto, 1 AP1, 2 Pre-AP.
inline int teslaCarTypeIndex() {
  const QString value = readFileText(QStringLiteral("TeslaCarProfile"), 64).toLower();
  if (value == "ap1") return 1;
  if (value == "preap" || value == "pre-ap") return 2;
  return 0;
}

inline QByteArray teslaCarTypeValue(int index) {
  if (index == 1) return "ap1";
  if (index == 2) return "preap";
  return "auto";
}

inline QString teslaProfileName(const QString &profile) {
  const QString p = profile.toLower();
  if (p == "ap1") return QObject::tr("AP1");
  if (p == "preap" || p == "pre-ap") return QObject::tr("Pre-AP");
  if (p.isEmpty() || p == "unknown") return QObject::tr("Not detected yet");
  return profile.left(24);
}

// Short value for a status row; the long text goes in the row's description.
inline QString shortStatus(const QString &text, int max = 36) {
  QString t = text.simplified();
  return t.length() > max ? t.left(max - 1) + QStringLiteral("…") : t;
}

struct TeslaSetupStatus {
  QString profile, profileDesc;
  QString reason, reasonDesc;
  QString nap, napDesc;
  QString pedal, pedalDesc;
  QString radar, radarDesc;
};

// fingerprintAP1 / hasPedal / hasRadar come from the last CarParams the settings window parsed.
inline TeslaSetupStatus readTeslaSetupStatus(bool fingerprintAP1, bool hasPedal, bool hasRadar) {
  TeslaSetupStatus s;
  const QJsonObject car = readFileJson(QStringLiteral("TeslaProfileStatus"));
  const int typeIndex = teslaCarTypeIndex();
  const QString chosen = typeIndex == 1 ? QObject::tr("AP1") : typeIndex == 2 ? QObject::tr("Pre-AP") : QObject::tr("Auto");

  // Detected profile and why
  if (!car.isEmpty()) {
    const QString source = car.value("source").toString();
    s.profile = teslaProfileName(car.value("profile").toString());
    if (source == "manual") s.profile += QObject::tr(" (manual)");
    else if (source == "auto") s.profile += QObject::tr(" (auto)");
    const QString reason = car.value("reason").toString();
    s.reason = reason.isEmpty() ? QObject::tr("No reason given") : shortStatus(reason);
    s.reasonDesc = reason;
  } else if (fingerprintAP1) {
    s.profile = QObject::tr("AP1");
    s.reason = QObject::tr("Saved car model");
    s.reasonDesc = QObject::tr("The last drive used the saved car model (Tesla AP1 Model S).");
  } else {
    s.profile = QObject::tr("Not detected yet");
    s.reason = QObject::tr("Turn the car on once");
    s.reasonDesc = QObject::tr("Turn the car on with the comma connected. This shows which car type BogPilot picked and why.");
  }
  s.profileDesc = QObject::tr("Car type setting: %1.").arg(chosen);

  // NotAutopilot import
  const QJsonObject nap = readFileJson(QStringLiteral("NAPMigrationStatus"));
  if (!nap.isEmpty()) {
    const int imported = nap.value("imported").toObject().size();
    const int kept = nap.value("kept").toArray().size();
    s.nap = imported > 0 ? QObject::tr("Imported (%1)").arg(imported) : QObject::tr("Found, nothing to import");
    QStringList lines;
    const QJsonObject importedObj = nap.value("imported").toObject();
    for (auto it = importedObj.begin(); it != importedObj.end(); ++it) {
      lines << QString("%1 = %2").arg(it.key(), it.value().toString());
    }
    QString desc = QObject::tr("<b>Settings carried over from NotAutopilot.</b> Your NAP settings were backed up to /data/params_bogpilot/nap_backup and left in place.");
    if (!lines.isEmpty()) desc += "<br><br>" + lines.join("<br>");
    if (kept > 0) desc += "<br><br>" + QObject::tr("%1 already set in BogPilot and kept as they were.").arg(kept);
    const QString napCarType = nap.value("nap_car_type").toString();
    if (!napCarType.isEmpty()) {
      desc += "<br><br>" + QObject::tr("NotAutopilot was set to %1. Car Type was left on its own setting; change it above if needed.").arg(napCarType == "auto" ? QObject::tr("Auto") : teslaProfileName(napCarType));
    }
    const QString epas = nap.value("epas_hint").toString();
    if (!epas.isEmpty()) desc += "<br><br>" + epas.toHtmlEscaped();
    s.napDesc = desc;
  } else if (teslaFileExists(QStringLiteral("NAPMigrationDone"))) {
    s.nap = QObject::tr("Imported");
    s.napDesc = QObject::tr("The NotAutopilot import already ran on this device.");
  } else {
    s.nap = QObject::tr("Not found");
    s.napDesc = QObject::tr("No NotAutopilot settings were found on this device. Nothing was imported. Settings are only found if you switched without a factory reset.");
  }

  // Pedal and radar
  auto hwStatus = [&](const QString &carKey, const QString &enabledKey, bool detected, QString &value, QString &desc) {
    const QString fromCar = car.value(carKey).toString();
    // "n/a" is what the car code writes for AP1 (no Pre-AP pedal/radar settings); fall through to detection.
    if (!fromCar.isEmpty() && fromCar != QStringLiteral("n/a")) {
      value = shortStatus(fromCar);
      desc = fromCar;
      return;
    }
    const QString enabled = readFileText(enabledKey, 64).toLower();
    const bool on = enabled == "1" || enabled == "true" || enabled == "on";
    if (detected) {
      value = QObject::tr("Detected");
      desc = QObject::tr("Seen on the last drive.");
    } else if (on) {
      value = QObject::tr("On, not seen yet");
      desc = QObject::tr("Turned on in settings (or imported from NotAutopilot) but not seen on the car yet. Turn the car on to check.");
    } else {
      value = QObject::tr("Off");
      desc = QObject::tr("Not turned on and not seen on the last drive.");
    }
  };
  hwStatus("pedal", "PreAPPedalEnabled", hasPedal, s.pedal, s.pedalDesc);
  hwStatus("radar", "PreAPRadarEnabled", hasRadar, s.radar, s.radarDesc);
  return s;
}

}  // namespace bogpilot
