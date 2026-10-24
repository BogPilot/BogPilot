#include "selfdrive/ui/qt/window.h"

#include <QDateTime>
#include <QFontDatabase>

#include "system/hardware/hw.h"

MainWindow::MainWindow(QWidget *parent) : QWidget(parent) {
  main_layout = new QStackedLayout(this);
  main_layout->setMargin(0);

  homeWindow = new HomeWindow(this);
  main_layout->addWidget(homeWindow);
  QObject::connect(homeWindow, &HomeWindow::openSettings, this, &MainWindow::openSettings);
  QObject::connect(homeWindow, &HomeWindow::closeSettings, this, &MainWindow::closeSettings);

  settingsWindow = new SettingsWindow(this);
  main_layout->addWidget(settingsWindow);
  QObject::connect(settingsWindow, &SettingsWindow::closeSettings, this, &MainWindow::closeSettings);
  QObject::connect(settingsWindow, &SettingsWindow::reviewTrainingGuide, [=]() {
    onboardingWindow->showTrainingGuide();
    main_layout->setCurrentWidget(onboardingWindow);
  });
  QObject::connect(settingsWindow, &SettingsWindow::showDriverView, [=] {
    homeWindow->showDriverView(true);
  });

  onboardingWindow = new OnboardingWindow(this);
  main_layout->addWidget(onboardingWindow);
  QObject::connect(onboardingWindow, &OnboardingWindow::onboardingDone, [=]() {
    main_layout->setCurrentWidget(homeWindow);
  });
  if (!onboardingWindow->completed()) {
    main_layout->setCurrentWidget(onboardingWindow);
  }

  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    if (!offroad) {
      closeSettings();
    }
  });
  QObject::connect(device(), &Device::interactiveTimeout, [=]() {
    if (main_layout->currentWidget() == settingsWindow) {
      closeSettings();
    }
  });

  // load fonts
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Black.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Bold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-ExtraBold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-ExtraLight.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Medium.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Regular.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-SemiBold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Thin.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/JetBrainsMono-Medium.ttf");

  // no outline to prevent the focus rectangle
  setStyleSheet(R"(
    * {
      font-family: Inter;
      outline: none;
    }
  )");
  setAttribute(Qt::WA_NoSystemBackground);

  screensaver = new OffroadScreensaver(this);
  main_layout->addWidget(screensaver);
  QObject::connect(screensaver, &OffroadScreensaver::dismissed, this, [this]() {
    if (main_layout->currentWidget() == screensaver) {
      main_layout->setCurrentWidget(homeWindow);
    }
  });
  // Registered after the settings-closing timeout slot, so settings close first.
  QObject::connect(device(), &Device::interactiveTimeout, this, [this]() {
    if (main_layout->currentWidget() != homeWindow || uiState()->scene.started) {
      return;
    }
    if (screensaver->onInteractiveTimeout()) {
      main_layout->setCurrentWidget(screensaver);
    }
  });
  QObject::connect(uiState(), &UIState::offroadTransition, this, [this](bool offroad) {
    if (!offroad) {
      screensaver->dismiss();
    }
  });

  screenStream = new ScreenStream(this, this);
}

void MainWindow::openSettings(int index, const QString &param) {
  if (screensaver) {
    screensaver->dismiss();
  }
  main_layout->setCurrentWidget(settingsWindow);
  settingsWindow->setCurrentPanel(index, param);
}

void MainWindow::closeSettings() {
  main_layout->setCurrentWidget(homeWindow);

  if (uiState()->scene.started) {
    // Map is always shown when using navigate on openpilot
    if (uiState()->scene.navigate_on_openpilot) {
      homeWindow->showMapPanel(true);
    } else {
      homeWindow->showSidebar(params.getBool("Sidebar") || frogpilotUIState()->frogpilot_toggles.value("debug_mode").toBool());
    }
  }
}

bool MainWindow::eventFilter(QObject *obj, QEvent *event) {
  FrogPilotUIState &fs = *frogpilotUIState();
  FrogPilotUIScene &frogpilot_scene = fs.frogpilot_scene;
  QJsonObject &frogpilot_toggles = fs.frogpilot_toggles;

  const QEvent::Type type = event->type();
  const bool pointer = type == QEvent::TouchBegin || type == QEvent::TouchUpdate || type == QEvent::TouchEnd ||
                       type == QEvent::MouseButtonPress || type == QEvent::MouseButtonRelease || type == QEvent::MouseMove;
  // The tap that dismisses the screensaver must not fall through onto the home screen.
  if (pointer && (screensaver_block_until > QDateTime::currentMSecsSinceEpoch() || (screensaver && screensaver->isVisible()))) {
    device()->resetInteractiveTimeout(frogpilot_toggles.value("screen_timeout").toInt(), frogpilot_toggles.value("screen_timeout_onroad").toInt());
    if (screensaver && screensaver->isVisible()) {
      screensaver->dismiss();
      screensaver_block_until = QDateTime::currentMSecsSinceEpoch() + 500;
    }
    return true;
  }

  bool ignore = false;
  switch (type) {
    case QEvent::TouchBegin:
    case QEvent::TouchUpdate:
    case QEvent::TouchEnd:
    case QEvent::MouseButtonPress:
    case QEvent::MouseMove: {
      // ignore events when device is awakened by resetInteractiveTimeout
      ignore = !device()->isAwake() || frogpilot_scene.driver_camera_timer >= UI_FREQ / 2;
      device()->resetInteractiveTimeout(frogpilot_toggles.value("screen_timeout").toInt(), frogpilot_toggles.value("screen_timeout_onroad").toInt());
      break;
    }
    default:
      break;
  }
  return ignore;
}
