// SAFETY_TESLA_PREAP (37): Pre-Autopilot Tesla Model S (2012-2014), no AP ECU.
//
// MIT License. Ported to this panda's (pre-opendbc) safety API by BogPilot from
// NotAutopilot opendbc safety/modes/tesla_preap.h (github.com/NotAutopilot/opendbc,
// MIT, commits cc39c7f / 21a45cf), which in turn descends from Tinkla
// (BogGyver/panda, MIT, tesla_unity_betaC3, safety_tesla.h "PreAP has no relay")
// and NotAutopilot panda 282fae7. Copyright (c) the openpilot, Tinkla and
// NotAutopilot authors. Mode number 37 and the flag bits match NAP so a NAP
// panda and a BogPilot panda agree on what the host asks for.
//
// Every CAN ID here is taken from the NAP file above (and tesla_can.dbc where
// noted); none are new.
//
// Differences from NAP (deliberately narrower, phase 1):
//   - TX whitelist: 0x488, 0x214, 0x551 (flag-gated), 0x560 (consumed, never
//     put on the car). NAP's 0x2B9 DAS_control, 0x45 stalk spoof,
//     0x3E9 body controls and 0x641 radar UDS are NOT allowed.
//   - Steering uses steer_angle_cmd_checks with the shared Tesla rate table
//     (this API has no _vm curvature check) plus a 360 deg magnitude clamp.
//   - Brake press disengages (generic_rx_checks), NAP keeps lateral on brake.
//   - Engage is a rising edge of the cruise stalk pull (lever 2), so a later
//     pull re-engages after a gear/door/hands-on drop.
//   - Pedal calibration mode (flag 64) is not ported: init returns an empty
//     TX list (silent) if it is requested.
//   - Always-on lateral is ignored: angle control needs controls_allowed.
//
// Pedal longitudinal (flag 1, BogPilot nap-pedal). Ported from NotAutopilot
// panda 282fae7 / napdbc f1a756d tesla_preap.h (650 raw gas threshold,
// ENABLE=0 release rule) and Tinkla 0d99a1b (BogGyver/openpilot, MIT:
// panda/board/pedal/main.c firmware protocol, safety_tesla.h stalk cancel).
// Stricter than NAP, which checked neither the pedal checksum, the counter,
// the command size nor the stock-cruise state:
//   - 0x552 GAS_SENSOR only on the configured pedal bus, with RX checksum
//     (byte 5 additive) and counter (byte 4 low nibble) checks.
//   - 0x551 ENABLE=1 needs longitudinal allowed, no brake, a fresh (<100 ms)
//     GAS_SENSOR with STATE 0 (NO_FAULT), stock cruise not engaged, a valid
//     checksum, a sequential IDX, GAS_COMMAND <= a speed-dependent cap and
//     GAS_COMMAND2 consistent with GAS_COMMAND.
//   - 0x551 ENABLE=0 must carry zero gas (what the firmware needs to clear
//     FAULT_INVALID); NAP allowed up to 500 raw.
//   - 0x45 STW_ACTN_RQ, bus 0, only as a stock-cruise CANCEL (lever 1) that
//     copies the last real stalk frame, with counter last+1 and a valid CRC,
//     at most one per real stalk frame, only while stock cruise is engaged
//     and openpilot is engaged. Every other 0x45 is blocked.
// Addresses: 0x551 GAS_COMMAND / 0x552 GAS_SENSOR (BO_ 1361/1362 in Tinkla
// opendbc/tesla_can.dbc and BogPilot opendbc/tesla_preap_pedal.dbc), 0x45
// STW_ACTN_RQ (BO_ 69) and 0x368 DI_state (BO_ 872) in opendbc/tesla_can.dbc.

#define PREAP_GET_BYTES_04(msg) ((msg)->data[0] | ((msg)->data[1] << 8) | ((msg)->data[2] << 16) | ((msg)->data[3] << 24))
#define PREAP_GET_BYTES_48(msg) ((msg)->data[4] | ((msg)->data[5] << 8) | ((msg)->data[6] << 16) | ((msg)->data[7] << 24))
#define PREAP_WORD_TO_BYTES(dst8, src32) 0[dst8] = ((src32) & 0xFFU); 1[dst8] = (((src32) >> 8U) & 0xFFU); 2[dst8] = (((src32) >> 16U) & 0xFFU); 3[dst8] = (((src32) >> 24U) & 0xFFU)

// defined in drivers/can_common.h, included after safety.h
void can_send(CANPacket_t *to_push, uint8_t bus_number, bool skip_tx_hook);
void can_set_checksum(CANPacket_t *packet);

// Flag bits: NAP tesla_preap.h / safety/tests/test_tesla_preap.py
#define PREAP_FLAG_ENABLE_PEDAL          1U
#define PREAP_FLAG_RADAR_EMULATION       2U
#define PREAP_FLAG_RADAR_BEHIND_NOSECONE 4U   // unused by NAP firmware too
#define PREAP_FLAG_PEDAL_BUS_ZERO        32U
#define PREAP_FLAG_PEDAL_CALIBRATION     64U

#define PREAP_HANDS_ON_DISENGAGE_LEVEL 2
#define PREAP_CANCEL_ECHO_WINDOW_US 600000U
#define PREAP_MAX_ANGLE 3600            // 0.1 deg; NAP: "360 deg, EPAS faults above this"
#define PREAP_PEDAL_GAS_PRESSED 650     // NAP: raw threshold from Pre-AP drive data
#define PREAP_PEDAL_SENSOR_TIMEOUT_US 100000U  // 5 missed frames at 50 Hz
#define PREAP_STALK_FRESH_US 250000U    // last real 0x45 must be this recent to copy it
#define PREAP_PEDAL_CMD2_TOL 4          // |2*GAS_COMMAND2 - GAS_COMMAND| raw
// GAS_COMMAND raw cap vs speed (m/s). raw = (phys + 22.85856576) / 0.0507968128;
// phys 60/72/95 at 0/12/40 m/s, 5-10 above the host's NAP PEDAL_MAX_VALUES
// (50..90 over 0..40 m/s) so the host limit always binds first.
const struct lookup_t PREAP_PEDAL_MAX_GAS = {
  {0., 12., 40.},
  {1631., 1867., 2320.},
};
#define PREAP_KPH_TO_MS 0.277778f

#define PREAP_RADAR_VIN_ADDR 0x560U     // host -> panda donor config (NAP / Tinkla 0.6.6)

const SteeringLimits TESLA_PREAP_STEERING_LIMITS = {
  .angle_deg_to_can = 10,
  .angle_rate_up_lookup = {
    {0., 5., 15.},
    {10., 1.6, .3}
  },
  .angle_rate_down_lookup = {
    {0., 5., 15.},
    {10., 7.0, .8}
  },
};

static bool preap_enable_pedal = false;
static bool preap_radar_emulation = false;
static bool preap_calibration_requested = false;
static uint8_t preap_pedal_bus = 2U;

static int preap_gear = 4;          // DI_gear 4 = D; init to D like NAP
static int preap_gear_prev = 4;
static bool preap_doors_open = false;
static bool preap_di_brake_pressed = false;
static bool preap_brake_message_pressed = false;
static bool preap_steering_disengage = false;
static bool preap_steering_disengage_prev = false;
static bool preap_stalk_pull_prev = false;
static uint32_t preap_last_stalk_engage_us = 0U;

// pedal longitudinal state
static int preap_pedal_state = 4;            // GAS_SENSOR STATE, 4 = FAULT_STARTUP
static bool preap_pedal_seen = false;
static uint32_t preap_pedal_last_us = 0U;
static int preap_pedal_tx_idx_prev = -1;
static int preap_di_cruise_state = 0;        // DI_state DI_cruiseState
static bool preap_di_state_seen = false;
static uint32_t preap_di_state_last_us = 0U;
static uint8_t preap_stalk_last[8];          // last real STW_ACTN_RQ (bus 0)
static bool preap_stalk_seen = false;
static uint32_t preap_stalk_last_us = 0U;
static bool preap_stalk_spoof_used = true;   // one cancel per real stalk frame

static int preap_radar_epas_type = 0;
static int preap_radar_position = 0;
static uint8_t preap_radar_vin[17];
static uint8_t preap_radar_vin_complete = 0U;
static bool preap_radar_should_send = false;

static bool preap_radar_ready(void) {
  // Tinkla 0.6.6 / NAP: no radar-bus GTW frames until the whole 0x560 config arrived.
  return (preap_radar_vin_complete == 7U) && preap_radar_should_send;
}

static bool preap_radar_donor_active(void) {
  bool active = false;
  if (preap_radar_vin_complete == 7U) {
    for (int i = 0; i < 17; i++) {
      if ((preap_radar_vin[i] != 0U) && (preap_radar_vin[i] != (uint8_t)' ')) {
        active = true;
      }
    }
  }
  return active;
}

static void preap_apply_radar_vin_msg(const CANPacket_t *msg) {
  // Layout: NAP preap/teslacan.py create_radar_vin_msg (Tinkla 0.6.6).
  const int rec = msg->data[0];
  if (rec == 0) {
    preap_radar_should_send = (msg->data[2] & 0x01U) != 0U;
    preap_radar_position = (msg->data[2] >> 1) & 0x03;
    preap_radar_epas_type = (msg->data[2] >> 3) & 0x07;
    for (int i = 0; i < 3; i++) {
      preap_radar_vin[i] = msg->data[5 + i];
    }
    preap_radar_vin_complete |= 1U;
  } else if (rec == 1) {
    for (int i = 0; i < 7; i++) {
      preap_radar_vin[3 + i] = msg->data[1 + i];
    }
    preap_radar_vin_complete |= 2U;
  } else if (rec == 2) {
    for (int i = 0; i < 7; i++) {
      preap_radar_vin[10 + i] = msg->data[1 + i];
    }
    preap_radar_vin_complete |= 4U;
  } else {
  }
}

static uint32_t preap_radar_vin_char(int pos, int shift) {
  return ((uint32_t)preap_radar_vin[pos]) << (shift * 8);
}

// CRC-8 (poly 0x1D) for STW_ANGLHP_STAT -> 0x199 (NAP / Tinkla)
static const int preap_crc_lookup[256] = {
  0x00, 0x1D, 0x3A, 0x27, 0x74, 0x69, 0x4E, 0x53, 0xE8, 0xF5, 0xD2, 0xCF, 0x9C, 0x81, 0xA6, 0xBB,
  0xCD, 0xD0, 0xF7, 0xEA, 0xB9, 0xA4, 0x83, 0x9E, 0x25, 0x38, 0x1F, 0x02, 0x51, 0x4C, 0x6B, 0x76,
  0x87, 0x9A, 0xBD, 0xA0, 0xF3, 0xEE, 0xC9, 0xD4, 0x6F, 0x72, 0x55, 0x48, 0x1B, 0x06, 0x21, 0x3C,
  0x4A, 0x57, 0x70, 0x6D, 0x3E, 0x23, 0x04, 0x19, 0xA2, 0xBF, 0x98, 0x85, 0xD6, 0xCB, 0xEC, 0xF1,
  0x13, 0x0E, 0x29, 0x34, 0x67, 0x7A, 0x5D, 0x40, 0xFB, 0xE6, 0xC1, 0xDC, 0x8F, 0x92, 0xB5, 0xA8,
  0xDE, 0xC3, 0xE4, 0xF9, 0xAA, 0xB7, 0x90, 0x8D, 0x36, 0x2B, 0x0C, 0x11, 0x42, 0x5F, 0x78, 0x65,
  0x94, 0x89, 0xAE, 0xB3, 0xE0, 0xFD, 0xDA, 0xC7, 0x7C, 0x61, 0x46, 0x5B, 0x08, 0x15, 0x32, 0x2F,
  0x59, 0x44, 0x63, 0x7E, 0x2D, 0x30, 0x17, 0x0A, 0xB1, 0xAC, 0x8B, 0x96, 0xC5, 0xD8, 0xFF, 0xE2,
  0x26, 0x3B, 0x1C, 0x01, 0x52, 0x4F, 0x68, 0x75, 0xCE, 0xD3, 0xF4, 0xE9, 0xBA, 0xA7, 0x80, 0x9D,
  0xEB, 0xF6, 0xD1, 0xCC, 0x9F, 0x82, 0xA5, 0xB8, 0x03, 0x1E, 0x39, 0x24, 0x77, 0x6A, 0x4D, 0x50,
  0xA1, 0xBC, 0x9B, 0x86, 0xD5, 0xC8, 0xEF, 0xF2, 0x49, 0x54, 0x73, 0x6E, 0x3D, 0x20, 0x07, 0x1A,
  0x6C, 0x71, 0x56, 0x4B, 0x18, 0x05, 0x22, 0x3F, 0x84, 0x99, 0xBE, 0xA3, 0xF0, 0xED, 0xCA, 0xD7,
  0x35, 0x28, 0x0F, 0x12, 0x41, 0x5C, 0x7B, 0x66, 0xDD, 0xC0, 0xE7, 0xFA, 0xA9, 0xB4, 0x93, 0x8E,
  0xF8, 0xE5, 0xC2, 0xDF, 0x8C, 0x91, 0xB6, 0xAB, 0x10, 0x0D, 0x2A, 0x37, 0x64, 0x79, 0x5E, 0x43,
  0xB2, 0xAF, 0x88, 0x95, 0xC6, 0xDB, 0xFC, 0xE1, 0x5A, 0x47, 0x60, 0x7D, 0x2E, 0x33, 0x14, 0x09,
  0x7F, 0x62, 0x45, 0x58, 0x0B, 0x16, 0x31, 0x2C, 0x97, 0x8A, 0xAD, 0xB0, 0xE3, 0xFE, 0xD9, 0xC4
};
static int preap_compute_crc8(uint32_t lo, uint32_t hi, int msg_len) {
  int crc = 0xFF;
  for (int x = 0; x < msg_len; x++) {
    int v = (x <= 3) ? ((lo >> (x * 8)) & 0xFF) : ((hi >> ((x - 4) * 8)) & 0xFF);
    crc = preap_crc_lookup[crc ^ v];
  }
  return crc ^ 0xFF;
}

static void preap_send_radar(CANPacket_t *pkt) {
  can_set_checksum(pkt);
  can_send(pkt, 1U, true);
}

static void preap_radar_readdr(const CANPacket_t *src, uint16_t new_addr) {
  CANPacket_t pkt = {0};
  pkt.extended = src->extended;
  pkt.bus = 1U;
  pkt.addr = new_addr;
  pkt.data_len_code = src->data_len_code;
  for (int i = 0; i < GET_LEN(src); i++) {
    pkt.data[i] = src->data[i];
  }
  preap_send_radar(&pkt);
}

// GTW emulation, bus 0 -> bus 1, for a Bosch radar on a car with no gateway
// support for it. Addresses and bit patching: NAP tesla_preap.h
// tesla_preap_gtw_emulation (Tinkla 0.6.6). Only after the host sent a full
// 0x560 config with use-radar set and the RADAR_EMULATION flag is on.
static void tesla_preap_gtw_emulation(const CANPacket_t *to_fwd) {
  int addr = GET_ADDR(to_fwd);
  if ((GET_BUS(to_fwd) != 0U) || !preap_radar_emulation || !preap_radar_ready() || (GET_LEN(to_fwd) > 8U)) {
    return;
  }

  switch (addr) {
    case 0x45:  preap_radar_readdr(to_fwd, 0x219); break;  // STW_ACTN_RQ
    case 0x108: preap_radar_readdr(to_fwd, 0x109); break;  // DI_torque1
    case 0x145: preap_radar_readdr(to_fwd, 0x149); break;  // ESP_145h
    case 0x20A: preap_radar_readdr(to_fwd, 0x159); break;  // BrakeMessage -> ESP_C
    case 0x308: preap_radar_readdr(to_fwd, 0x209); break;  // GTW_odo
    case 0x30A: preap_radar_readdr(to_fwd, 0x2D9); break;  // BC_status
    default: break;
  }

  if (addr == 0x405) {
    // VIN feed -> 0x2B9 on the radar bus; donor VIN substituted when configured
    CANPacket_t pkt = {0};
    pkt.extended = to_fwd->extended; pkt.bus = 1U; pkt.addr = 0x2B9; pkt.data_len_code = to_fwd->data_len_code;
    uint32_t lo = PREAP_GET_BYTES_04(to_fwd);
    uint32_t hi = PREAP_GET_BYTES_48(to_fwd);
    if (preap_radar_donor_active() && ((lo & 0x10U) == 0x10U)) {
      const int rec = (int)(lo & 0xFFU);
      if (rec == 0x10) {
        lo = (uint32_t)rec;
        hi = preap_radar_vin_char(0, 1) | preap_radar_vin_char(1, 2) | preap_radar_vin_char(2, 3);
      } else if (rec == 0x11) {
        lo = (uint32_t)rec | preap_radar_vin_char(3, 1) | preap_radar_vin_char(4, 2) | preap_radar_vin_char(5, 3);
        hi = preap_radar_vin_char(6, 0) | preap_radar_vin_char(7, 1) | preap_radar_vin_char(8, 2) | preap_radar_vin_char(9, 3);
      } else if (rec == 0x12) {
        lo = (uint32_t)rec | preap_radar_vin_char(10, 1) | preap_radar_vin_char(11, 2) | preap_radar_vin_char(12, 3);
        hi = preap_radar_vin_char(13, 0) | preap_radar_vin_char(14, 1) | preap_radar_vin_char(15, 2) | preap_radar_vin_char(16, 3);
      } else {
      }
    }
    PREAP_WORD_TO_BYTES(&pkt.data[0], lo);
    PREAP_WORD_TO_BYTES(&pkt.data[4], hi);
    preap_send_radar(&pkt);
  }

  if (addr == 0x398) {
    // GTW_carConfig -> 0x2A9: country US, radar type Bosch, position, EPAS type
    CANPacket_t pkt = {0};
    pkt.extended = to_fwd->extended; pkt.bus = 1U; pkt.addr = 0x2A9; pkt.data_len_code = to_fwd->data_len_code;
    uint32_t lo = PREAP_GET_BYTES_04(to_fwd);
    uint32_t hi = PREAP_GET_BYTES_48(to_fwd);
    lo = (lo & 0xFFFFF33FU) | 0x100U | 0x440U;
    hi = (hi & 0xCFFF0F0FU) | 0x10000000U | ((uint32_t)preap_radar_position << 4) | ((uint32_t)preap_radar_epas_type << 12);
    // NAP/Tinkla 0.6.6: with a donor VIN, declare 4WD when VIN char 8 is '2' or '4'
    if (preap_radar_donor_active()) {
      const uint8_t drive = preap_radar_vin[7];
      if ((drive == (uint8_t)'2') || (drive == (uint8_t)'4')) {
        lo |= 0x08U;
      }
    }
    PREAP_WORD_TO_BYTES(&pkt.data[0], lo);
    PREAP_WORD_TO_BYTES(&pkt.data[4], hi);
    preap_send_radar(&pkt);
  }

  if (addr == 0x0E) {
    // STW_ANGLHP_STAT -> 0x199 with SNA replacement
    CANPacket_t pkt = {0};
    pkt.extended = to_fwd->extended; pkt.bus = 1U; pkt.addr = 0x199; pkt.data_len_code = to_fwd->data_len_code;
    uint32_t lo = PREAP_GET_BYTES_04(to_fwd);
    uint32_t hi = PREAP_GET_BYTES_48(to_fwd);
    if (((lo >> 16) & 0xFF3FU) == 0xFF3FU) {
      lo = (lo & 0x00C0FFFFU) | (0x0020U << 16);
      hi = (hi & 0x00FFFFF0U) | 0x00000004U;
      int crc = preap_compute_crc8(lo, hi, 7);
      hi = hi | ((uint32_t)crc << 24);
    }
    PREAP_WORD_TO_BYTES(&pkt.data[0], lo);
    PREAP_WORD_TO_BYTES(&pkt.data[4], hi);
    preap_send_radar(&pkt);
  }

  if (addr == 0x115) {
    // ESP_115h -> 0x129, plus synthetic DI_espControl 0x1A9
    preap_radar_readdr(to_fwd, 0x129);
    uint32_t hi_src = PREAP_GET_BYTES_48(to_fwd);
    int counter = ((hi_src & 0xF0U) >> 4) & 0x0F;
    uint32_t syn_lo = 0x000C0000U | ((uint32_t)counter << 28);
    int cksm = (0x38 + 0x0C + (counter << 4)) & 0xFF;
    CANPacket_t pkt = {0};
    pkt.bus = 1U; pkt.addr = 0x1A9; pkt.data_len_code = 5U;
    PREAP_WORD_TO_BYTES(&pkt.data[0], syn_lo);
    PREAP_WORD_TO_BYTES(&pkt.data[4], (uint32_t)cksm);
    preap_send_radar(&pkt);
  }

  if (addr == 0x118) {
    // DI_torque2 -> 0x119, plus synthetic ESP_wheelSpeeds 0x169
    preap_radar_readdr(to_fwd, 0x119);
    uint32_t lo = PREAP_GET_BYTES_04(to_fwd);
    int ws_counter = PREAP_GET_BYTES_48(to_fwd) & 0x0F;
    int raw_speed = (int)((0xFFF0000U & lo) >> 16);
    int speed;
    if (raw_speed == 0xFFF) {
      speed = 0x1FFF;
    } else {
      int mph_x100 = (raw_speed * 5) - 2500;
      int kph_x100 = (mph_x100 * 1609) / 1000;
      speed = (kph_x100 < 0) ? 0 : ((kph_x100 / 4) & 0x1FFF);
    }
    uint32_t ws_lo = (uint32_t)(speed | (speed << 13) | (speed << 26));
    uint32_t ws_hi = (uint32_t)((speed >> 6) | (speed << 7) | (ws_counter << 20)) & 0x00FFFFFFU;
    int ws_cksm = 0x76;
    ws_cksm = (ws_cksm + (int)(ws_lo & 0xFFU) + (int)((ws_lo >> 8) & 0xFFU) + (int)((ws_lo >> 16) & 0xFFU) + (int)((ws_lo >> 24) & 0xFFU)) & 0xFF;
    ws_cksm = (ws_cksm + (int)(ws_hi & 0xFFU) + (int)((ws_hi >> 8) & 0xFFU) + (int)((ws_hi >> 16) & 0xFFU)) & 0xFF;
    ws_hi = ws_hi | ((uint32_t)ws_cksm << 24);
    CANPacket_t pkt = {0};
    pkt.bus = 1U; pkt.addr = 0x169; pkt.data_len_code = 8U;
    PREAP_WORD_TO_BYTES(&pkt.data[0], ws_lo);
    PREAP_WORD_TO_BYTES(&pkt.data[4], ws_hi);
    preap_send_radar(&pkt);
  }
}

// Comma Pedal checksum (Tinkla 0d99a1b panda/board/pedal/main.c pedal_checksum,
// NAP teslacan.py pedal_checksum): address bytes + data bytes 0-4, byte 5.
static uint8_t preap_pedal_checksum(const CANPacket_t *msg) {
  const int addr = GET_ADDR(msg);
  int sum = (addr & 0xFF) + ((addr >> 8) & 0xFF);
  for (int i = 0; i < 5; i++) {
    sum += msg->data[i];
  }
  return (uint8_t)(sum & 0xFF);
}

static uint32_t tesla_preap_get_checksum(const CANPacket_t *to_push) {
  return (uint32_t)to_push->data[5];
}

static uint32_t tesla_preap_compute_checksum(const CANPacket_t *to_push) {
  return (uint32_t)preap_pedal_checksum(to_push);
}

static uint8_t tesla_preap_get_counter(const CANPacket_t *to_push) {
  // GAS_SENSOR IDX 35|4 = byte 4 low nibble (firmware: dat[4] = (state << 4) | idx)
  return (uint8_t)(to_push->data[4] & 0x0FU);
}

// DI_cruiseState (tesla_can.dbc VAL_ 872): 0 OFF, 1 STANDBY, 5 FAULT mean stock
// cruise is not driving the car. Anything else (ENABLED, STANDSTILL, OVERRIDE,
// PRE_FAULT, PRE_CANCEL, unknown) or no recent DI_state counts as engaged.
static bool preap_stock_cruise_engaged(void) {
  bool engaged = true;
  uint32_t elapsed = get_ts_elapsed(microsecond_timer_get(), preap_di_state_last_us);
  if (preap_di_state_seen && (elapsed < 500000U)) {
    engaged = !((preap_di_cruise_state == 0) || (preap_di_cruise_state == 1) || (preap_di_cruise_state == 5));
  }
  return engaged;
}

// Cancel worth sending: ENABLED, STANDSTILL or OVERRIDE.
static bool preap_stock_cruise_cancelable(void) {
  uint32_t elapsed = get_ts_elapsed(microsecond_timer_get(), preap_di_state_last_us);
  return preap_di_state_seen && (elapsed < 500000U) &&
         (preap_di_cruise_state >= 2) && (preap_di_cruise_state <= 4);
}

static bool preap_pedal_healthy(void) {
  uint32_t elapsed = get_ts_elapsed(microsecond_timer_get(), preap_pedal_last_us);
  return preap_pedal_seen && (preap_pedal_state == 0) && (elapsed < PREAP_PEDAL_SENSOR_TIMEOUT_US);
}

static bool preap_pedal_cmd_allowed(const CANPacket_t *to_send) {
  bool ok = true;
  const int idx = to_send->data[4] & 0x0F;
  const bool enable = (to_send->data[4] & 0x80U) != 0U;
  const int raw1 = (to_send->data[0] << 8) | to_send->data[1];
  const int raw2 = (to_send->data[2] << 8) | to_send->data[3];

  // the firmware drops non-sequential IDX; track every attempt so a single
  // blocked frame does not lock the stream out
  if ((preap_pedal_tx_idx_prev >= 0) && (idx != ((preap_pedal_tx_idx_prev + 1) % 16))) {
    ok = false;
  }
  preap_pedal_tx_idx_prev = idx;

  if (to_send->data[5] != preap_pedal_checksum(to_send)) {
    ok = false;
  }
  if ((to_send->data[4] & 0x70U) != 0U) {
    ok = false;  // unused bits must be zero
  }

  if (enable) {
    const int cap = (int)interpolate(PREAP_PEDAL_MAX_GAS, (float)vehicle_speed.min / VEHICLE_SPEED_FACTOR);
    if (!get_longitudinal_allowed() || brake_pressed || preap_di_brake_pressed || preap_brake_message_pressed) {
      ok = false;
    }
    if (!preap_pedal_healthy() || preap_stock_cruise_engaged()) {
      ok = false;
    }
    if (raw1 > cap) {
      ok = false;
    }
    if (ABS((2 * raw2) - raw1) > PREAP_PEDAL_CMD2_TOL) {
      ok = false;
    }
  } else {
    if ((raw1 != 0) || (raw2 != 0)) {
      ok = false;
    }
  }
  return ok;
}

// Stock-cruise cancel spoof (Tinkla 0d99a1b safety_tesla.h fake stalk cancel,
// NAP stock_cc_spoofer.py frame layout).
static bool preap_stalk_cancel_allowed(const CANPacket_t *to_send) {
  bool ok = preap_enable_pedal && controls_allowed && preap_stalk_seen && !preap_stalk_spoof_used;
  ok = ok && (GET_BUS(to_send) == 0U) && (GET_LEN(to_send) == 8U);
  ok = ok && (get_ts_elapsed(microsecond_timer_get(), preap_stalk_last_us) < PREAP_STALK_FRESH_US);
  ok = ok && preap_stock_cruise_cancelable();
  // byte 0: lever 1 (FWD / cancel), VSL_Enbl_Rq copied, SpdCtrlLvrStat_Inv 0 (NAP)
  ok = ok && ((to_send->data[0] & 0x3FU) == 1U);
  ok = ok && ((to_send->data[0] & 0x40U) == (preap_stalk_last[0] & 0x40U));
  ok = ok && ((to_send->data[0] & 0x80U) == 0U);
  // bytes 1-5 and byte 6 bits 0-3 (DTR, switches, wipers) copied unchanged
  for (int i = 1; i < 6; i++) {
    ok = ok && (to_send->data[i] == preap_stalk_last[i]);
  }
  ok = ok && ((to_send->data[6] & 0x0FU) == (preap_stalk_last[6] & 0x0FU));
  // MC_STW_ACTN_RQ 52|4 = last real + 1
  ok = ok && ((to_send->data[6] >> 4) == (((preap_stalk_last[6] >> 4) + 1U) & 0x0FU));
  // CRC_STW_ACTN_RQ 56|8: CRC-8 poly 0x1D over bytes 0-6
  uint32_t lo = PREAP_GET_BYTES_04(to_send);
  uint32_t hi = PREAP_GET_BYTES_48(to_send);
  ok = ok && (preap_compute_crc8(lo, hi, 7) == (int)to_send->data[7]);
  return ok;
}

static void preap_disengage(void) {
  controls_allowed = false;
  cruise_engaged_prev = false;
}

static void tesla_preap_rx_hook(const CANPacket_t *to_push) {
  const int bus = GET_BUS(to_push);
  const int addr = GET_ADDR(to_push);

  // Comma Pedal GAS_SENSOR 0x552, only on the configured pedal bus and only when
  // the pedal flag is set. Checksum and counter were checked by rx_msg_safety_check.
  // INTERCEPTOR_GAS 7|16 is the driver's pedal ADC; STATE 39|4 = byte 4 high nibble.
  if (preap_enable_pedal && (addr == 0x552) && (bus == (int)preap_pedal_bus) && (GET_LEN(to_push) == 6U)) {
    int pedal_val = (to_push->data[0] << 8) | to_push->data[1];
    gas_pressed = pedal_val > PREAP_PEDAL_GAS_PRESSED;
    preap_pedal_state = to_push->data[4] >> 4;
    preap_pedal_seen = true;
    preap_pedal_last_us = microsecond_timer_get();
  }

  if (bus == 0) {
    if (addr == 0x370) {
      // EPAS_sysStatus: internalSAS 0.1 deg, handsOnLevel, eacStatus, eacErrorCode (tesla_can.dbc)
      const int angle_meas_new = (((to_push->data[4] & 0x3FU) << 8) | to_push->data[5]) - 8192U;
      update_sample(&angle_meas, angle_meas_new);
      const int hands_on_level = to_push->data[4] >> 6;
      const int eac_status = to_push->data[6] >> 5;
      const int eac_error_code = to_push->data[2] >> 4;
      // NAP: codes 6-9 with eacStatus 0 mean the EPAS rejected the request
      const bool epas_rejecting = (eac_status == 0) && (eac_error_code >= 6) && (eac_error_code <= 9);
      preap_steering_disengage = (hands_on_level >= PREAP_HANDS_ON_DISENGAGE_LEVEL) || epas_rejecting;
      if (preap_steering_disengage && !preap_steering_disengage_prev) {
        preap_disengage();
      }
      preap_steering_disengage_prev = preap_steering_disengage;
    }

    if (addr == 0x155) {
      // ESP_B vehicle speed, 0.01 kph in bytes 5-6 (NAP)
      float speed = (((to_push->data[5] << 8) | to_push->data[6]) * 0.01f) * PREAP_KPH_TO_MS;
      UPDATE_VEHICLE_SPEED(speed);
      vehicle_moving = speed > (0.5f * PREAP_KPH_TO_MS);
    }

    if ((addr == 0x108) && !preap_enable_pedal) {
      // DI_torque1 accelerator pedal
      gas_pressed = to_push->data[6] != 0U;
    }

    if (addr == 0x20A) {
      // BrakeMessage driverBrakeStatus == 2 APPLIED (NAP)
      preap_brake_message_pressed = ((to_push->data[0] >> 2) & 0x03U) == 2U;
    }

    if (addr == 0x118) {
      // DI_torque2: DI_brakePedal, DI_gear (NAP)
      preap_di_brake_pressed = ((to_push->data[1] >> 7) & 0x01U) != 0U;
      preap_gear = (to_push->data[1] >> 4) & 0x07;
      if (preap_gear != 4) {
        preap_disengage();
      }
      preap_gear_prev = preap_gear;
    }

    if (addr == 0x318) {
      // GTW_carState doors (NAP)
      int d_fl = (to_push->data[1] >> 4) & 0x03;
      int d_fr = (to_push->data[1] >> 6) & 0x03;
      int d_rl = (to_push->data[2] >> 6) & 0x03;
      int d_rr = (to_push->data[3] >> 5) & 0x03;
      int d_ft = (to_push->data[6] >> 2) & 0x03;
      int d_tr = (to_push->data[5] >> 6) & 0x03;
      preap_doors_open = (d_fl == 1) || (d_fr == 1) || (d_rl == 1) || (d_rr == 1) || (d_ft == 1) || (d_tr == 1);
      if (preap_doors_open) {
        preap_disengage();
      }
    }

    if (addr == 0x368) {
      // DI_state DI_cruiseState 12|4@1+ (tesla_can.dbc BO_ 872)
      preap_di_cruise_state = to_push->data[1] >> 4;
      preap_di_state_seen = true;
      preap_di_state_last_us = microsecond_timer_get();
    }

    if (addr == 0x45) {
      // keep the last real stalk frame for the cancel spoof check
      for (int i = 0; i < 8; i++) {
        preap_stalk_last[i] = to_push->data[i];
      }
      preap_stalk_seen = true;
      preap_stalk_last_us = microsecond_timer_get();
      preap_stalk_spoof_used = false;

      // STW_ACTN_RQ SpdCtrlLvr_Stat: 2 = pull (engage), 1 = push (cancel) (NAP)
      const int lever = to_push->data[0] & 0x3FU;
      const bool pull = lever == 2;
      if (pull && !preap_stalk_pull_prev) {
        if ((preap_gear == 4) && !preap_doors_open && !preap_steering_disengage &&
            !preap_di_brake_pressed && !preap_brake_message_pressed) {
          controls_allowed = true;
          preap_last_stalk_engage_us = microsecond_timer_get();
        }
      } else if (lever == 1) {
        uint32_t elapsed = get_ts_elapsed(microsecond_timer_get(), preap_last_stalk_engage_us);
        if (elapsed > PREAP_CANCEL_ECHO_WINDOW_US) {
          preap_disengage();
        }
      } else {
      }
      preap_stalk_pull_prev = pull;
    }

    brake_pressed = preap_di_brake_pressed || preap_brake_message_pressed;
  }

  tesla_preap_gtw_emulation(to_push);

  if (preap_calibration_requested) {
    controls_allowed = false;
  }

  // no AP ECU and no relay on Pre-AP: never flag a relay malfunction
  generic_rx_checks(false);

  // always-on lateral gives no extra permission in this mode
  acc_main_on = controls_allowed;
  lkas_on = false;
}

static bool tesla_preap_tx_hook(const CANPacket_t *to_send) {
  bool tx = true;
  const int addr = GET_ADDR(to_send);

  // Host -> panda radar config. Consumed, never put on the car.
  if (addr == (int)PREAP_RADAR_VIN_ADDR) {
    preap_apply_radar_vin_msg(to_send);
    tx = false;
  }

  if (addr == 0x488) {
    // DAS_steeringControl: angle 6|15 (0.1 deg, offset), control type 23|2 (tesla_can.dbc)
    const int raw_angle_can = ((to_send->data[0] & 0x7FU) << 8) | to_send->data[1];
    const int desired_angle = raw_angle_can - 16384;
    const int steer_control_type = to_send->data[2] >> 6;
    const bool steer_control_enabled = steer_control_type == 1;
    if (steer_angle_cmd_checks(desired_angle, steer_control_enabled, TESLA_PREAP_STEERING_LIMITS)) {
      tx = false;
    }
    if ((steer_control_type != 0) && (steer_control_type != 1)) {
      tx = false;
    }
    if (steer_control_enabled && (ABS(desired_angle) > PREAP_MAX_ANGLE)) {
      tx = false;
    }
  }

  if (addr == 0x214) {
    // EPB_epasControl EPB_epasEACAllow 2|3: only 0 (off) or 1 (allow) (NAP)
    if ((to_send->data[0] & 0x07U) > 1U) {
      tx = false;
    }
  }

  if (addr == 0x551) {
    // Comma Pedal GAS_COMMAND: GAS_COMMAND 7|16, GAS_COMMAND2 23|16, ENABLE 39|1,
    // IDX 35|4, CHECKSUM 47|8 (tesla_preap_pedal.dbc BO_ 1361)
    if (!preap_enable_pedal || (GET_BUS(to_send) != preap_pedal_bus) || (GET_LEN(to_send) != 6U)) {
      tx = false;
    } else if (!preap_pedal_cmd_allowed(to_send)) {
      tx = false;
    } else {
    }
  }

  if (addr == 0x45) {
    // STW_ACTN_RQ: stock-cruise cancel only (whitelisted on bus 0, len 8)
    if (!preap_stalk_cancel_allowed(to_send)) {
      tx = false;
    } else {
      preap_stalk_spoof_used = true;
    }
  }

  return tx;
}

static int tesla_preap_fwd_hook(int bus_num, int addr) {
  UNUSED(bus_num);
  UNUSED(addr);
  // No AP ECU on bus 2: no 0<->2 forwarding (NAP). Radar frames go out via can_send.
  return -1;
}

// TX whitelist. No relay on Pre-AP.
static const CanMsg TESLA_PREAP_TX_MSGS[] = {
  {0x488, 0, 4},  // DAS_steeringControl
  {0x214, 0, 3},  // EPB_epasControl
  {0x551, 0, 6},  // Comma Pedal GAS_COMMAND, bus 0 (flag-gated)
  {0x551, 2, 6},  // Comma Pedal GAS_COMMAND, bus 2 (flag-gated)
  {0x560, 0, 8},  // radar donor config to panda (consumed)
  {0x45, 0, 8},   // STW_ACTN_RQ stock-cruise cancel (pedal flag + strict checks)
};

// RX checks: frequency only (NAP disables checksum/counter for Pre-AP EPAS).
static RxCheck tesla_preap_rx_checks[] = {
  {.msg = {{0x370, 0, 8, .frequency = 25U}, { 0 }, { 0 }}},   // EPAS_sysStatus
  {.msg = {{0x108, 0, 8, .frequency = 100U}, { 0 }, { 0 }}},  // DI_torque1
  {.msg = {{0x118, 0, 6, .frequency = 100U}, { 0 }, { 0 }}},  // DI_torque2
  {.msg = {{0x20a, 0, 8, .frequency = 50U}, { 0 }, { 0 }}},   // BrakeMessage
  {.msg = {{0x368, 0, 8, .frequency = 10U}, { 0 }, { 0 }}},   // DI_state
  {.msg = {{0x318, 0, 8, .frequency = 10U}, { 0 }, { 0 }}},   // GTW_carState
  {.msg = {{0x45, 0, 8, .frequency = 10U}, { 0 }, { 0 }}},    // STW_ACTN_RQ
  {.msg = {{0x155, 0, 8, .frequency = 50U}, { 0 }, { 0 }}},   // ESP_B
};

// GAS_SENSOR with checksum and counter (max_counter 15), one table per pedal bus
// so a pedal frame on the other bus never satisfies the check.
#define PREAP_RX_CHECKS_PEDAL(pedal_bus) {                                   \
  {.msg = {{0x370, 0, 8, .frequency = 25U}, { 0 }, { 0 }}},                  \
  {.msg = {{0x108, 0, 8, .frequency = 100U}, { 0 }, { 0 }}},                 \
  {.msg = {{0x118, 0, 6, .frequency = 100U}, { 0 }, { 0 }}},                 \
  {.msg = {{0x20a, 0, 8, .frequency = 50U}, { 0 }, { 0 }}},                  \
  {.msg = {{0x368, 0, 8, .frequency = 10U}, { 0 }, { 0 }}},                  \
  {.msg = {{0x318, 0, 8, .frequency = 10U}, { 0 }, { 0 }}},                  \
  {.msg = {{0x45, 0, 8, .frequency = 10U}, { 0 }, { 0 }}},                   \
  {.msg = {{0x155, 0, 8, .frequency = 50U}, { 0 }, { 0 }}},                  \
  {.msg = {{0x552, (pedal_bus), 6, .check_checksum = true, .max_counter = 15U, .frequency = 50U}, { 0 }, { 0 }}}, \
}

static RxCheck tesla_preap_rx_checks_pedal_bus2[] = PREAP_RX_CHECKS_PEDAL(2);
static RxCheck tesla_preap_rx_checks_pedal_bus0[] = PREAP_RX_CHECKS_PEDAL(0);

static safety_config tesla_preap_init(uint16_t param) {
  preap_calibration_requested = GET_FLAG(param, PREAP_FLAG_PEDAL_CALIBRATION);
  preap_enable_pedal = GET_FLAG(param, PREAP_FLAG_ENABLE_PEDAL) && !preap_calibration_requested;
  preap_radar_emulation = GET_FLAG(param, PREAP_FLAG_RADAR_EMULATION) && !preap_calibration_requested;
  preap_pedal_bus = GET_FLAG(param, PREAP_FLAG_PEDAL_BUS_ZERO) ? 0U : 2U;

  preap_gear = 4;
  preap_gear_prev = 4;
  preap_doors_open = false;
  preap_di_brake_pressed = false;
  preap_brake_message_pressed = false;
  preap_steering_disengage = false;
  preap_steering_disengage_prev = false;
  preap_stalk_pull_prev = false;
  preap_last_stalk_engage_us = 0U;
  preap_pedal_state = 4;
  preap_pedal_seen = false;
  preap_pedal_last_us = 0U;
  preap_pedal_tx_idx_prev = -1;
  preap_di_cruise_state = 0;
  preap_di_state_seen = false;
  preap_di_state_last_us = 0U;
  preap_stalk_seen = false;
  preap_stalk_last_us = 0U;
  preap_stalk_spoof_used = true;
  for (int i = 0; i < 8; i++) {
    preap_stalk_last[i] = 0U;
  }
  preap_radar_position = 0;
  preap_radar_epas_type = 0;
  preap_radar_vin_complete = 0U;
  preap_radar_should_send = false;
  for (int i = 0; i < 17; i++) {
    preap_radar_vin[i] = (uint8_t)' ';
  }

  safety_config ret;
  if (preap_calibration_requested) {
    ret = (safety_config){NULL, 0, NULL, 0};  // not ported: silent
  } else if (preap_enable_pedal && (preap_pedal_bus == 0U)) {
    ret = BUILD_SAFETY_CFG(tesla_preap_rx_checks_pedal_bus0, TESLA_PREAP_TX_MSGS);
  } else if (preap_enable_pedal) {
    ret = BUILD_SAFETY_CFG(tesla_preap_rx_checks_pedal_bus2, TESLA_PREAP_TX_MSGS);
  } else {
    ret = BUILD_SAFETY_CFG(tesla_preap_rx_checks, TESLA_PREAP_TX_MSGS);
  }
  return ret;
}

const safety_hooks tesla_preap_hooks = {
  .init = tesla_preap_init,
  .rx = tesla_preap_rx_hook,
  .tx = tesla_preap_tx_hook,
  .fwd = tesla_preap_fwd_hook,
  .get_checksum = tesla_preap_get_checksum,
  .compute_checksum = tesla_preap_compute_checksum,
  .get_counter = tesla_preap_get_counter,
};
