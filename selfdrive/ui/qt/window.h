#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/home.h"
#include "selfdrive/ui/qt/offroad/onboarding.h"
#include "selfdrive/ui/qt/offroad/settings.h"

#include "frogpilot/ui/qt/offroad/offroad_screensaver.h"
#include "frogpilot/ui/screenstream/screen_stream.h"

class MainWindow : public QWidget {
  Q_OBJECT

public:
  explicit MainWindow(QWidget *parent = 0);

private:
  bool eventFilter(QObject *obj, QEvent *event) override;
  void openSettings(int index = 0, const QString &param = "");
  void closeSettings();

  QStackedLayout *main_layout;
  HomeWindow *homeWindow;
  SettingsWindow *settingsWindow;
  OnboardingWindow *onboardingWindow;

  // FrogPilot variables
  Params params;

  // BogPilot: view-only browser stream of this window (port 8888)
  ScreenStream *screenStream;

  // BogPilot: offroad idle face, shown from main_layout when the offroad timeout fires.
  OffroadScreensaver *screensaver = nullptr;
  qint64 screensaver_block_until = 0;
};
