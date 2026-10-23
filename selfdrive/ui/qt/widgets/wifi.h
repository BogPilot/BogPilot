#pragma once

#include <QFrame>
#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/ui.h"

// BogPilot: realdata rlogsync upload card (page 4), same switch / file / helpers as the onroad readout
#include "frogpilot/ui/qt/onroad/upload_rate.h"

#if BOGPILOT_UPLOAD_RATE
#include <QLabel>
#include <QPixmap>
#include <QTimer>
#endif

class WiFiPromptWidget : public QFrame {
  Q_OBJECT

public:
  explicit WiFiPromptWidget(QWidget* parent = 0);

signals:
  void openSettings(int index = 0, const QString &param = "");

public slots:
  void updateState(const UIState &s, const FrogPilotUIState &fs);

protected:
  QStackedLayout *stack;

#if BOGPILOT_UPLOAD_RATE
  void showEvent(QShowEvent *event) override;

private:
  QWidget *buildRealdataPage();
  void readRealdata();      // 1 Hz: rate file (+ ShowUploadRate every 2 s), refreshes the labels
  void refreshRealdata();   // fills page 4 from the cached sample; sets realdataShown

  QWidget *realdataPage = nullptr;
  QLabel *rdTitle = nullptr, *rdIcon = nullptr, *rdUpLabel = nullptr, *rdUpValue = nullptr, *rdUpArrow = nullptr, *rdStatus = nullptr;
  QLabel *rdPendLabel = nullptr, *rdPendValue = nullptr, *rdPendSub = nullptr, *rdFooter = nullptr;
  QLabel *rdEtaHead = nullptr;                                      // "ETA" over the tier rows' right column
  QLabel *rdTierTag[2] = {}, *rdTierValue[2] = {}, *rdTierEta[2] = {};   // rev4b: Logs / HEVC rows
  QWidget *rdTierRow[2] = {};
  QPixmap rdUploadingIcon, rdDoneIcon;
  QString rdArrowStyle, rdSubStyle;
  QTimer *rdTimer = nullptr;
  bogpilot_uprate::Sample rdSample;
  bool rdEnabled = true;
  bool rdCommaUploadsOff = false;
  bool realdataShown = false;
  int rdToggleCountdown = 0;
  int rdMinHeight = 0;      // tallest state, measured on first show; applied only while page 4 is shown
#endif
};
