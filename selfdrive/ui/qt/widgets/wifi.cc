#include "selfdrive/ui/qt/widgets/wifi.h"

#include <QHBoxLayout>
#include <QLabel>
#include <QPixmap>
#include <QPushButton>

#if BOGPILOT_UPLOAD_RATE
#include <QDateTime>

#include <algorithm>
#endif

WiFiPromptWidget::WiFiPromptWidget(QWidget *parent) : QFrame(parent) {
  stack = new QStackedLayout(this);

  // Setup Wi-Fi
  QFrame *setup = new QFrame;
  QVBoxLayout *setup_layout = new QVBoxLayout(setup);
  setup_layout->setContentsMargins(56, 40, 56, 40);
  setup_layout->setSpacing(20);
  {
    QHBoxLayout *title_layout = new QHBoxLayout;
    title_layout->setSpacing(32);
    {
      QLabel *icon = new QLabel;
      QPixmap pixmap("../assets/offroad/icon_wifi_strength_full.svg");
      icon->setPixmap(pixmap.scaledToWidth(80, Qt::SmoothTransformation));
      title_layout->addWidget(icon);

      QLabel *title = new QLabel(tr("Setup Wi-Fi"));
      title->setStyleSheet("font-size: 64px; font-weight: 600;");
      title_layout->addWidget(title);
      title_layout->addStretch();
    }
    setup_layout->addLayout(title_layout);

    QLabel *desc = new QLabel(tr("Connect to Wi-Fi to upload driving data and help improve openpilot"));
    desc->setStyleSheet("font-size: 40px; font-weight: 400;");
    desc->setWordWrap(true);
    setup_layout->addWidget(desc);

    QPushButton *settings_btn = new QPushButton(tr("Open Settings"));
    connect(settings_btn, &QPushButton::clicked, [=]() { emit openSettings(1); });
    settings_btn->setStyleSheet(R"(
      QPushButton {
        font-size: 48px;
        font-weight: 500;
        border-radius: 10px;
        background-color: #465BEA;
        padding: 32px;
      }
      QPushButton:pressed {
        background-color: #3049F4;
      }
    )");
    setup_layout->addWidget(settings_btn);
  }
  stack->addWidget(setup);

  // Uploading data
  QWidget *uploading = new QWidget;
  QVBoxLayout *uploading_layout = new QVBoxLayout(uploading);
  uploading_layout->setContentsMargins(64, 56, 64, 56);
  uploading_layout->setSpacing(36);
  {
    QHBoxLayout *title_layout = new QHBoxLayout;
    {
      QLabel *title = new QLabel(tr("Ready to upload"));
      title->setStyleSheet("font-size: 64px; font-weight: 600;");
      title->setWordWrap(true);
      title->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Minimum);
      title_layout->addWidget(title);
      title_layout->addStretch();

      QLabel *icon = new QLabel;
      QPixmap pixmap("../assets/offroad/icon_wifi_uploading.svg");
      icon->setPixmap(pixmap.scaledToWidth(120, Qt::SmoothTransformation));
      title_layout->addWidget(icon);
    }
    uploading_layout->addLayout(title_layout);

    QLabel *desc = new QLabel(tr("Training data will be pulled periodically while your device is on Wi-Fi"));
    desc->setStyleSheet("font-size: 48px; font-weight: 400;");
    desc->setWordWrap(true);
    uploading_layout->addWidget(desc);
  }
  stack->addWidget(uploading);

  // Not uploading data
  QWidget *notUploading = new QWidget;
  QVBoxLayout *not_uploading_layout = new QVBoxLayout(notUploading);
  not_uploading_layout->setContentsMargins(64, 56, 64, 56);
  not_uploading_layout->setSpacing(36);
  {
    QHBoxLayout *title_layout = new QHBoxLayout;
    {
      QLabel *title = new QLabel(tr("Uploading disabled"));
      title->setStyleSheet("font-size: 64px; font-weight: 600;");
      title->setWordWrap(true);
      title->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Minimum);
      title_layout->addWidget(title);
      title_layout->addStretch();

      QLabel *icon = new QLabel;
      QPixmap pixmap("../../frogpilot/assets/other_images/icon_wifi_uploading_disabled.svg");
      icon->setPixmap(pixmap.scaledToWidth(120, Qt::SmoothTransformation));
      title_layout->addWidget(icon);
    }
    not_uploading_layout->addLayout(title_layout);

    QLabel *desc = new QLabel(tr("Toggle off the \"Turn Off Data Uploads\" toggle to re-enable uploads."));
    desc->setStyleSheet("font-size: 48px; font-weight: 400;");
    desc->setWordWrap(true);
    not_uploading_layout->addWidget(desc);
  }
  stack->addWidget(notUploading);

#if BOGPILOT_UPLOAD_RATE
  // Realdata rlogsync upload (page 4)
  realdataPage = buildRealdataPage();
  stack->addWidget(realdataPage);

  rdTimer = new QTimer(this);
  QObject::connect(rdTimer, &QTimer::timeout, this, &WiFiPromptWidget::readRealdata);
  rdTimer->start(bogpilot_uprate::READ_INTERVAL_MS);
#endif

  setStyleSheet(R"(
    WiFiPromptWidget {
      background-color: #333333;
      border-radius: 10px;
    }
  )");

  QObject::connect(uiState(), &UIState::uiUpdate, this, &WiFiPromptWidget::updateState);
}

void WiFiPromptWidget::updateState(const UIState &s, const FrogPilotUIState &fs) {
  if (!isVisible()) return;

  auto &sm = *(s.sm);

  auto network_type = sm["deviceState"].getDeviceState().getNetworkType();
  auto uploading = network_type == cereal::DeviceState::NetworkType::WIFI ||
      network_type == cereal::DeviceState::NetworkType::ETHERNET;
  const bool no_uploads = fs.frogpilot_toggles.value("no_uploads").toBool();
#if BOGPILOT_UPLOAD_RATE
  // "Turn Off Data Uploads" only stops comma uploads; the realdata rsync is separate, so its card wins while it is
  // fresh and notes the toggle in its footer. No fresh rate file (or ShowUploadRate off): the stock pages.
  if (rdCommaUploadsOff != no_uploads) {
    rdCommaUploadsOff = no_uploads;
    refreshRealdata();
  }
  if (realdataShown) {
    stack->setCurrentWidget(realdataPage);
    return;
  }
#endif
  stack->setCurrentIndex(no_uploads ? 2 : uploading ? 1 : 0);
}

#if BOGPILOT_UPLOAD_RATE
// Labels start empty and their text is set at runtime (untranslated, like the onroad readout), so the UI's
// "all strings wrapped" test and the .ts files are unaffected.
QWidget *WiFiPromptWidget::buildRealdataPage() {
  QWidget *page = new QWidget;
  QVBoxLayout *layout = new QVBoxLayout(page);
  layout->setContentsMargins(64, 56, 64, 56);
  layout->setSpacing(0);

  QHBoxLayout *title_layout = new QHBoxLayout;
  rdTitle = new QLabel;
  rdTitle->setStyleSheet("font-size: 64px; font-weight: 600;");
  title_layout->addWidget(rdTitle);
  title_layout->addStretch();
  rdUploadingIcon = QPixmap("../assets/offroad/icon_wifi_uploading.svg").scaledToWidth(120, Qt::SmoothTransformation);
  rdDoneIcon = QPixmap("../assets/img_circled_check.svg").scaledToWidth(100, Qt::SmoothTransformation);
  rdIcon = new QLabel;
  rdIcon->setPixmap(rdUploadingIcon);
  title_layout->addWidget(rdIcon);
  layout->addLayout(title_layout);
  layout->addSpacing(28);

  const QString small_style = "color: #AAAAAA; font-size: 36px; font-weight: 600;";
  const QString status_style = "color: #AAAAAA; font-size: 38px; font-weight: 400;";

  rdUpLabel = new QLabel;
  rdUpLabel->setStyleSheet(small_style);
  layout->addWidget(rdUpLabel);

  QHBoxLayout *up_layout = new QHBoxLayout;
  up_layout->setSpacing(16);
  rdUpValue = new QLabel;
  rdUpValue->setStyleSheet("font-size: 96px; font-weight: bold;");
  up_layout->addWidget(rdUpValue);
  rdUpArrow = new QLabel;
  rdUpArrow->setStyleSheet("font-size: 96px; font-weight: bold;");
  up_layout->addWidget(rdUpArrow);
  up_layout->addStretch();
  layout->addLayout(up_layout);

  rdStatus = new QLabel;   // "no update for N s" while stale
  rdStatus->setStyleSheet(status_style);
  layout->addWidget(rdStatus);
  layout->addSpacing(30);

  QHBoxLayout *pend_head = new QHBoxLayout;
  rdPendLabel = new QLabel;
  rdPendLabel->setStyleSheet(small_style);
  pend_head->addWidget(rdPendLabel);
  pend_head->addStretch();
  rdEtaHead = new QLabel;   // "ETA" above the tier rows' right column (rev4b only)
  rdEtaHead->setStyleSheet(small_style);
  pend_head->addWidget(rdEtaHead);
  layout->addLayout(pend_head);
  rdPendValue = new QLabel;
  rdPendValue->setStyleSheet("font-size: 96px; font-weight: bold;");
  layout->addWidget(rdPendValue);
  rdPendSub = new QLabel;   // ETA / growing / seg count / all sent, coloured per state (rlogsync without tier tokens)
  rdPendSub->setStyleSheet("font-size: 42px; font-weight: 600;");
  layout->addWidget(rdPendSub);
  // rev4b: one row per tier, "Logs  4 segs .... 2 min" / "HEVC  54 files · 1.7 GB .... 1 h 25 m"
  for (int i = 0; i < 2; ++i) {
    rdTierRow[i] = new QWidget;
    QHBoxLayout *row = new QHBoxLayout(rdTierRow[i]);
    row->setContentsMargins(0, i == 0 ? 0 : 6, 0, 0);
    row->setSpacing(22);
    rdTierTag[i] = new QLabel;
    rdTierTag[i]->setStyleSheet("color: #AAAAAA; font-size: 40px; font-weight: 600;");
    rdTierTag[i]->setFixedWidth(116);   // "HEVC" at Inter 40 px SemiBold = 111 px
    row->addWidget(rdTierTag[i]);
    rdTierValue[i] = new QLabel;
    rdTierValue[i]->setStyleSheet("font-size: 40px; font-weight: 600;");
    row->addWidget(rdTierValue[i]);
    row->addStretch();
    rdTierEta[i] = new QLabel;
    rdTierEta[i]->setStyleSheet("font-size: 40px; font-weight: 600;");
    row->addWidget(rdTierEta[i]);
    layout->addWidget(rdTierRow[i]);
  }
  layout->addSpacing(22);

  rdFooter = new QLabel;
  rdFooter->setStyleSheet(status_style);
  layout->addWidget(rdFooter);
  layout->addStretch();

  page->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Ignored);   // no say in the card size until it is shown
  return page;
}

void WiFiPromptWidget::showEvent(QShowEvent *event) {
  QFrame::showEvent(event);

  // Minimum height = the tallest state (sub-line + footer; the stale line replaces the sub-line), so the card keeps
  // its size as the state changes. Measured once, after the stylesheets have been applied.
  if (rdMinHeight == 0) {
    realdataPage->ensurePolished();
    rdTitle->setText("Uploading");
    rdUpLabel->setText("REALDATA UPLOAD");
    rdUpValue->setText("88.8 Mbps");
    rdUpArrow->setText(QString::fromUtf8("↑"));
    rdStatus->setVisible(false);
    rdPendLabel->setText("PENDING");
    rdPendValue->setText("88 GB");
    rdPendSub->setText("ETA 1 h 25 min");
    rdFooter->setText("comma uploads: off");
    rdStatus->setText("no update for 88 s");
    rdEtaHead->setText(QString::fromUtf8("last known · checking…"));
    for (int i = 0; i < 2; ++i) {
      rdTierTag[i]->setText(i == 0 ? "Logs" : "HEVC");
      rdTierValue[i]->setText(i == 0 ? "88 segs" : QString::fromUtf8("888 files · 88 GB"));
      rdTierEta[i]->setText("8 h 88 m");
    }
    // tallest real state: both tier rows (rev4b), the single sub-line (older rlogsync) or the stale line
    for (QLabel *l : {rdPendLabel, rdPendValue, rdFooter, rdEtaHead}) l->setVisible(true);
    for (QWidget *w : rdTierRow) w->setVisible(true);
    rdPendSub->setVisible(false);
    rdStatus->setVisible(false);
    const int tiers_h = realdataPage->layout()->sizeHint().height();
    for (QWidget *w : rdTierRow) w->setVisible(false);
    rdPendSub->setVisible(true);
    const int single_h = realdataPage->layout()->sizeHint().height();
    rdPendSub->setVisible(false);
    rdStatus->setVisible(true);
    const int stale_h = realdataPage->layout()->sizeHint().height();
    rdMinHeight = std::max({tiers_h, single_h, stale_h});
    realdataPage->setMinimumHeight(realdataShown ? rdMinHeight : 0);
    for (QLabel *l : {rdTitle, rdUpLabel, rdUpValue, rdUpArrow, rdStatus, rdPendLabel, rdPendValue, rdPendSub, rdFooter, rdEtaHead}) l->clear();
    for (int i = 0; i < 2; ++i) for (QLabel *l : {rdTierTag[i], rdTierValue[i], rdTierEta[i]}) l->clear();
  }

  readRealdata();
}

void WiFiPromptWidget::readRealdata() {
  if (!isVisible()) {
    return;
  }
  if (rdToggleCountdown-- <= 0) {
    rdEnabled = bogpilot_uprate::toggleEnabled();
    rdToggleCountdown = 1;   // every 2nd tick = 2 s
  }
  rdSample = rdEnabled ? bogpilot_uprate::readFile() : bogpilot_uprate::Sample();
  refreshRealdata();
  if (realdataShown) {
    stack->setCurrentWidget(realdataPage);
  } else if (stack->currentWidget() == realdataPage) {
    stack->setCurrentIndex(rdCommaUploadsOff ? 2 : 1);   // stock page until the next uiUpdate picks the exact one
  }
}

void WiFiPromptWidget::refreshRealdata() {
  using namespace bogpilot_uprate;
  const qint64 age = QDateTime::currentSecsSinceEpoch() - rdSample.epoch;
  // right after boot rlogsync's rate writer may not have written yet: "checking…" instead of the stock page
  const bool checking = bootChecking(rdSample, uptimeSeconds(), rdEnabled);
  const bool shown = rdEnabled && (fresh(rdSample, age) || checking);
  if (shown != realdataShown) {
    realdataShown = shown;
    // the stack sizes to its largest page, so page 4 only takes part (size and minimum) while it is shown
    realdataPage->setSizePolicy(shown ? QSizePolicy::Preferred : QSizePolicy::Ignored, shown ? QSizePolicy::Preferred : QSizePolicy::Ignored);
    realdataPage->setMinimumHeight(shown ? rdMinHeight : 0);
    updateGeometry();
  }
  if (!shown) {
    return;
  }

  if (checking) {
    rdTitle->setText("Uploading");
    rdIcon->setPixmap(rdUploadingIcon);
    rdUpLabel->setText("REALDATA UPLOAD");
    rdUpValue->setText(QString::fromUtf8("— Mbps"));
    rdUpArrow->setText(QString::fromUtf8("↑"));
    const QString grey_arrow = "font-size: 96px; font-weight: bold; color: #777777;";
    if (grey_arrow != rdArrowStyle) {
      rdArrowStyle = grey_arrow;
      rdUpArrow->setStyleSheet(grey_arrow);
    }
    rdStatus->setText(QString::fromUtf8("checking…"));
    rdStatus->setVisible(true);
    for (QLabel *l : {rdPendLabel, rdPendValue, rdPendSub, rdEtaHead}) l->setVisible(false);
    for (QWidget *w : rdTierRow) w->setVisible(false);
    rdFooter->setText(rdCommaUploadsOff ? "comma uploads: off" : "");
    return;
  }

  const bool stale = age > STALE_S;
  const Backlog backlog = backlogState(rdSample);
  const bool done = !stale && backlog == Backlog::CaughtUp;

  rdTitle->setText(done ? "Up to date" : "Uploading");
  rdIcon->setPixmap(done ? rdDoneIcon : rdUploadingIcon);

  rdUpLabel->setText("REALDATA UPLOAD");
  rdUpValue->setText(stale ? QString::fromUtf8("— Mbps") : mbps(rdSample.total) + " Mbps");
  rdUpArrow->setText(QString::fromUtf8("↑"));
  const QString arrow_style = QString("font-size: 96px; font-weight: bold; color: %1;").arg(stale || rdSample.total <= 0 ? "#777777" : "rgb(61, 220, 151)");
  if (arrow_style != rdArrowStyle) {
    rdArrowStyle = arrow_style;
    rdUpArrow->setStyleSheet(arrow_style);
  }

  const bool saved = rdSample.has_pending && rdSample.saved;   // boot: last scan from before the reboot
  rdStatus->setText(QString("no update for %1 s").arg(age));
  rdStatus->setVisible(stale);

  // rlogsync v3.1 (5-field line): speed only. rev4b (L=/V= tokens): Logs + HEVC rows instead of the single sub-line
  const QList<TierLine> tiers = tierLines(rdSample);
  const bool show_tiers = rdSample.has_pending && !stale && !tiers.isEmpty();
  rdPendLabel->setVisible(rdSample.has_pending);
  rdPendValue->setVisible(rdSample.has_pending);
  rdPendSub->setVisible(rdSample.has_pending && !stale && !show_tiers);
  // saved scan: no ETAs yet, the column head says so (keeps the card at the mockup height)
  rdEtaHead->setText(saved ? QString::fromUtf8("last known · checking…") : QString("ETA"));
  rdEtaHead->setVisible(show_tiers);
  for (int i = 0; i < 2; ++i) {
    rdTierRow[i]->setVisible(show_tiers);
    if (!show_tiers) continue;
    const TierLine &t = tiers[i];
    auto color_css = [](const QColor &c) { return QString("color: rgba(%1, %2, %3, %4);").arg(c.red()).arg(c.green()).arg(c.blue()).arg(c.alpha()); };
    rdTierTag[i]->setText(t.tag);
    rdTierValue[i]->setText(t.value);
    rdTierEta[i]->setText(t.eta);
    const QString vs = "font-size: 40px; font-weight: 600; " + color_css(t.value_color);
    const QString es = "font-size: 40px; font-weight: 600; " + color_css(t.eta_color);
    if (rdTierValue[i]->styleSheet() != vs) rdTierValue[i]->setStyleSheet(vs);
    if (rdTierEta[i]->styleSheet() != es) rdTierEta[i]->setStyleSheet(es);
  }
  if (rdSample.has_pending) {
    rdPendLabel->setText("PENDING");
    rdPendValue->setText(stale ? QString::fromUtf8("— GB") : (rdSample.pending_bytes == 0 ? QString("0 MB") : sizeText(rdSample.pending_bytes)));
    QColor color;
    rdPendSub->setText(pendingSubLine(rdSample, &color));
    if (saved) rdPendSub->setText(QString::fromUtf8("%1 seg · last known").arg(rdSample.pending_segs));
    const QString sub_style = QString("font-size: 42px; font-weight: 600; color: rgba(%1, %2, %3, %4);").arg(color.red()).arg(color.green()).arg(color.blue()).arg(color.alpha());
    if (sub_style != rdSubStyle) {
      rdSubStyle = sub_style;
      rdPendSub->setStyleSheet(sub_style);
    }
  }

  rdFooter->setText(rdCommaUploadsOff ? "comma uploads: off" : "");

  // never shrink: if real text ever needs more than the measured minimum, keep the larger height from then on
  const int h = realdataPage->layout()->sizeHint().height();
  if (h > rdMinHeight) {
    rdMinHeight = h;
    realdataPage->setMinimumHeight(h);
  }
}
#endif
