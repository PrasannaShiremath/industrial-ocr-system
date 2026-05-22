"""
═══════════════════════════════════════════════════════════════════
  Real-Time Industrial OCR System — TRIGGERED SNAPSHOT VERSION
  Stack: PaddleOCR · OpenCV · Threading · MQTT · Regex · Logging
  Mode: Sensor/keyboard triggered — one sharp frame per product
═══════════════════════════════════════════════════════════════════

pip install paddlepaddle paddleocr opencv-python paho-mqtt numpy pyserial
"""

# ─────────────────────────────────────────────
#  SECTION 1 — IMPORTS
#  Location: top of file, nothing above this
# ─────────────────────────────────────────────
import cv2
import time
import queue
import threading
import re
import os
import json
import numpy as np
from datetime import datetime
from paddleocr import PaddleOCR
import paho.mqtt.client as mqtt

# Uncomment this when your ESP32/sensor is connected via USB serial:
# import serial


# ─────────────────────────────────────────────
#  SECTION 2 — CONFIGURATION
#  Location: right after imports
#  Edit these values to match your setup
# ─────────────────────────────────────────────
VIDEO_SOURCE        = 0          # 0 = first USB camera
CONFIDENCE_THRESH   = 0.75       # ignore detections below this score
FRAME_QUEUE_SIZE    = 2
OCR_QUEUE_SIZE      = 2

# TRIGGER MODE — choose one:
# "keyboard"  → press T key to simulate a sensor trigger (for testing)
# "serial"    → real ESP32/photoelectric sensor via USB serial port
TRIGGER_MODE        = "keyboard"
SERIAL_PORT         = "COM3"     # Windows: "COM3", Linux: "/dev/ttyUSB0"
SERIAL_BAUD         = 9600

# Camera exposure — short exposure freezes motion on the conveyor
# -1 = auto, -6 = ~1ms (fast conveyor), -4 = ~8ms (slow conveyor)
CAMERA_EXPOSURE     = -6

# Logging — all captured images + JSON results saved here
LOG_DIR             = "logs"

# MQTT broker settings
MQTT_BROKER         = "localhost"
MQTT_PORT           = 1883
MQTT_TOPIC_OCR      = "factory/ocr/detections"
MQTT_TOPIC_SERIAL   = "factory/ocr/serial_numbers"

# Neon colour palette (BGR for OpenCV)
NEON_GREEN  = (57, 255, 20)
NEON_CYAN   = (255, 255, 0)
NEON_PINK   = (255, 0, 200)
WHITE       = (255, 255, 255)
BLACK       = (0, 0, 0)


# ─────────────────────────────────────────────
#  SECTION 3 — REGEX PARSER
#  Location: after configuration block
#  Add/edit patterns to match your label format
# ─────────────────────────────────────────────
PATTERNS = {
    "serial_number":  re.compile(r'\b[A-Z]{2,4}[-]?\d{4,8}\b'),
    "employee_id":    re.compile(r'\bEMP[-]?\d{4,6}\b', re.IGNORECASE),
    "warning_label":  re.compile(r'\b(CAUTION|WARNING|DANGER|STOP)\b', re.IGNORECASE),
    "lot_code":       re.compile(r'\bLOT[:\s]?[A-Z0-9]{6,12}\b', re.IGNORECASE),
}

def parse_ocr_text(text: str) -> dict:
    """Scan raw OCR string against all industrial patterns."""
    hits = {}
    for name, pattern in PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            hits[name] = matches
    return hits


# ─────────────────────────────────────────────
#  SECTION 4 — LOGGING FUNCTION
#  Location: after parse_ocr_text()
#  NEW — was not in the original script
# ─────────────────────────────────────────────
# Global item counter — increments every trigger
_item_counter = 0
_item_lock    = threading.Lock()

def log_detection(frame: np.ndarray, detections: list) -> dict:
    """
    Called once per triggered capture.
    Saves: raw JPEG image + JSON result + one row in master CSV.
    Returns the log entry dict for MQTT publishing.
    """
    global _item_counter
    with _item_lock:
        _item_counter += 1
        item_id = _item_counter

    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    # 1. Save the captured frame as evidence image
    img_path = os.path.join(LOG_DIR, f"{ts}_item{item_id}.jpg")
    cv2.imwrite(img_path, frame)

    # 2. Build and save JSON result
    combined_text = " ".join(d["text"] for d in detections)
    log_entry = {
        "timestamp":  ts,
        "item_id":    item_id,
        "image_file": img_path,
        "detections": [
            {"text": d["text"], "score": round(d["score"], 3)}
            for d in detections
        ],
        "parsed": parse_ocr_text(combined_text),
    }
    json_path = os.path.join(LOG_DIR, f"{ts}_item{item_id}.json")
    with open(json_path, "w") as f:
        json.dump(log_entry, f, indent=2)

    # 3. Append one line to master CSV (opens Excel directly)
    csv_path   = os.path.join(LOG_DIR, "master_log.csv")
    write_hdr  = not os.path.exists(csv_path)
    texts      = "|".join(d["text"]            for d in detections)
    scores     = "|".join(str(round(d["score"], 3)) for d in detections)
    with open(csv_path, "a", newline="") as f:
        if write_hdr:
            f.write("timestamp,item_id,image,texts,scores\n")
        f.write(f'{ts},{item_id},{img_path},"{texts}","{scores}"\n')

    print(f"[LOG] Item {item_id} → {img_path}")
    return log_entry


# ─────────────────────────────────────────────
#  SECTION 5 — MQTT PUBLISHER
#  Location: after log_detection()
#  Same as original — no changes needed
# ─────────────────────────────────────────────
class MQTTPublisher:
    """Non-blocking MQTT wrapper. Fails gracefully if broker is offline."""
    def __init__(self, broker: str, port: int):
        self.client    = mqtt.Client(client_id="ocr_system", clean_session=True)
        self.connected = False
        try:
            self.client.connect(broker, port, keepalive=30)
            self.client.loop_start()
            self.connected = True
            print(f"[MQTT] Connected to {broker}:{port}")
        except Exception as e:
            print(f"[MQTT] Broker unavailable ({e}). Running offline.")

    def publish(self, topic: str, payload: str, qos: int = 1):
        if self.connected:
            self.client.publish(topic, payload, qos=qos)

    def disconnect(self):
        if self.connected:
            self.client.loop_stop()
            self.client.disconnect()


# ─────────────────────────────────────────────
#  SECTION 6 — CAMERA PRODUCER (TRIGGER MODE)
#  Location: replaces the old CameraProducer class entirely
#  KEY CHANGE: camera waits for a trigger signal before capturing
# ─────────────────────────────────────────────
class CameraProducer(threading.Thread):
    """
    Runs on its own thread.
    In TRIGGER MODE it holds a live preview feed but only pushes
    a frame into frame_queue when a trigger event fires.

    Trigger sources:
      - keyboard: main loop calls producer.trigger() when T is pressed
      - serial:   background serial thread calls producer.trigger()
                  when ESP32 sends the "TRIGGER" string
    """
    def __init__(self, source, frame_queue: queue.Queue, mode: str = "keyboard"):
        super().__init__(daemon=True, name="CameraProducer")
        self.source       = source
        self.frame_queue  = frame_queue
        self.mode         = mode
        self.stopped      = threading.Event()
        self._trigger     = threading.Event()  # set this to fire a capture
        self.preview_frame = None              # always holds the latest frame
        self._preview_lock = threading.Lock()

    def trigger(self):
        """Call this from any thread to fire a capture."""
        self._trigger.set()

    def run(self):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera: {self.source}")

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_EXPOSURE, CAMERA_EXPOSURE)  # short exposure

        print(f"[Camera] Ready  {int(cap.get(3))}×{int(cap.get(4))} "
              f"@ {cap.get(cv2.CAP_PROP_FPS):.0f} FPS  mode={self.mode}")

        if self.mode == "serial":
            self._start_serial_listener()

        while not self.stopped.is_set():
            ok, frame = cap.read()
            if not ok:
                break

            # Always keep the preview updated (for the live window)
            with self._preview_lock:
                self.preview_frame = frame

            # If trigger fired → flush buffer and grab a fresh sharp frame
            if self._trigger.is_set():
                self._trigger.clear()
                # Flush 2 stale frames from camera buffer
                for _ in range(2):
                    cap.grab()
                ok2, sharp = cap.read()
                if ok2:
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            pass
                    self.frame_queue.put_nowait(sharp)
                    print("[Camera] Triggered capture → sent to OCR")

        cap.release()

    def get_preview(self):
        """Returns the latest camera frame for live display."""
        with self._preview_lock:
            return self.preview_frame.copy() if self.preview_frame is not None else None

    def _start_serial_listener(self):
        """Background thread that listens for ESP32 trigger signal."""
        def _listen():
            try:
                import serial
                ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
                print(f"[Serial] Listening on {SERIAL_PORT} @ {SERIAL_BAUD}")
                while not self.stopped.is_set():
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line == "TRIGGER":
                        print("[Serial] Hardware trigger received")
                        self.trigger()
            except Exception as e:
                print(f"[Serial] Error: {e} — falling back to keyboard mode")
        t = threading.Thread(target=_listen, daemon=True, name="SerialListener")
        t.start()

    def stop(self):
        self.stopped.set()


# ─────────────────────────────────────────────
#  SECTION 7 — OCR WORKER
#  Location: replaces the old OCRWorker class
#  KEY CHANGE: calls log_detection() after every inference
# ─────────────────────────────────────────────
class OCRWorker(threading.Thread):
    """
    Consumes triggered frames, runs PaddleOCR, logs results, publishes MQTT.
    GIL note: PaddleOCR inference runs in C++ and releases the GIL,
    so the camera preview thread stays smooth during heavy inference.
    """
    def __init__(self, frame_queue: queue.Queue,
                 result_queue: queue.Queue,
                 mqtt_pub: MQTTPublisher):
        super().__init__(daemon=True, name="OCRWorker")
        self.frame_queue  = frame_queue
        self.result_queue = result_queue
        self.mqtt         = mqtt_pub
        self.stopped      = threading.Event()

        print("[OCR] Loading PaddleOCR engine …")
        self.ocr = PaddleOCR(
            use_angle_cls=True,
            lang="en",
            use_gpu=False,       # flip to True if using paddlepaddle-gpu
            show_log=False,
            rec_batch_num=6,
        )
        print("[OCR] Engine ready.")

    def run(self):
        while not self.stopped.is_set():
            try:
                frame = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # ── Inference (GIL released inside C++ kernels) ──
            raw = self.ocr.ocr(frame, cls=True)

            detections = []
            if raw and raw[0]:
                for line in raw[0]:
                    box, (text, score) = line
                    if score < CONFIDENCE_THRESH:
                        continue
                    pts = np.array(box, dtype=np.int32)
                    detections.append({"pts": pts, "text": text, "score": score})

            # ── Log every triggered capture (even if no text found) ──
            log_entry = log_detection(frame, detections)

            # ── MQTT publish if anything was parsed ──
            if log_entry["parsed"]:
                self.mqtt.publish(MQTT_TOPIC_OCR, json.dumps(log_entry))
                if "serial_number" in log_entry["parsed"]:
                    self.mqtt.publish(
                        MQTT_TOPIC_SERIAL,
                        json.dumps(log_entry["parsed"]["serial_number"])
                    )

            # Push result to display queue
            if self.result_queue.full():
                try:
                    self.result_queue.get_nowait()
                except queue.Empty:
                    pass
            self.result_queue.put_nowait((frame, detections))

    def stop(self):
        self.stopped.set()


# ─────────────────────────────────────────────
#  SECTION 8 — RENDERING HELPERS
#  Location: after OCRWorker, before main()
#  Same as original — no changes needed
# ─────────────────────────────────────────────
def draw_detections(frame: np.ndarray, detections: list) -> np.ndarray:
    overlay = frame.copy()
    for det in detections:
        pts, text, score = det["pts"], det["text"], det["score"]
        cv2.polylines(overlay, [pts], isClosed=True,
                      color=NEON_GREEN, thickness=3, lineType=cv2.LINE_AA)
        cv2.polylines(overlay, [pts], isClosed=True,
                      color=NEON_CYAN,  thickness=1, lineType=cv2.LINE_AA)
        label = f"{text}  {score:.2f}"
        org   = (pts[0][0], pts[0][1] - 10)
        (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(overlay,
                      (org[0]-4, org[1]-th-4), (org[0]+tw+4, org[1]+bl),
                      BLACK, cv2.FILLED)
        cv2.putText(overlay, label, org,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, NEON_PINK, 1, cv2.LINE_AA)
    return cv2.addWeighted(overlay, 0.85, frame, 0.15, 0)


def draw_hud(frame: np.ndarray, fps: float, item_count: int,
             last_trigger_ago: float) -> np.ndarray:
    """HUD shows FPS, item counter, and time since last trigger."""
    # FPS badge
    cv2.rectangle(frame, (8, 8), (170, 40), BLACK, cv2.FILLED)
    color = NEON_GREEN if fps >= 25 else (0,165,255) if fps >= 15 else (0,0,255)
    cv2.putText(frame, f"FPS: {fps:5.1f}", (14, 32),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)

    # Item counter (top right)
    cv2.putText(frame, f"Items logged: {item_count}", (10, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)

    # Last trigger age
    trigger_text = f"Last trigger: {last_trigger_ago:.1f}s ago"
    cv2.putText(frame, trigger_text, (10, 88),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, NEON_CYAN, 1, cv2.LINE_AA)

    # Keyboard hint
    cv2.putText(frame, "Press T = trigger | Q = quit", (10, frame.shape[0]-12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180,180,180), 1, cv2.LINE_AA)
    return frame


# ─────────────────────────────────────────────
#  SECTION 9 — MAIN LOOP
#  Location: bottom of file, replaces old main()
#  KEY CHANGE: T key fires camera.trigger()
# ─────────────────────────────────────────────
def main():
    frame_queue  = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
    result_queue = queue.Queue(maxsize=OCR_QUEUE_SIZE)

    mqtt_pub = MQTTPublisher(MQTT_BROKER, MQTT_PORT)
    camera   = CameraProducer(VIDEO_SOURCE, frame_queue, mode=TRIGGER_MODE)
    worker   = OCRWorker(frame_queue, result_queue, mqtt_pub)

    camera.start()
    worker.start()

    fps            = 0.0
    alpha          = 0.1
    prev_time      = time.perf_counter()
    last_detections = []
    last_trigger_t  = time.perf_counter()

    print("\n[Main] Running.  Press T to trigger capture.  Press Q to quit.\n")

    while True:
        # ── Get latest OCR result (non-blocking) ──
        try:
            _, last_detections = result_queue.get_nowait()
            last_trigger_t = time.perf_counter()   # reset timer on new result
        except queue.Empty:
            pass

        # ── Get live preview frame (always smooth) ──
        preview = camera.get_preview()
        if preview is None:
            time.sleep(0.005)
            continue

        # ── Render detections on the preview ──
        display = draw_detections(preview, last_detections)
        display = draw_hud(display, fps, _item_counter,
                           time.perf_counter() - last_trigger_t)

        cv2.imshow("Industrial OCR  |  T = trigger  Q = quit", display)

        # ── FPS ──
        now       = time.perf_counter()
        fps       = alpha * (1.0 / max(now - prev_time, 1e-9)) + (1-alpha) * fps
        prev_time = now

        # ── Key handling ──
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('t'):
            camera.trigger()             # simulate sensor trigger via keyboard
            print("[Main] Manual trigger fired (keyboard)")

    # ── Shutdown ──
    print("[Main] Shutting down …")
    camera.stop()
    worker.stop()
    camera.join(timeout=2)
    worker.join(timeout=5)
    mqtt_pub.disconnect()
    cv2.destroyAllWindows()
    print(f"[Main] Done. {_item_counter} items logged to '{LOG_DIR}/'")


if __name__ == "__main__":
    main()