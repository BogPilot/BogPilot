#include "frogpilot/ui/qt/offroad/offroad_screensaver.h"

#include <QDateTime>
#include <QFont>
#include <QFontMetrics>
#include <QHideEvent>
#include <QPainter>
#include <QPaintEvent>
#include <QResizeEvent>
#include <QShowEvent>
#include <QTimer>

#include "frogpilot/ui/qt/widgets/bogpilot_file_toggle.h"
#include "selfdrive/ui/ui.h"

namespace {

constexpr int kLabelPx = 40;
constexpr int kValuePx = 88;
constexpr int kCaptionPx = 72;
constexpr int kGap = 10;
constexpr int kBlockGap = 28;
constexpr float kSpeedX = 260.f;
constexpr float kSpeedY = 150.f;

QFont interFont(int px, QFont::Weight weight) {
  QFont font(QStringLiteral("Inter"));
  font.setPixelSize(px);
  font.setWeight(weight);
  return font;
}

// Same "Up to date" condition as the offroad upload card (wifi.cc refreshRealdata):
// a fresh, non-stale sample whose PENDING backlog is 0.
bool cardSaysUpToDate(const bogpilot_uprate::Sample &sample, qint64 age) {
  const bool stale = age > bogpilot_uprate::STALE_S;
  return bogpilot_uprate::fresh(sample, age) && !stale &&
         bogpilot_uprate::backlogState(sample) == bogpilot_uprate::Backlog::CaughtUp;
}

bool enabled() {
  return bogpilot::readFileToggle(QString::fromLatin1(bogpilot_screensaver::TOGGLE_KEY), false);
}

}  // namespace

OffroadScreensaver::OffroadScreensaver(QWidget *parent) : QWidget(parent) {
  setAttribute(Qt::WA_AcceptTouchEvents);
  setAttribute(Qt::WA_OpaquePaintEvent);
  logo = QPixmap(QStringLiteral("../../frogpilot/assets/bogpilot_theme/logo.png"));

  sampleTimer = new QTimer(this);
  QObject::connect(sampleTimer, &QTimer::timeout, this, [this]() { refreshSample(); });
  animTimer = new QTimer(this);
  QObject::connect(animTimer, &QTimer::timeout, this, [this]() { tick(); });
}

bool OffroadScreensaver::onInteractiveTimeout() {
  return !uiState()->scene.started && enabled();
}

void OffroadScreensaver::dismiss() {
  emit dismissed();
}

void OffroadScreensaver::showEvent(QShowEvent *event) {
  QWidget::showEvent(event);
  x = qMax(0.f, width() * 0.18f);
  y = qMax(0.f, height() * 0.12f);
  vx = kSpeedX;
  vy = kSpeedY;
  toggleTicks = 1;  // the timeout slot just read the toggle
  refreshSample();
  relayout();
  clock.start();
  sampleTimer->start(bogpilot_uprate::READ_INTERVAL_MS);
  animTimer->start(33);
  // Runs inside the interactiveTimeout signal, before Device::setAwake.
  device()->setDisplayHold(true);
  update();
}

void OffroadScreensaver::hideEvent(QHideEvent *event) {
  if (sampleTimer) {
    sampleTimer->stop();
  }
  if (animTimer) {
    animTimer->stop();
  }
  device()->setDisplayHold(false);
  QWidget::hideEvent(event);
}

void OffroadScreensaver::resizeEvent(QResizeEvent *event) {
  QWidget::resizeEvent(event);
  if (isVisible()) {
    relayout();
  }
}

bool OffroadScreensaver::refreshSample() {
  if (toggleTicks-- <= 0) {
    toggleTicks = 1;
    if (!enabled()) {
      dismiss();
      return false;
    }
  }

  sample = bogpilot_uprate::readFile();
  ageS = QDateTime::currentSecsSinceEpoch() - sample.epoch;
  const bool shown = bogpilot_uprate::fresh(sample, ageS);
  const bool nextStale = shown && ageS > bogpilot_uprate::STALE_S;
  const bool nextCaught = cardSaysUpToDate(sample, ageS);
  const bool nextPending = shown && sample.has_pending && !nextCaught;
  const bool layoutChanged = nextCaught != caughtUp || nextPending != showPending || nextStale != stale || shown != rateShown;
  caughtUp = nextCaught;
  showPending = nextPending;
  stale = nextStale;
  rateShown = shown;
  if (layoutChanged || contentW <= 1) {
    relayout();
  }
  update(contentRect().adjusted(-6, -6, 6, 6));
  return true;
}

void OffroadScreensaver::relayout() {
  const QFont label = interFont(kLabelPx, QFont::DemiBold);
  const QFont value = interFont(kValuePx, QFont::Bold);
  const QFont caption = interFont(kCaptionPx, QFont::DemiBold);
  const QFontMetrics lm(label);
  const QFontMetrics vm(value);
  const QFontMetrics cm(caption);

  if (caughtUp) {
    const int target = qBound(220, qMin(qMax(1, width()), qMax(1, height())) / 3, 480);
    logoScaled = logo.isNull() ? QPixmap() : logo.scaledToWidth(target, Qt::SmoothTransformation);
    const int textW = cm.horizontalAdvance(QStringLiteral("Up to date"));
    contentW = qMax(logoScaled.width(), textW);
    contentH = logoScaled.height() + (logoScaled.isNull() ? 0 : 18) + cm.height();
  } else {
    logoScaled = QPixmap();
    const int speedW = vm.horizontalAdvance(QStringLiteral("888.8 Mbps")) + 16 + vm.horizontalAdvance(QString::fromUtf8("↑"));
    const int labelW = qMax(lm.horizontalAdvance(QStringLiteral("UPLOAD")), lm.horizontalAdvance(QStringLiteral("PENDING")));
    const int sizeW = vm.horizontalAdvance(QStringLiteral("888 GB"));
    contentW = qMax(speedW, qMax(labelW, sizeW));
    contentH = lm.height() + kGap + vm.height();
    if (showPending) {
      contentH += kBlockGap + lm.height() + kGap + vm.height();
    }
  }

  if (width() > 0 && contentW >= width()) {
    x = 0.f;
    vx = 0.f;
  } else if (qAbs(vx) < 1.f) {
    vx = kSpeedX;
  }
  if (height() > 0 && contentH >= height()) {
    y = 0.f;
    vy = 0.f;
  } else if (qAbs(vy) < 1.f) {
    vy = kSpeedY;
  }
  if (width() > contentW) {
    x = qBound(0.f, x, float(width() - contentW));
  }
  if (height() > contentH) {
    y = qBound(0.f, y, float(height() - contentH));
  }
}

QRect OffroadScreensaver::contentRect() const {
  return QRect(qRound(x), qRound(y), contentW, contentH);
}

void OffroadScreensaver::tick() {
  float dt = 0.016f;
  if (clock.isValid()) {
    dt = qBound(0.f, clock.restart() / 1000.f, 0.05f);
  } else {
    clock.start();
  }
  if (width() < 2 || height() < 2 || contentW < 1 || contentH < 1) {
    return;
  }

  const QRect old = contentRect();
  if (contentW < width()) {
    x += vx * dt;
    if (x < 0.f) {
      x = 0.f;
      vx = qAbs(vx);
    } else if (x + contentW > width()) {
      x = float(width() - contentW);
      vx = -qAbs(vx);
    }
  }
  if (contentH < height()) {
    y += vy * dt;
    if (y < 0.f) {
      y = 0.f;
      vy = qAbs(vy);
    } else if (y + contentH > height()) {
      y = float(height() - contentH);
      vy = -qAbs(vy);
    }
  }
  update(old.adjusted(-8, -8, 8, 8));
  update(contentRect().adjusted(-8, -8, 8, 8));
}

void OffroadScreensaver::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setClipRegion(event->region());
  p.fillRect(event->rect(), Qt::black);
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::TextAntialiasing);
  p.setRenderHint(QPainter::SmoothPixmapTransform);

  const QRect box = contentRect();
  if (caughtUp) {
    const QFont caption = interFont(kCaptionPx, QFont::DemiBold);
    const QFontMetrics cm(caption);
    int yCursor = box.top();
    if (!logoScaled.isNull()) {
      p.drawPixmap(box.left() + (box.width() - logoScaled.width()) / 2, yCursor, logoScaled);
      yCursor += logoScaled.height() + 18;
    }
    p.setFont(caption);
    p.setPen(Qt::white);
    p.drawText(QRect(box.left(), yCursor, box.width(), cm.height()), Qt::AlignHCenter | Qt::AlignVCenter, QStringLiteral("Up to date"));
    return;
  }

  const QFont label = interFont(kLabelPx, QFont::DemiBold);
  const QFont value = interFont(kValuePx, QFont::Bold);
  const QFontMetrics lm(label);
  const QFontMetrics vm(value);
  const QColor dim(0xAA, 0xAA, 0xAA);
  const QString speed = (!rateShown || stale)
      ? QString::fromUtf8("— Mbps")
      : bogpilot_uprate::mbps(sample.total) + QStringLiteral(" Mbps");
  const QString arrow = QString::fromUtf8("↑");
  const bool live = rateShown && !stale && sample.total > 0;
  const QColor arrowColor = live ? QColor(61, 220, 151) : QColor(0x77, 0x77, 0x77);

  int yCursor = box.top();
  auto drawLabel = [&](const QString &text) {
    p.setFont(label);
    p.setPen(dim);
    p.drawText(QRect(box.left(), yCursor, box.width(), lm.height()), Qt::AlignHCenter | Qt::AlignVCenter, text);
    yCursor += lm.height() + kGap;
  };

  drawLabel(QStringLiteral("UPLOAD"));

  const int sw = vm.horizontalAdvance(speed);
  const int aw = vm.horizontalAdvance(arrow);
  const int gap = 14;
  const int rowLeft = box.left() + (box.width() - (sw + gap + aw)) / 2;
  p.setFont(value);
  p.setPen(Qt::white);
  p.drawText(QRect(rowLeft, yCursor, sw, vm.height()), Qt::AlignLeft | Qt::AlignVCenter, speed);
  p.setPen(arrowColor);
  p.drawText(QRect(rowLeft + sw + gap, yCursor, aw + 4, vm.height()), Qt::AlignLeft | Qt::AlignVCenter, arrow);
  yCursor += vm.height();

  if (showPending) {
    yCursor += kBlockGap;
    drawLabel(QStringLiteral("PENDING"));
    const QString pending = stale
        ? QString::fromUtf8("— GB")
        : (sample.pending_bytes == 0 ? QStringLiteral("0 MB") : bogpilot_uprate::sizeText(sample.pending_bytes));
    p.setFont(value);
    p.setPen(Qt::white);
    p.drawText(QRect(box.left(), yCursor, box.width(), vm.height()), Qt::AlignHCenter | Qt::AlignVCenter, pending);
  }
}
