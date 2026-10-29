#pragma once

// View-only screen stream for a second display (e.g. the Tesla MCU2 browser).
//
// The UI thread renders the main window into a small QImage only while someone is watching.
// Everything else (JPEG encode, HTTP, sockets) runs on a worker thread. There are no input or
// write endpoints: the server only answers GET/HEAD for /, /stream, /frame.jpg and /stats.

#include <QByteArray>
#include <QElapsedTimer>
#include <QImage>
#include <QObject>
#include <QPointer>
#include <QThread>
#include <QTimer>
#include <QWidget>

#include <atomic>
#include <map>

class QTcpServer;
class QTcpSocket;

namespace screen_stream {
constexpr quint16 kPort = 8888;          // 8081/8082 are taken (The Pond uses 8082)
constexpr int kWidth = 1080;             // half of the 2160x1080 panel
constexpr int kHeight = 540;
constexpr int kJpegQuality = 70;
constexpr int kMaxFps = 10;
constexpr double kMaxUiDuty = 0.15;   // UI-thread share for grabs; lowers the fps if grabs get slow
constexpr int kMaxClients = 4;           // concurrent sockets, all kinds
constexpr int kIdleStopMs = 2000;        // stop capturing this long after the last viewer leaves
constexpr int kFrameWaitMs = 3000;       // /frame.jpg gives up after this long without a frame
constexpr int kMaxQueuedBytes = 512 * 1024;  // skip frames for a stream client that can't keep up
constexpr int kMaxRequestBytes = 4096;
constexpr int kParamPollMs = 2000;

// File-backed like the other BogPilot toggles: the device's prebuilt params_pyx.so cannot store new
// Params keys, and Params::clearAll would delete an unknown key. Absent file = on (the default).
constexpr const char *kToggleDir = "/data/params_bogpilot";
constexpr const char *kTogglePath = "/data/params_bogpilot/RemoteUIStream";
bool enabled();
void setEnabled(bool on);
}

// Lives on the worker thread.
class ScreenStreamServer : public QObject {
  Q_OBJECT

public:
  explicit ScreenStreamServer(QObject *parent = nullptr) : QObject(parent) {}

  std::atomic<bool> encoding{false};  // a frame is queued or being encoded

public slots:
  void start();
  void stop();
  void encodeFrame(const QImage &image, double grab_ms);

signals:
  void watchingChanged(bool watching);
  void listening(bool ok);

private:
  struct Client {
    QTcpSocket *socket = nullptr;
    QByteArray buffer;
    bool streaming = false;
    bool waiting_frame = false;
    bool keep_alive = false;
    bool head = false;
    qint64 wait_started_ms = 0;
    qint64 last_activity_ms = 0;
  };

  void onNewConnection();
  void onReadyRead(QTcpSocket *socket);
  void onDisconnected(QTcpSocket *socket);
  void processBuffer(Client &client);
  void handleRequest(Client &client, const QByteArray &method, const QByteArray &path);
  void sendResponse(Client &client, int code, const QByteArray &type, const QByteArray &body);
  void sendFrame(Client &client);
  void closeClient(Client &client);
  void housekeeping();
  void updateWatching();
  QByteArray statsJson() const;

  QTcpServer *server = nullptr;
  QTimer *housekeeping_timer = nullptr;
  std::map<QTcpSocket *, Client> clients;

  QByteArray latest_jpeg;
  quint64 frame_seq = 0;
  qint64 last_poll_ms = -1000000;
  qint64 last_watched_ms = -1000000;
  bool watching = false;

  QElapsedTimer clock;
  // Stats (worker thread only).
  double grab_ms_avg = 0, encode_ms_avg = 0, fps_avg = 0;
  qint64 last_frame_ms = 0;
  quint64 frames_total = 0;
  quint64 window_bytes = 0;
  qint64 window_start_ms = 0;
  double kbps = 0;
};

// Lives on the UI thread. Owns the worker thread and the capture timer.
class ScreenStream : public QObject {
  Q_OBJECT

public:
  explicit ScreenStream(QWidget *root, QObject *parent = nullptr);
  ~ScreenStream() override;

signals:
  void frameReady(const QImage &image, double grab_ms);

private:
  void checkParam();
  void startServer();
  void stopServer();
  void setWatching(bool watching);
  void capture();

  QPointer<QWidget> root;
  QThread *thread = nullptr;
  ScreenStreamServer *server = nullptr;
  QTimer param_timer;
  QTimer capture_timer;
  qint64 retry_after_ms = 0;
  double grab_ms_avg = 0;
  QElapsedTimer clock;
};
