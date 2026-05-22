# 🏭 Real-Time Industrial OCR System

A production-grade, zero-frame-lag Optical Character Recognition system
for industrial conveyor lines. Built with PaddleOCR, OpenCV, and Python.

![Demo](docs/demo.gif)

---

## 🚀 Features

- ⚡ **Multi-threaded architecture** — camera and OCR run on separate threads,
  display stays at 30 FPS regardless of inference speed
- 📸 **Triggered snapshot capture** — photoelectric sensor fires camera at
  exact moment product passes, eliminating motion blur
- 🧠 **PaddleOCR C++ backend** — GIL-free inference via native kernels
- 🔍 **Smart Regex parser** — auto-extracts serial numbers, lot codes,
  employee IDs, and warning labels
- 📡 **MQTT publishing** — parsed results sent to broker in real time,
  ready to trigger ESP32 relays or PLC events
- 🗂️ **Auto-logging** — every capture saved as JPEG + JSON + CSV row

---

## 🏗️ System Architecture

[Photoelectric Sensor]
│ trigger
▼
[Camera Thread]  ──frame_queue──►  [OCR Thread]  ──result_queue──►  [Display]
CameraProducer                     OCRWorker                        main()
(OpenCV)                           (PaddleOCR)                      (cv2)
│
┌────┴────┐
[Logger]  [MQTT]
JPEG/JSON  paho
/CSV

---

## 📦 Installation

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/ocr_project.git
cd ocr_project
```

### 2. Create virtual environment
```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux
```

### 3. Install dependencies (in this exact order)
```bash
pip install "numpy<2"
pip install paddlepaddle==2.6.1
pip install paddleocr==2.7.3
pip install "numpy<2" --force-reinstall
pip install paho-mqtt opencv-python pyserial shapely
```

---

## ⚙️ Configuration

Edit the config block at the top of `main.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDEO_SOURCE` | `0` | Camera index or video file path |
| `TRIGGER_MODE` | `"keyboard"` | `"keyboard"` or `"serial"` |
| `SERIAL_PORT` | `"COM3"` | ESP32/sensor serial port |
| `CAMERA_EXPOSURE` | `-6` | `-6` = ~1ms (fast belt), `-4` = ~8ms |
| `CONFIDENCE_THRESH` | `0.75` | Minimum OCR confidence to accept |
| `MQTT_BROKER` | `"localhost"` | MQTT broker IP address |
| `LOG_DIR` | `"logs"` | Folder for saved captures |

---

## ▶️ Usage

```bash
# Start MQTT broker first (optional)
mosquitto -v

# Run the system
python main.py
```

### Keyboard controls
| Key | Action |
|-----|--------|
| `T` | Trigger a manual capture (simulates sensor) |
| `Q` | Quit and shutdown cleanly |

---

## 📂 Log Output

Every triggered capture creates 3 files:

logs/
├── master_log.csv                    ← all items, opens in Excel
├── 20250520_091233_item1.jpg         ← raw captured image
└── 20250520_091233_item1.json        ← structured OCR result

Sample JSON output:
```json
{
  "timestamp": "20250520_091233_123456",
  "item_id": 1,
  "image_file": "logs/20250520_091233_item1.jpg",
  "detections": [
    {"text": "SN-884721", "score": 0.934}
  ],
  "parsed": {
    "serial_number": ["SN-884721"]
  }
}
```

---

## 🔧 Hardware Setup (Production)

[Omron E3Z Sensor] ──GPIO──► [ESP32] ──USB Serial──► [PC running main.py]
│
[Industrial Camera / USB Cam]

Belt speed → exposure guide:

| Belt Speed | Recommended Exposure |
|-----------|---------------------|
| 0.1 m/s | `-4` (~8ms) |
| 0.3 m/s | `-6` (~1ms) |
| 0.5 m/s | `-7` + strobe |
| 1.0 m/s | Strobe mandatory |

---

## 📋 Requirements

- Python 3.8 – 3.11
- Windows 10 / Ubuntu 20.04+
- Webcam or industrial USB camera
- 8GB RAM minimum (16GB recommended)
- NVIDIA GPU optional (set `use_gpu=True` for 5–10x speed)

---

## 🗺️ Roadmap

- [ ] Fine-tune PaddleOCR on company-specific label dataset
- [ ] ONNX Runtime / TensorRT export for edge deployment
- [ ] Web dashboard for live log monitoring
- [ ] Docker container for easy deployment

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 🙋 Author

**Your Name**
[LinkedIn](https://linkedin.com/in/yourprofile) · [GitHub](https://github.com/yourusername)