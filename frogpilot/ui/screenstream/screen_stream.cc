#include "frogpilot/ui/screenstream/screen_stream.h"

#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <QBuffer>
#include <QDir>
#include <QFile>
#include <QHostAddress>
#include <QPainter>
#include <QTcpServer>
#include <QTcpSocket>

#include <algorithm>
#include <cstdlib>

using namespace screen_stream;

namespace {

// Viewer page. Frames are fetched one at a time from /frame.jpg and drawn on a <canvas>:
// the MCU2 browser pauses <video> while in Drive but keeps painting canvas, and fetch +
// createImageBitmap needs nothing newer than what Chrome 136 already has (no WebRTC/WebCodecs/MSE).
// Each /frame.jpg request waits for the next captured frame, so the loop paces itself to the
// capture rate and to the Wi-Fi link without piling up requests.
const char kIndexHtml[] = R"HTML(<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BogPilot screen</title>
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden}
canvas{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:min(100vw,200vh);height:min(50vw,100vh);background:#000}
#m{position:absolute;left:12px;bottom:10px;color:#777;font:16px sans-serif}
</style></head>
<body><canvas id="c" width="1080" height="540"></canvas><div id="m">connecting&hellip;</div>
<script>
var c=document.getElementById('c'),x=c.getContext('2d'),m=document.getElementById('m'),n=0,fails=0;
function draw(b){
  if(window.createImageBitmap)return createImageBitmap(b).then(function(i){x.drawImage(i,0,0,c.width,c.height);if(i.close)i.close();});
  return new Promise(function(ok,no){var u=URL.createObjectURL(b),i=new Image();
    i.onload=function(){x.drawImage(i,0,0,c.width,c.height);URL.revokeObjectURL(u);ok();};
    i.onerror=function(){URL.revokeObjectURL(u);no();};i.src=u;});
}
function next(){
  fetch('/frame.jpg?n='+(n++),{cache:'no-store'}).then(function(r){if(!r.ok)throw r.status;return r.blob();})
  .then(draw).then(function(){fails=0;m.style.display='none';next();})
  .catch(function(){fails++;m.style.display='block';m.textContent='waiting for the comma\u2026';setTimeout(next,Math.min(5000,500*fails));});
}
next();
</script></body></html>
)HTML";

constexpr char kBoundary[] = "bogpilotframe";

QByteArray statusText(int code) {
  switch (code) {
    case 200: return "OK";
    case 404: return "Not Found";
    case 405: return "Method Not Allowed";
    case 413: return "Payload Too Large";
    case 503: return "Service Unavailable";
    default: return "Bad Request";
  }
}

}  // namespace

bool screen_stream::enabled() {
  QFile file(kTogglePath);
  if (!file.open(QIODevice::ReadOnly)) {
    return true;
  }
  QByteArray value = file.read(16).trimmed().toLower();
  return !(value == "0" || value == "false" || value == "off");
}

void screen_stream::setEnabled(bool on) {
  QDir().mkpath(kToggleDir);
  QFile file(kTogglePath);
  if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    file.write(on ? "1" : "0");
  }
}

// ---------------------------------------------------------------- worker thread

void ScreenStreamServer::start() {
  // Encoding must never compete with the UI thread (the UI process runs at nice -20).
  setpriority(PRIO_PROCESS, static_cast<id_t>(syscall(SYS_gettid)), 10);

  clock.start();
  window_start_ms = clock.elapsed();

  server = new QTcpServer(this);
  server->setMaxPendingConnections(kMaxClients);
  connect(server, &QTcpServer::newConnection, this, &ScreenStreamServer::onNewConnection);
  bool ok = server->listen(QHostAddress::AnyIPv4, kPort);
  if (!ok) {
    qWarning() << "ScreenStream: cannot listen on port" << kPort << server->errorString();
  }

  housekeeping_timer = new QTimer(this);
  housekeeping_timer->setInterval(250);
  connect(housekeeping_timer, &QTimer::timeout, this, &ScreenStreamServer::housekeeping);
  housekeeping_timer->start();

  emit listening(ok);
}

void ScreenStreamServer::stop() {
  if (housekeeping_timer) {
    housekeeping_timer->stop();
  }
  for (auto &[socket, client] : clients) {
    socket->disconnect(this);
    socket->abort();
    delete socket;
  }
  clients.clear();
  delete server;
  server = nullptr;
  delete housekeeping_timer;
  housekeeping_timer = nullptr;
  if (watching) {
    watching = false;
    emit watchingChanged(false);
  }
}

void ScreenStreamServer::onNewConnection() {
  while (server && server->hasPendingConnections()) {
    QTcpSocket *socket = server->nextPendingConnection();
    if (static_cast<int>(clients.size()) >= kMaxClients) {
      socket->write("HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
      socket->disconnectFromHost();
      connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
      continue;
    }
    Client &client = clients[socket];
    client.socket = socket;
    client.last_activity_ms = clock.elapsed();
    connect(socket, &QTcpSocket::readyRead, this, [this, socket]() { onReadyRead(socket); });
    // Queued so a socket never leaves the client map while it is being iterated.
    connect(socket, &QTcpSocket::disconnected, this, [this, socket]() { onDisconnected(socket); }, Qt::QueuedConnection);
  }
}

void ScreenStreamServer::onReadyRead(QTcpSocket *socket) {
  auto it = clients.find(socket);
  if (it == clients.end()) {
    return;
  }
  Client &client = it->second;
  QByteArray data = socket->readAll();
  client.last_activity_ms = clock.elapsed();
  if (client.streaming) {
    return;  // a streaming client has nothing more to say; ignore anything it sends
  }
  client.buffer += data;
  processBuffer(client);
}

void ScreenStreamServer::processBuffer(Client &client) {
  while (!client.streaming && !client.waiting_frame) {
    int end = client.buffer.indexOf("\r\n\r\n");
    if (end < 0) {
      if (client.buffer.size() > kMaxRequestBytes) {
        client.keep_alive = false;
        sendResponse(client, 413, "text/plain", "request too large\n");
      }
      return;
    }
    QByteArray header = client.buffer.left(end);
    client.buffer.remove(0, end + 4);

    QList<QByteArray> lines = header.split('\n');
    QList<QByteArray> request = lines.value(0).trimmed().split(' ');
    if (request.size() < 3) {
      client.keep_alive = false;
      sendResponse(client, 400, "text/plain", "bad request\n");
      return;
    }
    client.keep_alive = request[2] == "HTTP/1.1";
    for (int i = 1; i < lines.size(); ++i) {
      QByteArray line = lines[i].trimmed().toLower();
      if (line.startsWith("connection:")) {
        client.keep_alive = !line.contains("close");
      } else if (line.startsWith("content-length:") && line.mid(15).trimmed() != "0") {
        client.keep_alive = false;  // nothing here accepts a body
      }
    }
    QByteArray path = request[1];
    int query = path.indexOf('?');
    if (query >= 0) {
      path.truncate(query);
    }
    handleRequest(client, request[0], path);
    if (!client.socket || !clients.count(client.socket) || client.socket->state() != QAbstractSocket::ConnectedState) {
      return;
    }
  }
}

void ScreenStreamServer::handleRequest(Client &client, const QByteArray &method, const QByteArray &path) {
  client.head = method == "HEAD";
  if (method != "GET" && !client.head) {
    client.keep_alive = false;
    sendResponse(client, 405, "text/plain", "read-only\n");
    return;
  }

  if (path == "/" || path == "/index.html") {
    sendResponse(client, 200, "text/html; charset=utf-8", QByteArray(kIndexHtml));
  } else if (path == "/stats") {
    sendResponse(client, 200, "application/json", statsJson());
  } else if (path == "/frame.jpg") {
    last_poll_ms = clock.elapsed();
    if (client.head) {
      sendResponse(client, 200, "image/jpeg", QByteArray());
      return;
    }
    client.waiting_frame = true;  // answered by the next captured frame
    client.wait_started_ms = clock.elapsed();
    updateWatching();
  } else if (path == "/stream") {
    QByteArray header = "HTTP/1.1 200 OK\r\n"
                        "Content-Type: multipart/x-mixed-replace; boundary=" + QByteArray(kBoundary) + "\r\n"
                        "Cache-Control: no-store\r\nConnection: close\r\n\r\n";
    client.socket->write(header);
    if (client.head) {
      client.socket->disconnectFromHost();
      return;
    }
    client.streaming = true;
    client.buffer.clear();
    updateWatching();
  } else {
    sendResponse(client, 404, "text/plain", "not found\n");
  }
}

void ScreenStreamServer::sendResponse(Client &client, int code, const QByteArray &type, const QByteArray &body) {
  QByteArray out = "HTTP/1.1 " + QByteArray::number(code) + " " + statusText(code) + "\r\n";
  out += "Content-Type: " + type + "\r\n";
  out += "Content-Length: " + QByteArray::number(body.size()) + "\r\n";
  out += "Cache-Control: no-store\r\n";
  out += client.keep_alive ? "Connection: keep-alive\r\n\r\n" : "Connection: close\r\n\r\n";
  if (!client.head) {
    out += body;
  }
  client.socket->write(out);
  window_bytes += out.size();
  if (!client.keep_alive) {
    client.socket->disconnectFromHost();
  }
}

void ScreenStreamServer::sendFrame(Client &client) {
  client.waiting_frame = false;
  sendResponse(client, 200, "image/jpeg", latest_jpeg);
}

void ScreenStreamServer::closeClient(Client &client) {
  client.keep_alive = false;
  client.socket->disconnectFromHost();
}

void ScreenStreamServer::onDisconnected(QTcpSocket *socket) {
  if (clients.erase(socket) == 0) {
    return;  // already removed (stop() or a duplicate signal)
  }
  socket->deleteLater();
  updateWatching();
}

void ScreenStreamServer::encodeFrame(const QImage &image, double grab_ms) {
  QElapsedTimer timer;
  timer.start();
  QByteArray jpeg;
  {
    QBuffer buffer(&jpeg);
    buffer.open(QIODevice::WriteOnly);
    if (!image.save(&buffer, "JPG", kJpegQuality)) {
      jpeg.clear();
    }
  }
  double encode_ms = timer.nsecsElapsed() / 1e6;

  if (!jpeg.isEmpty()) {
    latest_jpeg = jpeg;
    frame_seq++;
    frames_total++;

    qint64 now = clock.elapsed();
    const double a = frames_total == 1 ? 1.0 : 0.1;
    grab_ms_avg += a * (grab_ms - grab_ms_avg);
    encode_ms_avg += a * (encode_ms - encode_ms_avg);
    if (frames_total > 1 && now > last_frame_ms) {
      fps_avg += a * (1000.0 / (now - last_frame_ms) - fps_avg);
    }
    last_frame_ms = now;

    QByteArray part = "--" + QByteArray(kBoundary) + "\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                      QByteArray::number(jpeg.size()) + "\r\n\r\n";
    for (auto &[socket, client] : clients) {
      if (client.streaming) {
        if (socket->bytesToWrite() > kMaxQueuedBytes) {
          continue;  // slow link: drop this frame for this viewer instead of queueing latency
        }
        socket->write(part);
        socket->write(jpeg);
        socket->write("\r\n");
        window_bytes += part.size() + jpeg.size() + 2;
      } else if (client.waiting_frame) {
        sendFrame(client);
      }
    }
    for (auto &[socket, client] : clients) {
      if (!client.waiting_frame && !client.streaming && !client.buffer.isEmpty()) {
        processBuffer(client);  // pipelined requests that waited behind a frame
      }
    }
  } else {
    qWarning() << "ScreenStream: JPEG encode failed";
  }
  encoding = false;
}

void ScreenStreamServer::housekeeping() {
  qint64 now = clock.elapsed();
  for (auto &[socket, client] : clients) {
    if (client.waiting_frame && now - client.wait_started_ms > kFrameWaitMs) {
      client.waiting_frame = false;
      client.keep_alive = false;
      sendResponse(client, 503, "text/plain", "no frame\n");
    } else if (!client.streaming && !client.waiting_frame && now - client.last_activity_ms > 15000) {
      closeClient(client);  // idle keep-alive socket
    }
  }
  if (now - window_start_ms >= 2000) {
    kbps = window_bytes * 8.0 / (now - window_start_ms);  // bits per ms == kbit/s
    window_bytes = 0;
    window_start_ms = now;
  }
  updateWatching();
}

void ScreenStreamServer::updateWatching() {
  qint64 now = clock.elapsed();
  bool active = now - last_poll_ms < kIdleStopMs;
  for (const auto &[socket, client] : clients) {
    active |= client.streaming || client.waiting_frame;
  }
  if (active) {
    last_watched_ms = now;
  }
  bool next = active || (watching && now - last_watched_ms < kIdleStopMs);
  if (next != watching) {
    watching = next;
    emit watchingChanged(watching);
  }
}

QByteArray ScreenStreamServer::statsJson() const {
  int streams = 0;
  for (const auto &[socket, client] : clients) {
    streams += client.streaming;
  }
  return QString("{\"watching\":%1,\"clients\":%2,\"streams\":%3,\"frames\":%4,\"fps\":%5,\"grab_ms\":%6,"
                 "\"encode_ms\":%7,\"frame_kb\":%8,\"kbps\":%9,\"width\":%10,\"height\":%11,\"quality\":%12}\n")
    .arg(watching ? "true" : "false").arg(clients.size()).arg(streams).arg(frames_total)
    .arg(fps_avg, 0, 'f', 2).arg(grab_ms_avg, 0, 'f', 2).arg(encode_ms_avg, 0, 'f', 2)
    .arg(latest_jpeg.size() / 1024.0, 0, 'f', 1).arg(kbps, 0, 'f', 0)
    .arg(kWidth).arg(kHeight).arg(kJpegQuality).toUtf8();
}

// ---------------------------------------------------------------- UI thread

ScreenStream::ScreenStream(QWidget *root, QObject *parent) : QObject(parent), root(root) {
  clock.start();

  capture_timer.setTimerType(Qt::PreciseTimer);
  capture_timer.setInterval(1000 / kMaxFps);
  connect(&capture_timer, &QTimer::timeout, this, &ScreenStream::capture);

  param_timer.setInterval(kParamPollMs);
  connect(&param_timer, &QTimer::timeout, this, &ScreenStream::checkParam);
  param_timer.start();
  QTimer::singleShot(0, this, &ScreenStream::checkParam);
}

ScreenStream::~ScreenStream() {
  stopServer();
}

void ScreenStream::checkParam() {
  bool on = screen_stream::enabled();
  if (on && !thread && clock.elapsed() >= retry_after_ms) {
    startServer();
  } else if (!on && thread) {
    stopServer();
  }
}

void ScreenStream::startServer() {
  thread = new QThread();
  thread->setObjectName("ScreenStream");
  server = new ScreenStreamServer();
  server->moveToThread(thread);

  connect(this, &ScreenStream::frameReady, server, &ScreenStreamServer::encodeFrame, Qt::QueuedConnection);
  connect(server, &ScreenStreamServer::watchingChanged, this, &ScreenStream::setWatching, Qt::QueuedConnection);
  connect(server, &ScreenStreamServer::listening, this, [this](bool ok) {
    if (!ok) {
      stopServer();
      retry_after_ms = clock.elapsed() + 10000;
    }
  }, Qt::QueuedConnection);

  thread->start();
  QMetaObject::invokeMethod(server, "start", Qt::QueuedConnection);
}

void ScreenStream::stopServer() {
  capture_timer.stop();
  if (!thread) {
    return;
  }
  QMetaObject::invokeMethod(server, "stop", Qt::BlockingQueuedConnection);
  thread->quit();
  thread->wait();
  delete server;
  delete thread;
  server = nullptr;
  thread = nullptr;
}

void ScreenStream::setWatching(bool watching) {
  if (watching && server) {
    if (!capture_timer.isActive()) {
      capture_timer.setInterval(1000 / kMaxFps);
      grab_ms_avg = 0;
      capture_timer.start();
      capture();
    }
  } else {
    capture_timer.stop();
  }
}

void ScreenStream::capture() {
  // Skip while the previous frame is still being encoded: the worker sets the pace,
  // and the UI thread never queues more than one frame.
  if (!root || !server || server->encoding || root->width() <= 0 || root->height() <= 0) {
    return;
  }

  QElapsedTimer timer;
  timer.start();
  QImage frame(kWidth, kHeight, QImage::Format_RGB32);
  frame.fill(Qt::black);
  {
    // Render the widget tree straight into the half-size image. The camera view (QOpenGLWidget)
    // is read back by Qt and drawn scaled; everything else is painted at half resolution, which
    // is cheaper than painting full size and shrinking afterwards.
    QPainter painter(&frame);
    painter.scale(static_cast<qreal>(kWidth) / root->width(), static_cast<qreal>(kHeight) / root->height());
    root->render(&painter, QPoint(), QRegion(), QWidget::DrawWindowBackground | QWidget::DrawChildren);
  }
  double grab_ms = timer.nsecsElapsed() / 1e6;

  // Keep the UI thread's grab time under kMaxUiDuty: if grabs are slow (e.g. onroad on the
  // device), stretch the capture interval instead of eating into the UI's frame budget.
  grab_ms_avg = grab_ms_avg == 0 ? grab_ms : 0.8 * grab_ms_avg + 0.2 * grab_ms;
  int interval = std::max(1000 / kMaxFps, static_cast<int>(grab_ms_avg / kMaxUiDuty));
  if (std::abs(interval - capture_timer.interval()) >= 10) {
    capture_timer.setInterval(interval);
  }

  server->encoding = true;
  emit frameReady(frame, grab_ms);
}
