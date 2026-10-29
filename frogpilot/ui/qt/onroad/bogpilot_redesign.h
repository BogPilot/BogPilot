#pragma once

// BogPilot onroad redesign (speed row, personality pill, lead lock-on box, shared panel style).
// Plain QPainter helpers with no cereal/UIState dependencies, so they can be rendered offscreen in tests.
// Layout follows the approved 2160x1080 mockup; every coordinate here is in AnnotatedCameraWidget space,
// which is the screen minus the 30 px engage border (screen x/y = value + 30).
// opview (gerrylum, Rick Lan non-commercial licence) was a visual reference only; no code from it is used.

#include <QColor>
#include <QFont>
#include <QLineF>
#include <QPainter>
#include <QPixmap>
#include <QPointF>
#include <QRectF>
#include <QString>
#include <QVector>

// File-backed toggle (the prebuilt params_pyx.so cannot store new Params keys).
// Missing file = ON. "0", "false" or "off" turns the redesign off and restores the previous BogPilot layout.
const char BOGPILOT_REDESIGN_PATH[] = "/data/params_bogpilot/RedesignUI";
bool bogpilotRedesignFileEnabled(const QString &path = BOGPILOT_REDESIGN_PATH);

namespace bogpilot {

// ---- shared style ----
const QColor PANEL(14, 17, 22, 168);
const QColor PANEL_DARK(14, 17, 22, 200);
const QColor SPEED_PANEL(14, 17, 22, 130);   // speed row capsule only: ~23% less opaque than PANEL
const QColor STROKE(255, 255, 255, 30);
const QColor MUTED(255, 255, 255, 150);
const QColor TEXT(255, 255, 255, 240);
const QColor ACTUAL_GREEN(61, 220, 151);
const QColor ARC_PURPLE(150, 100, 255);
const QColor ARC_YELLOW(255, 200, 0);
const QColor ARC_ORANGE(255, 115, 0);
const QColor BADGE_GREEN(31, 150, 80);

QFont font(int px, QFont::Weight weight = QFont::Normal);
void paintPanel(QPainter &p, const QRectF &r, qreal radius, const QColor &fill = PANEL, const QColor &stroke = STROKE, qreal stroke_w = 2.0);
// Draws text horizontally centred on cx with its baseline at y.
void drawCentredBaseline(QPainter &p, qreal cx, qreal baseline, const QString &text);
// "MAX" -> "M A X" (the mockup's letter-spaced small labels)
QString spaced(const QString &s);

// ---- layout (widget space) ---- (round 3: bigger speed row, record button above the personality pill)
constexpr int EDGE = 30;                // left/right inset from the camera-view edge (screen x 60)
constexpr int SPEED_ROW_TOP = 24;       // capsule y 24..200 (screen 54..230)
constexpr int SPEED_ROW_HEIGHT = 176;
constexpr int SPEED_ROW_MIN_RIGHT = 740;  // capsule reaches at least x 740 (screen 770) when a limit is shown
constexpr int CLUSTER_LEFT_FOR_REC = 20;   // = CLUSTER_LEFT (static_assert below)
constexpr int REC_DIAMETER = 124;       // record button disc
constexpr int REC_HIT = 174;            // record button widget (touch target): 25 px beyond the disc's outer ring
constexpr int REC_CX = CLUSTER_LEFT_FOR_REC + 75, REC_CY = 696;   // v5: centred over the personality circle    // record centre (screen 105, 738): above the personality pill, hit box
                                            // left-aligned with the pill (x 0..150)
constexpr int GRAPH_TOP = 228;          // accel graph 30..590 x 228..468 (screen 60..620 x 258..498)
constexpr int GRAPH_WIDTH = 560;
constexpr int GRAPH_HEIGHT = 240;
constexpr int PEDAL_W = 60, PEDAL_H = 120;  // pedal icons (0.625 of the 96x192 pixmaps), under the graph
constexpr int PEDAL_GAP = 18;
constexpr int STATUS_ICON = 132;        // CEM / paused icons, row beside the record button
constexpr int STATUS_X = 196, STATUS_Y = 642, STATUS_STEP = 150;   // screen x 198 / 348 / 498, y 672..804
constexpr int CLUSTER_CY = 900;         // lower-left cluster centre line (screen y 930)
constexpr int CLUSTER_D = 150;          // DM / map button diameter
constexpr int CLUSTER_LEFT = 20;       // v5: 20 px buffer between the personality circle and the engage border
constexpr int CLUSTER_GAP = 6;
static_assert(CLUSTER_LEFT_FOR_REC == CLUSTER_LEFT, "record button must stay centred over the personality circle");
constexpr int PILL_W = 150;             // v5: personality is a single CLUSTER_D circle (was a 411 px pill); DM and map
                                        // slide left, so the map disc now ends x 462 (screen 492)
constexpr int PILL_H = 150;             // = CLUSTER_D
constexpr int PILL_LABEL_SPACE = 40;    // title row above the pill
constexpr int HUD_W = 400;              // developer HUD 1670..2070 x 560..990 (screen 1700..2100 x 590..1020)
constexpr int HUD_H = 430;

// SwapSpeedGraph (file-backed /data/params_bogpilot/SwapSpeedGraph): accel graph on top (y 24..264), speed row below it
// (y 292..468). The stack keeps the same 24..468 footprint, so everything hung below it stays put.
constexpr int STACK_BOTTOM = GRAPH_TOP + GRAPH_HEIGHT;                // 468 in both orders
constexpr int SWAP_GRAPH_TOP = SPEED_ROW_TOP;                          // 24
constexpr int SWAP_SPEED_ROW_TOP = STACK_BOTTOM - SPEED_ROW_HEIGHT;    // 292
const char SWAP_SPEED_GRAPH_PATH[] = "/data/params_bogpilot/SwapSpeedGraph";
inline bool &swapSpeedGraph() { static bool on = false; return on; }   // refreshed by the onroad widget every 2 s
inline int speedRowTop() { return swapSpeedGraph() ? SWAP_SPEED_ROW_TOP : SPEED_ROW_TOP; }
inline int graphTop() { return swapSpeedGraph() ? SWAP_GRAPH_TOP : GRAPH_TOP; }

inline QRectF accelGraphRect() { return QRectF(EDGE, graphTop(), GRAPH_WIDTH, GRAPH_HEIGHT); }

// ---- speed row: MAX | current speed | LIMIT tile (+offset badge); the record button now sits in the lower-left ----
struct SpeedRowInputs {
  QString set_speed = "–";
  QString max_label = "MAX";
  QColor max_color = MUTED;            // label colour (existing MAX colouring)
  QColor set_speed_color = TEXT;       // set speed colour (existing over-limit colouring)
  bool show_max = true;
  QString speed = "0";
  bool show_speed = true;
  QString unit = "MPH";
  bool show_limit = false;
  bool vienna = false;                 // EU round sign instead of the US tile
  QString limit = "–";
  QString limit_label = "LIMIT";
  QString offset;                      // "+5"; empty = no badge
  qreal limit_opacity = 1.0;           // 0.25 while the SLC limit is overridden
  bool traffic_mode = false;           // red capsule outline

  // Curve Speed Controller in the MAX slot (approved design A). Replaces the old popup while the redesign is on.
  bool csc_active = false;             // amber-tinted slot: curve glyph, target number, "from N" label
  bool csc_training = false;           // learning: pulsing amber ring + glyph, MAX keeps the set speed, label below
  bool csc_left = true;                // glyph bends left (mirrored for right curves)
  QString csc_speed;                   // target speed shown in the slot
  QString csc_label;                   // "from 35" (active) or "training" (training)
  qreal csc_pulse = 0.0;               // 0..1, training ring strength
};

struct SpeedRowGeometry {
  QRectF capsule;
  QRectF tile;                         // empty when no limit is shown
  QRectF badge;                        // empty when there is no offset
  QPointF rec_center;                  // unused since round 3 (record button is in the lower-left)
};

SpeedRowGeometry speedRowGeometry(const SpeedRowInputs &in);
// MAX slot of the capsule inset 6 px (widget x 36..200, y 30..158; screen 66..230 x 60..188)
QRectF maxSlotRect();
// Warning-sign style curved arrow centred on c: stem up, quarter bend, arrowhead toward the turn side
void paintCurveGlyph(QPainter &p, const QPointF &c, qreal size, const QColor &color, bool left, qreal stroke);
SpeedRowGeometry paintSpeedRow(QPainter &p, const SpeedRowInputs &in);

// ---- personality: single circle button showing one / two / three bars (Aggressive / Standard / Relaxed,
// cereal LongitudinalPersonality 0, 1, 2); each tap cycles to the next. pill = the circle's square; its label is drawn
// PILL_LABEL_SPACE above it.
void paintPersonalityPill(QPainter &p, const QRectF &pill, int personality, bool traffic_mode, const QString &label);
// Personality to switch to when pos (inside the button) is tapped while `current` is shown: (current + 1) % 3, or -1.
int personalityAt(const QRectF &pill, const QPointF &pos, int current);

// ---- lead box colour by distance (v5) ----
// Time-gap thresholds scaled by the car's speed (v_ego, m/s), each with a distance floor so crawling / stop-and-go
// does not read red: white at >= max(2.0 s * v, 10 m), orange at max(1.0 s * v, 5 m), red at <= max(0.5 s * v, 2 m),
// blended between. Adjacent / non-primary leads use the same thresholds with muted grey / orange / red.
// Below WHITE_BELOW_V (8 mph) the box is always white (muted grey for non-primary).
constexpr float WHITE_BELOW_V = 3.58f;   // m/s, 8 mph
QColor leadDistanceColor(float d_rel, bool adjacent, float v_ego);

// ---- lead lock-on box v3 ----
// box = inner edge of the square. Thin side segments are centred on the 8 px bracket centreline, so the thick corner
// brackets protrude both inside and outside. dist = pill text above the box (empty = no pill).
void paintLeadLock(QPainter &p, const QRectF &box, const QString &dist, const QColor &color = QColor(255, 255, 255, 245));
// Lead labels v6 (approved option B, lead-in-box): the primary lead's speed is a number only (no unit) centred inside
// the box on a dark translucent pill (paintLeadSpeedInBox), sized like the adjacent-lead number, with the closing accent
// bar under the digits. Only the distance tag sits outside the box: below it by default, above it when the tag below
// would reach keepout_top (top of the steering arc's area, i.e. close leads). Either position is also checked against
// the speed row and accel graph (leadTagHudOverlap); when both positions collide, the side with less overlap wins.
// The tag is clamped inside `view`.
struct LeadLabels {
  QString speed;                       // "42" (or "~42" for a camera-only lead), drawn inside the box
  QString dist;                        // "144"
  QString dist_unit;                   // "ft" / "m"
  bool vision_only = false;            // no radar match: shown as "~N", same near-white ink
  QColor accent;                       // bar under the digits: orange / red while closing; invalid = none
};
// Returns true when the distance tag was placed above the box.
bool paintLeadLabels(QPainter &p, const QRectF &box, const LeadLabels &l, const QRectF &view, qreal keepout_top);
// Primary lead speed inside the lock-on box (number only). Scales with the box height (0.42 x, 24-64 px) and shrinks
// so the pill stays inside the corner brackets.
void paintLeadSpeedInBox(QPainter &p, const QRectF &box, const QString &speed, bool vision_only, const QColor &accent);
// Overlap area (px^2) of r with the speed row capsule and the accel graph panel (8 px margin). These are painted after
// the leads, so anything under them is hidden.
qreal leadTagHudOverlap(const QRectF &r);

// ---- LKAS Fault border flash ----
// True for the steering fault Tesla AP1 reports as "LKAS Fault" (steerUnavailable: EPAS EAC_FAULT or the EPB revoking
// EAC) in any of its alert forms: immediateDisable ("TAKE CONTROL IMMEDIATELY" / "LKAS Fault: Restart the Car"),
// permanent ("LKAS Fault: Restart the car to engage") and noEntry ("openpilot Unavailable" / "LKAS Fault: ..."),
// or any other alert whose first or second line says LKAS Fault.
bool isLkasFaultAlert(const QString &alert_type, const QString &text1, const QString &text2 = QString());
// While that alert is on screen the status border hard-alternates red / orange every LKAS_FAULT_FLASH_MS
// (2.5 Hz full cycle), overriding the engaged / disengaged colour.
constexpr int LKAS_FAULT_FLASH_MS = 200;
const QColor LKAS_FAULT_RED(0xe0, 0x1b, 0x24, 0xff);
const QColor LKAS_FAULT_ORANGE(0xff, 0x8c, 0x00, 0xff);
QColor lkasFaultBorderColor(double millis);
// Adjacent-lane lead speed (approved option A, number only): the absolute speed centred inside the muted box on a
// small dark translucent pill, in the box colour lightened for legibility. The number scales with the box height and
// shrinks so the pill always stays inside the brackets of small / distant boxes. The caller hides it below
// ADJ_SPEED_MIN (displayed units).
constexpr int ADJ_SPEED_MIN = 5;
// Placement (approved mockup 2026-10-21, adjacent leads only): the pill stays inside the box unless its in-box rect,
// grown by ADJ_LABEL_MARGIN, meets one of the `keepout` rects (the icons in the driver-side bottom corner, which paint
// over the leads). Then it moves outside the box on the side away from that corner (right; left with rhd),
// ADJ_LABEL_OUTSIDE px past the box edge and centred on the box, and slides further out past every keepout rect it
// still meets. A thin leader line joins it to the box once it sits more than ADJ_LABEL_LEADER_MIN px out.
// `view` (if valid) keeps the outside pill on screen. No keepout = the in-box pill, exactly as before.
constexpr int ADJ_LABEL_MARGIN = 8;
constexpr int ADJ_LABEL_OUTSIDE = 26;
constexpr int ADJ_LABEL_LEADER_MIN = 40;
constexpr int ADJ_LABEL_SLIDE_GAP = 4;
constexpr int ADJ_LABEL_BRACKET = 16;   // corner brackets reach this far outside the box (leader starts there)
struct AdjacentSpeedPlacement {
  QRectF rect;          // pill
  int px = 0;           // font px; 0 = nothing to draw
  bool outside = false;
  bool leader = false;
  QLineF leader_line;
};
// The keepout for the above: record button touch target, no-GPS slot (shown or not), personality / DM [/ map] row with
// its label, the pedal icons (if shown) and the CEM / paused icons as drawn (`status_icons`, already in widget space).
// Fixed parts are mirrored with rhd. `widget_w` = camera widget width.
QVector<QRectF> bottomCornerKeepout(qreal widget_w, bool rhd, bool map_button, bool pedals, const QVector<QRectF> &status_icons);
AdjacentSpeedPlacement placeAdjacentSpeed(const QRectF &box, const QString &speed, const QVector<QRectF> &keepout,
                                          bool rhd = false, const QRectF &view = QRectF());
void paintAdjacentSpeed(QPainter &p, const QRectF &box, const QString &speed, const QColor &box_color,
                        const QVector<QRectF> &keepout = QVector<QRectF>(), bool rhd = false, const QRectF &view = QRectF());
// Box for a lead whose road contact point is (x, y), sized from the distance like the old chevron.
QRectF leadLockBox(const QPointF &contact, float d_rel, qreal max_x, qreal max_y);

// ---- round buttons ----
// Record button: dark REC_DIAMETER disc centred in the touch target `hit`, red dot; while recording a pulsing red ring
// and a red stop square (pulse 0..1).
void paintRecordButton(QPainter &p, const QRectF &hit, bool recording, qreal pulse);
// Dark disc with a centred icon (map / directions button).
void paintIconButton(QPainter &p, const QRectF &r, const QPixmap &icon, qreal opacity);
// No-GPS indicator (replaces the suppressed noGps banner): record-button style disc, white satellite always lit,
// red circle-slash drawn only while slash_on. Display only.
constexpr int NOGPS_CX = CLUSTER_LEFT + PILL_W + CLUSTER_GAP + CLUSTER_D / 2;   // 251: centred over the DM circle
constexpr int NOGPS_CY = REC_CY;                                                 // 696: record button row
constexpr qint64 NOGPS_DEBOUNCE_MS = 5000;
void paintNoGpsIndicator(QPainter &p, const QPointF &c, bool slash_on);

// ---- alert banner ----
// v5 (mockup 5d): banner for small / mid alerts in a widget of size `widget` (widget space = screen minus the 30 px
// border). Horizontal space runs from ALERT_GAP right of the record button to a right margin of one record-button width;
// vertically it sits ALERT_GAP below the accel graph and ALERT_GAP above the personality / DM / map row. Mid alerts fill
// that space; small alerts are a shorter banner centred in it. Full width minus 40 px margins when narrower.
constexpr int ALERT_GAP = 36;
constexpr int ALERT_RIGHT_MARGIN = 150;   // record button width rule (kept at the pre-v5 150 px)
QRect alertBannerRect(const QSize &widget, bool mid);
void paintAlertBanner(QPainter &p, const QRect &r, bool mid, const QString &text1, const QString &text2, const QColor &fill);

}  // namespace bogpilot
