// ============================================================
//  XYZ Stage Controller — Arduino Sketch
//  Compatible with A4988 / DRV8825 drivers
//  Baud: 115200
// ============================================================

#include <EEPROM.h>

// --- Pin Definitions ---
const int STEP_PINS[3] = {3, 2, 4}; // X, Y, Z
const int DIR_PINS[3]  = {6, 5, 7}; // X, Y, Z
const int sys_en = 8;

// --- EEPROM Layout (43 bytes) ---
// 0:     magic (0xAB = valid state saved)
// 1-12:  positions  — 3 × long (4 bytes each)
// 13-36: limits     — 6 × long: Xmin, Xmax, Ymin, Ymax, Zmin, Zmax
// 37:    axis calibration bitmask (bit 0=X, bit 1=Y, bit 2=Z)
// 38:    focus magic (0xAF = focus Z is set)
// 39-42: focusZ — long
const byte MAGIC_VALID = 0xAB;
const byte MAGIC_FOCUS = 0xAF;

// --- Runtime state ---
long pos[3]     = {0, 0, 0};
long prevPos[3] = {0, 0, 0};
long limMin[3]  = {0, 0, 0};
long limMax[3]  = {0, 0, 0};
bool axisCalibrated[3] = {false, false, false};
bool isCalibrated = false;
int stepDelay = 800;

// --- Driver power ---
// The DRV8825s squeal (current chopping in the audible range) whenever they
// are enabled, including at standstill. So by default they are enabled only
// for the duration of a move. 'E' switches to holding torque at idle (for an
// axis that back-drives when unpowered); 'D' returns to releasing at idle.
bool holdWhenIdle = false;
bool driversOn = false;

// --- Pending calibration endpoints (transient, not saved to EEPROM) ---
long endpointPos[3];
long endpointNeg[3];
bool endpointPosSet[3] = {false, false, false};
bool endpointNegSet[3] = {false, false, false};

// --- Focus ---
long focusZ   = 0;
bool focusSet = false;

// --- Non-blocking serial buffer ---
char cmdBuf[64];
int cmdBufLen = 0;

// ============================================================
void setup() {
  pinMode(sys_en, OUTPUT);
  digitalWrite(sys_en, HIGH); // drivers off (active LOW) until a move needs them
  for (int i = 0; i < 3; i++) {
    pinMode(STEP_PINS[i], OUTPUT);
    pinMode(DIR_PINS[i], OUTPUT);
  }

  Serial.begin(115200);

  byte magic = EEPROM.read(0);
  if (magic == MAGIC_VALID) {
    for (int i = 0; i < 3; i++)
      EEPROM.get(1 + i * 4, pos[i]);
    byte calMask = EEPROM.read(37);
    for (int i = 0; i < 3; i++) {
      if (calMask & (1 << i)) {
        axisCalibrated[i] = true;
        EEPROM.get(13 + i * 8,     limMin[i]);
        EEPROM.get(13 + i * 8 + 4, limMax[i]);
        pos[i] = constrain(pos[i], limMin[i], limMax[i]);
      }
    }
    isCalibrated = (calMask == 0x07);
  } else {
    EEPROM.write(0, MAGIC_VALID);
    EEPROM.write(37, 0);
    savePosition();
  }

  if (EEPROM.read(38) == MAGIC_FOCUS) {
    focusSet = true;
    EEPROM.get(39, focusZ);
  }

  memcpy(prevPos, pos, sizeof(pos));
  sendPos();
  sendLimits();

  // Report per-axis calibration state on startup
  for (int i = 0; i < 3; i++) {
    if (axisCalibrated[i]) {
      Serial.print("CAL "); Serial.print("XYZ"[i]);
      Serial.print(" DONE "); Serial.print(limMin[i]);
      Serial.print(" "); Serial.println(limMax[i]);
    }
  }
  if (axisCalibrated[0] || axisCalibrated[1] || axisCalibrated[2]) {
    Serial.print("CAL INFO");
    for (int i = 0; i < 3; i++) {
      Serial.print(" "); Serial.print(limMin[i]);
      Serial.print(" "); Serial.print(limMax[i]);
    }
    Serial.println();
  }

  Serial.println(isCalibrated ? "CALIBRATED" : "UNCALIBRATED");
  if (focusSet) { Serial.print("FOCUS "); Serial.println(focusZ); }
  else          { Serial.println("FOCUS UNSET"); }
  Serial.println("READY");
}

// ============================================================
void loop() {
  if (readSerial()) {
    processCommand(String(cmdBuf));
  }
}

// ============================================================
//  Serial
// ============================================================

bool readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdBufLen > 0) {
        cmdBuf[cmdBufLen] = '\0';
        cmdBufLen = 0;
        return true;
      }
    } else if (cmdBufLen < 63) {
      cmdBuf[cmdBufLen++] = c;
    }
  }
  return false;
}

void processCommand(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line.startsWith("CAL")) {
    handleCal(line.length() > 3 ? line.substring(4) : String(""));
    return;
  }

  // UNDO — revert position to before last motion command (no motors move)
  if (line == "UNDO") {
    memcpy(pos, prevPos, sizeof(pos));
    savePosition();
    sendPos();
    Serial.println("UNDONE");
    return;
  }

  // FOCUS SET / GO / GET
  if (line.startsWith("FOCUS")) {
    String arg = (line.length() > 6) ? line.substring(6) : String("GET");
    arg.trim();
    handleFocus(arg);
    return;
  }

  char cmd = line.charAt(0);
  switch (cmd) {

    case 'A': {   // Absolute move: A x y z
      long t[3];
      sscanf(line.c_str() + 1, " %ld %ld %ld", &t[0], &t[1], &t[2]);
      moveAbsolute(t[0], t[1], t[2]);
      break;
    }

    case 'R': {   // Relative move: R dx dy dz
      long d[3];
      sscanf(line.c_str() + 1, " %ld %ld %ld", &d[0], &d[1], &d[2]);
      moveAbsolute(pos[0]+d[0], pos[1]+d[1], pos[2]+d[2]);
      break;
    }

    case 'J': {   // Single-axis jog: J X 100
      char axis; long delta;
      sscanf(line.c_str() + 1, " %c %ld", &axis, &delta);
      int i = axisIdx(axis);
      if (i >= 0) {
        long t[3] = {pos[0], pos[1], pos[2]};
        t[i] += delta;
        moveAbsolute(t[0], t[1], t[2]);
      }
      break;
    }

    case 'H':   // Redefine current position as (0,0,0)
      memcpy(prevPos, pos, sizeof(pos));
      pos[0] = 0; pos[1] = 0; pos[2] = 0;
      savePosition();
      sendPos();
      Serial.println("HOME");
      break;

    case 'S': {   // Set speed delay: S 800
      int d = line.substring(1).toInt();
      if (d > 10) { stepDelay = d; Serial.print("SPEED "); Serial.println(stepDelay); }
      else Serial.println("ERR delay must be >10");
      break;
    }

    // E: hold torque when idle. D: release when idle (default). Moves always
    // power the drivers, so D no longer lets steps be counted with the
    // drivers off.
    case 'E': holdWhenIdle = true;  driversEnable();  Serial.println("ENABLED");  break;
    case 'D': holdWhenIdle = false; driversIdle();    Serial.println("DISABLED"); break;
    case 'P': sendPos();    break;
    case 'L': sendLimits(); break;

    case 'Z': {   // Zero axis: Z X  Z Y  Z Z  Z ALL
      memcpy(prevPos, pos, sizeof(pos));
      String arg = line.substring(1); arg.trim();
      if      (arg == "X")   pos[0] = 0;
      else if (arg == "Y")   pos[1] = 0;
      else if (arg == "Z")   pos[2] = 0;
      else if (arg == "ALL") { pos[0] = 0; pos[1] = 0; pos[2] = 0; }
      savePosition(); sendPos();
      break;
    }

    default:
      Serial.print("ERR unknown command: "); Serial.println(line);
  }
}

// ============================================================
//  Calibration
// ============================================================

void handleCal(String arg) {
  arg.trim();

  if (arg == "CANCEL") {
    Serial.println("CAL CANCELLED");
    return;
  }

  // CAL CLEAR X/Y/Z/ALL — erase calibration for one or all axes
  if (arg.startsWith("CLEAR")) {
    String what = arg.length() > 5 ? arg.substring(5) : String("");
    what.trim();
    if (what == "ALL" || what == "") {
      for (int i = 0; i < 3; i++) {
        endpointPosSet[i] = endpointNegSet[i] = false;
        axisCalibrated[i] = false;
      }
      isCalibrated = false;
      EEPROM.write(37, 0);
      Serial.println("CAL CLEARED");
    } else {
      int idx = axisIdx(what.charAt(0));
      if (idx >= 0) {
        endpointPosSet[idx] = endpointNegSet[idx] = false;
        axisCalibrated[idx] = false;
        isCalibrated = false;
        byte calMask = EEPROM.read(37) & ~(1 << idx);
        EEPROM.write(37, calMask);
        Serial.print("CAL CLEAR "); Serial.println("XYZ"[idx]);
      }
    }
    Serial.println(isCalibrated ? "CALIBRATED" : "UNCALIBRATED");
    sendLimits();
    return;
  }

  // CAL ENDP X POS / NEG — mark current position as an endpoint
  // The two endpoints of an axis define its range; their midpoint becomes 0.
  if (arg.startsWith("ENDP ")) {
    if (arg.length() < 9) { Serial.println("ERR CAL ENDP: need axis and side (e.g. ENDP X POS)"); return; }
    int idx = axisIdx(arg.charAt(5));
    if (idx < 0) { Serial.println("ERR CAL ENDP: unknown axis"); return; }
    String side = arg.substring(7); side.trim();
    bool isPos = (side == "POS");
    bool isNeg = (side == "NEG");
    if (!isPos && !isNeg) {
      Serial.println("ERR CAL ENDP: side must be POS or NEG");
      return;
    }

    char ch = "XYZ"[idx];
    if (isPos) {
      endpointPos[idx]    = pos[idx];
      endpointPosSet[idx] = true;
      Serial.print("CAL ENDP "); Serial.print(ch);
      Serial.print(" POS MARKED "); Serial.println(pos[idx]);
    } else {
      endpointNeg[idx]    = pos[idx];
      endpointNegSet[idx] = true;
      Serial.print("CAL ENDP "); Serial.print(ch);
      Serial.print(" NEG MARKED "); Serial.println(pos[idx]);
    }

    // If exactly one endpoint is now set (just started re-calibrating this axis),
    // drop the old limits immediately so the user can move freely to the other extreme.
    bool exactlyOneSet = (endpointPosSet[idx] != endpointNegSet[idx]);
    if (exactlyOneSet && axisCalibrated[idx]) {
      axisCalibrated[idx] = false;
      isCalibrated = false;
      byte calMask = EEPROM.read(37) & ~(1 << idx);
      EEPROM.write(37, calMask);
      Serial.print("CAL CLEAR "); Serial.println(ch);
      Serial.println("UNCALIBRATED");
      sendLimits();
    }

    // When both endpoints are set, compute calibration for this axis
    if (endpointPosSet[idx] && endpointNegSet[idx]) {
      long posPt = endpointPos[idx];
      long negPt = endpointNeg[idx];

      if (posPt <= negPt) {
        Serial.println("ERR CAL ENDP: positive endpoint must be greater than negative endpoint");
        if (isPos) endpointPosSet[idx] = false;
        else       endpointNegSet[idx] = false;
        return;
      }

      // Midpoint of the two endpoints becomes coordinate 0
      long center = (posPt + negPt) / 2;
      limMin[idx] = negPt - center;
      limMax[idx] = posPt - center;

      // Shift this axis so that the midpoint is now 0
      long shift = center;
      pos[idx]     -= shift;
      prevPos[idx] -= shift;
      endpointPos[idx] -= shift;
      endpointNeg[idx] -= shift;

      axisCalibrated[idx] = true;
      isCalibrated = axisCalibrated[0] && axisCalibrated[1] && axisCalibrated[2];

      // Persist to EEPROM
      byte calMask = 0;
      for (int i = 0; i < 3; i++) if (axisCalibrated[i]) calMask |= (1 << i);
      EEPROM.write(0, MAGIC_VALID);
      EEPROM.write(37, calMask);
      savePosition();
      EEPROM.put(13 + idx * 8,     limMin[idx]);
      EEPROM.put(13 + idx * 8 + 4, limMax[idx]);

      Serial.print("CAL "); Serial.print(ch);
      Serial.print(" DONE "); Serial.print(limMin[idx]);
      Serial.print(" "); Serial.println(limMax[idx]);

      Serial.print("CAL INFO");
      for (int i = 0; i < 3; i++) {
        Serial.print(" "); Serial.print(limMin[i]);
        Serial.print(" "); Serial.print(limMax[i]);
      }
      Serial.println();

      Serial.println(isCalibrated ? "CALIBRATED" : "UNCALIBRATED");
      sendPos();
      sendLimits();
    }
    return;
  }

  // CAL MANUAL — bulk entry (used by state import)
  // Format: CAL MANUAL xMin xMax yMin yMax zMin zMax xCur yCur zCur
  // All values in the same observed coordinate system; midpoints become 0.
  if (arg.startsWith("MANUAL ")) {
    long v[9];
    int n = sscanf(arg.c_str() + 7,
                   " %ld %ld %ld %ld %ld %ld %ld %ld %ld",
                   &v[0],&v[1],&v[2],&v[3],&v[4],&v[5],&v[6],&v[7],&v[8]);
    if (n == 9) {
      for (int i = 0; i < 3; i++) {
        long mn = v[i * 2], mx = v[i * 2 + 1];
        long center = (mn + mx) / 2;
        limMin[i] = mn - center;
        limMax[i] = mx - center;
        pos[i]    = v[6 + i] - center;
        axisCalibrated[i] = true;
      }
      memcpy(prevPos, pos, sizeof(pos));
      isCalibrated = true;
      EEPROM.write(0, MAGIC_VALID);
      EEPROM.write(37, 0x07);
      savePosition();
      for (int i = 0; i < 3; i++) {
        EEPROM.put(13 + i * 8,     limMin[i]);
        EEPROM.put(13 + i * 8 + 4, limMax[i]);
      }
      Serial.print("CAL INFO");
      for (int i = 0; i < 3; i++) {
        Serial.print(" "); Serial.print(limMin[i]);
        Serial.print(" "); Serial.print(limMax[i]);
      }
      Serial.println();
      Serial.println("CALIBRATED");
      Serial.println("CAL MANUAL OK");
      sendPos();
      sendLimits();
    } else {
      Serial.println("ERR CAL MANUAL: need 9 values xmin xmax ymin ymax zmin zmax xcur ycur zcur");
    }
    return;
  }

  Serial.print("ERR unknown CAL command: "); Serial.println(arg);
}

// ============================================================
//  Focus
// ============================================================

void handleFocus(String arg) {
  if (arg == "SET") {
    focusZ  = pos[2];
    focusSet = true;
    EEPROM.write(38, MAGIC_FOCUS);
    EEPROM.put(39, focusZ);
    Serial.print("FOCUS "); Serial.println(focusZ);

  } else if (arg == "GO") {
    if (!focusSet) { Serial.println("ERR no focus position set"); return; }
    memcpy(prevPos, pos, sizeof(pos));
    long eMin, eMax;
    effectiveLimits(2, eMin, eMax);
    long tz = constrain(focusZ, eMin, eMax);
    driversEnable();
    stepAxisTo(2, tz);
    driversIdle();
    savePosition();
    sendPos();
    Serial.println("DONE");

  } else { // GET (default)
    if (focusSet) { Serial.print("FOCUS "); Serial.println(focusZ); }
    else          { Serial.println("FOCUS UNSET"); }
  }
}

// ============================================================
//  Helpers
// ============================================================

int axisIdx(char c) {
  if (c == 'X' || c == 'x') return 0;
  if (c == 'Y' || c == 'y') return 1;
  if (c == 'Z' || c == 'z') return 2;
  return -1;
}

// Movement limits: use calibrated range for that axis, or unrestricted if not calibrated.
void effectiveLimits(int ax, long &eMin, long &eMax) {
  if (axisCalibrated[ax]) {
    eMin = limMin[ax];
    eMax = limMax[ax];
  } else {
    eMin = -2000000000L;
    eMax =  2000000000L;
  }
}

// Single step pulse.
void doStep(int ax, bool fwd) {
  digitalWrite(DIR_PINS[ax], fwd ? HIGH : LOW);
  delayMicroseconds(2);
  digitalWrite(STEP_PINS[ax], HIGH);
  delayMicroseconds(stepDelay);
  digitalWrite(STEP_PINS[ax], LOW);
}

// ============================================================
//  Motion
// ============================================================

void moveAbsolute(long tx, long ty, long tz) {
  memcpy(prevPos, pos, sizeof(pos));
  long t[3] = {tx, ty, tz};
  for (int i = 0; i < 3; i++) {
    long eMin, eMax;
    effectiveLimits(i, eMin, eMax);
    t[i] = constrain(t[i], eMin, eMax);
  }
  driversEnable();
  stepAxisTo(0, t[0]);
  stepAxisTo(1, t[1]);
  stepAxisTo(2, t[2]);
  driversIdle();
  savePosition();
  sendPos();
  Serial.println("DONE");
}

void driversEnable() {
  if (driversOn) return;
  digitalWrite(sys_en, LOW);
  driversOn = true;
  delay(2);  // let the outputs and coil current come up before the first step
}

void driversIdle() {
  if (holdWhenIdle || !driversOn) return;
  delay(20);  // let the rotor settle on the last step before releasing it
  digitalWrite(sys_en, HIGH);
  driversOn = false;
}

void stepAxisTo(int ax, long target) {
  if (pos[ax] == target) return;
  digitalWrite(DIR_PINS[ax], (target > pos[ax]) ? HIGH : LOW);
  while (pos[ax] != target) {
    digitalWrite(STEP_PINS[ax], HIGH);
    delayMicroseconds(stepDelay);
    digitalWrite(STEP_PINS[ax], LOW);
    delayMicroseconds(stepDelay);
    pos[ax] += (target > pos[ax]) ? 1 : -1;
  }
}

// ============================================================
//  Reporting
// ============================================================

void sendPos() {
  Serial.print("POS ");
  Serial.print(pos[0]); Serial.print(" ");
  Serial.print(pos[1]); Serial.print(" ");
  Serial.println(pos[2]);
}

// Sends calibrated limits for each axis, or 0 0 if that axis is not yet calibrated.
void sendLimits() {
  Serial.print("LIMITS");
  for (int i = 0; i < 3; i++) {
    if (axisCalibrated[i]) {
      Serial.print(" "); Serial.print(limMin[i]);
      Serial.print(" "); Serial.print(limMax[i]);
    } else {
      Serial.print(" 0 0");
    }
  }
  Serial.println();
}

// ============================================================
//  EEPROM
// ============================================================

void savePosition() {
  for (int i = 0; i < 3; i++)
    EEPROM.put(1 + i * 4, pos[i]);
}
