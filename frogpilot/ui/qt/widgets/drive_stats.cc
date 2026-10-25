#include "selfdrive/ui/qt/request_repeater.h"

#include "frogpilot/ui/qt/widgets/drive_stats.h"

#include <QDate>
#include <QDateTime>
#include <QFile>
#include <QTimeZone>

// BogPilot: per-day drive ledger written by frogpilot/system/frogpilot_tracking.py, keyed by the device's
// LOCAL calendar day. Both sides resolve the zone the same way (see bogpilot_timezone() there):
// Params "Timezone" (set by system/timed.py from GPS) -> /data/etc/timezone -> system zone -> UTC.
// Read explicitly each time because the UI starts before timed sets the zone and Qt caches the system zone.
static const char *DRIVE_HISTORY_PATH = "/data/params_bogpilot/DriveHistory";
static const char *TIMEZONE_NAME_PATH = "/data/etc/timezone";
static const int PAST_WEEK_DAYS = 7;  // today + 6 prior local days

static QTimeZone bogpilotTimeZone(const QString &paramName) {
  QStringList names = {paramName.trimmed()};
  QFile f(TIMEZONE_NAME_PATH);
  if (f.open(QIODevice::ReadOnly)) {
    names << QString::fromUtf8(f.readAll()).trimmed();
  }
  for (const QString &name : names) {
    if (!name.isEmpty()) {
      QTimeZone tz(name.toUtf8());
      if (tz.isValid()) return tz;
    }
  }
  QTimeZone sys = QTimeZone::systemTimeZone();
  return sys.isValid() ? sys : QTimeZone::utc();
}

static QDate localDate(const QDateTime &nowUtc, const QTimeZone &tz) {
  return nowUtc.toUTC().toTimeZone(tz).date();
}

// Sum the ledger days in [today - 6, today], comparing real dates (bad keys are skipped). Days "after" today
// (possible right after moving to a zone further west) are counted too, so a zone change never hides a drive.
static QJsonObject sumPastWeek(const QJsonObject &history, const QDate &today) {
  double routes = 0, meters = 0, seconds = 0;
  const QDate oldest = today.addDays(-(PAST_WEEK_DAYS - 1));
  for (auto it = history.begin(); it != history.end(); ++it) {
    QDate day = QDate::fromString(it.key(), "yyyy-MM-dd");  // strict, same as Python date.fromisoformat
    if (!day.isValid() || day < oldest || !it.value().isObject()) continue;
    QJsonObject d = it.value().toObject();
    routes += d.value("routes").toDouble();
    meters += d.value("meters").toDouble();
    seconds += d.value("seconds").toDouble();
  }
  return {{"routes", routes}, {"distance", meters * METER_TO_MILE}, {"minutes", seconds / 60.0}};
}

static QLabel *newLabel(const QString &text, const QString &type) {
  QLabel *label = new QLabel(text);
  label->setProperty("type", type);
  return label;
}

DriveStats::DriveStats(QWidget *parent) : QFrame(parent) {
  isMetric = params.getBool("IsMetric");
  konik = useKonikServer();

  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(50, 25, 50, 20);

  allTitle = addStatsLayouts(konik ? tr("ALL TIME (KONIK)") : tr("ALL TIME"), all);
  weekTitle = addStatsLayouts(konik ? tr("PAST WEEK (KONIK)") : tr("PAST WEEK"), week);
  addStatsLayouts(tr("BOGPILOT"), frogPilot, true);

  std::optional<QString> dongleId = getDongleId();
  if (dongleId.has_value()) {
    QString url = CommaApi::BASE_URL + "/v1.1/devices/" + dongleId.value() + "/stats";
    RequestRepeater *repeater = new RequestRepeater(this, url, "ApiCache_DriveStats", 30);
    QObject::connect(repeater, &RequestRepeater::requestDone, this, &DriveStats::parseResponse);
  }

  setStyleSheet(R"(
    DriveStats {
      background-color: #333333;
      border-radius: 10px;
    }

    QLabel[type="frogpilot_title"] { font-size: 50px; font-weight: 500; color: #D0D0D0; }
    QLabel[type="number"] { font-size: 65px; font-weight: 400; }
    QLabel[type="title"] { font-size: 50px; font-weight: 500; }
    QLabel[type="unit"] { font-size: 50px; font-weight: 300; color: #A0A0A0; }
  )");
}

void DriveStats::showEvent(QShowEvent *event) {
  isMetric = params.getBool("IsMetric");

  updateStats();
}

QLabel *DriveStats::addStatsLayouts(const QString &title, StatsLabels &labels, bool FrogPilot) {
  QGridLayout *grid_layout = new QGridLayout;
  grid_layout->setVerticalSpacing(10);
  grid_layout->setContentsMargins(0, 10, 0, 10);

  int row = 0;
  QLabel *title_label = newLabel(title, FrogPilot ? "frogpilot_title" : "title");
  grid_layout->addWidget(title_label, row++, 0, 1, 3);
  grid_layout->addItem(new QSpacerItem(0, 10), row++, 0, 1, 1);

  grid_layout->addWidget(labels.routes = newLabel("0", "number"), row, 0, Qt::AlignLeft);
  grid_layout->addWidget(labels.distance = newLabel("0", "number"), row, 1, Qt::AlignLeft);
  grid_layout->addWidget(labels.hours = newLabel("0", "number"), row, 2, Qt::AlignLeft);

  grid_layout->addWidget(newLabel(tr("Drives"), "unit"), row + 1, 0, Qt::AlignLeft);
  grid_layout->addWidget(labels.distance_unit = newLabel(isMetric ? tr("KM") : tr("Miles"), "unit"), row + 1, 1, Qt::AlignLeft);
  grid_layout->addWidget(newLabel(tr("Hours"), "unit"), row + 1, 2, Qt::AlignLeft);

  QVBoxLayout *main_layout = static_cast<QVBoxLayout *>(layout());
  main_layout->addLayout(grid_layout);
  main_layout->addStretch(1);
  return title_label;
}

void DriveStats::parseResponse(const QString &response, bool success) {
  if (!success) {
    return;
  }

  QJsonDocument doc = QJsonDocument::fromJson(response.trimmed().toUtf8());
  if (doc.isNull()) {
    qDebug() << "JSON Parse failed on getting past drives statistics";
    return;
  }
  stats = doc;
  // comma/Konik only count routes that reached their servers, so a device with uploads off (or never
  // paired) gets a valid reply full of zeros. Treat that like "no API data" and use the local numbers.
  QJsonObject all_obj = stats.object()["all"].toObject();
  apiHasData = all_obj["routes"].toDouble() > 0 || all_obj["minutes"].toDouble() > 0;
  updateStats();
}

void DriveStats::updateStatsForLabel(const QJsonObject &obj, StatsLabels &labels) {
  labels.distance->setText(QString::number(int(obj["distance"].toDouble() * (isMetric ? MILE_TO_KM : 1))));
  labels.distance_unit->setText(isMetric ? tr("KM") : tr("Miles"));
  labels.hours->setText(QString::number((int)(obj["minutes"].toDouble() / 60)));
  labels.routes->setText(QString::number((int)obj["routes"].toDouble()));
}

void DriveStats::updateFrogPilotStatsForLabel(StatsLabels &labels) {
  QJsonObject frogpilot_stats = QJsonDocument::fromJson(QByteArray::fromStdString(params.get("FrogPilotStats"))).object();

  labels.distance->setText(QString::number(int(frogpilot_stats.value("FrogPilotMeters").toDouble() * (isMetric ? 0.001 : METER_TO_MILE))));
  labels.distance_unit->setText(isMetric ? tr("KM") : tr("Miles"));
  labels.hours->setText(QString::number(int(frogpilot_stats.value("FrogPilotSeconds").toDouble() / (60 * 60))));
  labels.routes->setText(QString::number(frogpilot_stats.value("FrogPilotDrives").toInt()));
}

QJsonObject DriveStats::localAllTime() {
  // Same source as the BOGPILOT row (FrogPilotStats), in the API's {routes, distance (miles), minutes} shape.
  QJsonObject fp = QJsonDocument::fromJson(QByteArray::fromStdString(params.get("FrogPilotStats"))).object();
  return {
    {"routes", fp.value("FrogPilotDrives").toDouble()},
    {"distance", fp.value("FrogPilotMeters").toDouble() * METER_TO_MILE},
    {"minutes", fp.value("FrogPilotSeconds").toDouble() / 60.0},
  };
}

QJsonObject DriveStats::localPastWeek() {
  QJsonObject history;
  QFile f(DRIVE_HISTORY_PATH);
  if (f.open(QIODevice::ReadOnly)) {
    history = QJsonDocument::fromJson(f.readAll()).object();
  }
  QTimeZone tz = bogpilotTimeZone(QString::fromStdString(params.get("Timezone")));
  return sumPastWeek(history, localDate(QDateTime::currentDateTimeUtc(), tz));
}

void DriveStats::updateStats() {
  QJsonObject json = stats.object();

  if (apiHasData) {
    updateStatsForLabel(json["all"].toObject(), all);
    updateStatsForLabel(json["week"].toObject(), week);
    // Only store real server minutes; never overwrite them with 0 when the API is unavailable.
    params.putIntNonBlocking(konik ? "KonikMinutes" : "openpilotMinutes", json["all"].toObject()["minutes"].toDouble());
  } else {
    updateStatsForLabel(localAllTime(), all);
    updateStatsForLabel(localPastWeek(), week);
  }
  allTitle->setText(apiHasData ? (konik ? tr("ALL TIME (KONIK)") : tr("ALL TIME")) : tr("ALL TIME (ON DEVICE)"));
  weekTitle->setText(apiHasData ? (konik ? tr("PAST WEEK (KONIK)") : tr("PAST WEEK")) : tr("PAST WEEK (ON DEVICE)"));
  updateFrogPilotStatsForLabel(frogPilot);
}
