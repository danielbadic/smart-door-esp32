from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    render_template,
    redirect,
    url_for,
    session,
    Response,
)
import os
import threading
import time
import uuid
import contextlib
import tempfile
import atexit
from datetime import datetime
from deepface import DeepFace
import json
import secrets
import cv2
import requests
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Dict, Any, Optional
import shutil

from telegram_bot import create_telegram_bot, TelegramConfig


app = Flask(__name__, static_folder="static")
app.secret_key = secrets.token_hex(16)

UPLOAD_FOLDER = "uploads"
KNOWN_FOLDER = "known_faces"
STATIC_FOLDER = "static"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(KNOWN_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)

ADMIN_USER = os.getenv("ADMIN_USER", "admin")  # Default to "admin" if not set
ADMIN_PASS = os.getenv("ADMIN_PASS", "default")  # Default to "default" if not set

ESP32_IP = "192.168.0.100"

notifications = []

sse_clients = []

access_history = []  # List to store access attempts with status

# Thread pool
executor = ThreadPoolExecutor(max_workers=2)

# Thread locks for thread safety
notifications_lock = threading.Lock()
access_history_lock = threading.Lock()
sse_clients_lock = threading.Lock()

telegram_bot = create_telegram_bot()


class ResourceManager:
    """Manages temporary files and resources"""

    def __init__(self):
        self.temp_files = set()
        self.lock = threading.Lock()
        atexit.register(self.cleanup_all)

    @contextlib.contextmanager
    def temp_image_file(self, suffix=".jpg"):
        """Context manager for temporary image files"""
        temp_file = tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, dir=UPLOAD_FOLDER
        )
        temp_path = temp_file.name
        temp_file.close()

        with self.lock:
            self.temp_files.add(temp_path)

        try:
            yield temp_path
        finally:
            self.cleanup_file(temp_path)

    def cleanup_file(self, file_path: str):
        """Clean up a specific temporary file"""
        with self.lock:
            if file_path in self.temp_files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    self.temp_files.discard(file_path)
                except Exception as e:
                    print(f"Error cleaning up {file_path}: {e}")

    def cleanup_all(self):
        """Clean up all temporary files"""
        with self.lock:
            for file_path in list(self.temp_files):
                self.cleanup_file(file_path)


class FaceRecognitionService:
    """Centralized face recognition service"""

    @staticmethod
    def recognize_face(image_path: str) -> Dict[str, Any]:
        """Unified face recognition logic"""
        try:
            result = DeepFace.find(
                img_path=image_path,
                db_path=KNOWN_FOLDER,
                model_name="VGG-Face",
                enforce_detection=False,
            )

            match_found = len(result) > 0 and not result[0].empty
            recognized_person = None

            if match_found:
                best_match_path = result[0].iloc[0]["identity"]
                recognized_person = os.path.splitext(os.path.basename(best_match_path))[
                    0
                ]

            return {
                "access_granted": match_found,
                "recognized_person": recognized_person,
                "status": "granted" if match_found else "denied",
            }
        except Exception as e:
            return {
                "access_granted": False,
                "recognized_person": None,
                "status": "error",
                "error": str(e),
            }


class AccessRecordManager:
    """Manages access records and notifications"""

    @staticmethod
    def generate_timestamp() -> str:
        """Centralized timestamp generation"""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def create_access_record(
        filename: str, recognition_result: Dict, method: str = "automatic"
    ) -> Dict:
        """Centralized access record creation"""
        return {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "filename": filename,
            "image_url": f"/uploads/{filename}",
            "access_granted": recognition_result["access_granted"],
            "status": recognition_result["status"],
            "method": method,
            "recognized_person": recognition_result.get("recognized_person"),
            "recognition_result": (
                "Persoană recunoscută"
                if recognition_result["access_granted"]
                else "Persoană necunoscută"
            ),
        }

    @staticmethod
    def add_access_record(record: Dict):
        """Thread-safe record addition"""
        with access_history_lock:
            access_history.insert(0, record)
            if len(access_history) > 50:
                access_history.pop()

    @staticmethod
    def create_notification(record: Dict) -> Dict:
        """Create notification from access record"""
        return {
            "id": str(uuid.uuid4()),
            "timestamp": record["timestamp"],
            "filename": record["filename"],
            "image_url": record["image_url"],
            "access_granted": record["access_granted"],
            "status": record["status"],
            "recognition_result": record["recognition_result"],
            "method": record["method"],
        }

    @staticmethod
    def add_notification(notification: Dict):
        """Thread-safe notification addition"""
        with notifications_lock:
            notifications.insert(0, notification)
            if len(notifications) > 20:
                notifications.pop()


class ESP32Controller:
    """Handles ESP32 communication"""

    @staticmethod
    def open_door() -> Dict[str, Any]:
        """Centralized door opening logic"""
        try:
            response = requests.get(
                f"http://{ESP32_IP}/control?action=open", timeout=10
            )
            if response.status_code == 200:
                print("[🚪] Comandă de deschidere trimisă către ESP32")
                return {"success": True, "message": "Door opened successfully"}
            else:
                print(
                    f"[❌] Eroare la trimiterea comenzii către ESP32: {response.status_code}"
                )
                return {
                    "success": False,
                    "message": f"ESP32 error: {response.status_code}",
                }
        except requests.exceptions.RequestException as e:
            print(f"[❌] Eroare de conexiune cu ESP32: {str(e)}")
            return {"success": False, "message": f"Connection error: {str(e)}"}

    @staticmethod
    def capture_image() -> Optional[bytes]:
        """Capture image from ESP32"""
        try:
            response = requests.get(f"http://{ESP32_IP}/capture", timeout=10)
            if response.status_code == 200:
                return response.content
            return None
        except requests.exceptions.RequestException:
            return None


def async_task(func):
    """Decorator for running tasks asynchronously"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        def run_task():
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f"Async task error: {str(e)}")
                return None

        # Submit to thread pool
        future = executor.submit(run_task)
        return future

    return wrapper


@async_task
def process_face_recognition_async(image_path: str, method: str = "automatic"):
    """Async face recognition processing with Telegram notifications"""
    print(f"[🤖] Starting face recognition for {image_path}")

    recognition_result = FaceRecognitionService.recognize_face(image_path)

    # Create access record
    filename = os.path.basename(image_path)
    record = AccessRecordManager.create_access_record(
        filename, recognition_result, method
    )
    AccessRecordManager.add_access_record(record)

    # Create and add notification
    notification = AccessRecordManager.create_notification(record)
    AccessRecordManager.add_notification(notification)

    print(
        f"[🤖] Acces {'PERMIS' if recognition_result['access_granted'] else 'REFUZAT'}"
    )

    # SEND TELEGRAM NOTIFICATION HERE
    try:
        telegram_sent = telegram_bot.send_visitor_notification(
            access_granted=recognition_result["access_granted"],
            recognized_person=recognition_result.get("recognized_person"),
            image_path=image_path,
        )
        print(
            f"[📱] Telegram notification: {'✅ Sent' if telegram_sent else '❌ Failed'}"
        )
    except Exception as e:
        print(f"[📱] Telegram notification error: {e}")

    # Handle door opening
    if recognition_result["access_granted"]:
        door_result = ESP32Controller.open_door()
        if door_result["success"]:
            print("[🚪] Ușa deschisă automat pentru persoană recunoscută")

    # Determine event type and notify web clients
    if method == "automatic":
        event_type = "new_visitor"
    elif method == "stream_detection":
        if recognition_result["access_granted"]:
            event_type = "stream_face_recognized"
        else:
            event_type = "stream_face_denied"
    else:
        event_type = "face_recognition_complete"

    notify_clients(json.dumps({"type": event_type, "data": notification}))
    return record


# Global resource manager
resource_manager = ResourceManager()


def notify_clients(message):
    """Thread-safe client notification"""
    with sse_clients_lock:
        # add to end
        for client_queue in sse_clients:
            try:
                client_queue.append(message)
            except Exception as e:
                print(f"Error notifying client: {e}")


@app.route("/")
def index():
    if "logged_in" not in session:
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (
            request.form["username"] == ADMIN_USER
            and request.form["password"] == ADMIN_PASS
        ):
            session["logged_in"] = True
            return redirect(url_for("index"))
        else:
            error = "Credențiale invalide. Încercați din nou."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/api/history")
def get_history():
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    files = []
    try:
        for filename in sorted(os.listdir(UPLOAD_FOLDER), reverse=True):
            if filename.lower().endswith(".jpg"):
                # timestamp (format: visitor_YYYYMMDD_HHMMSS.jpg)
                try:
                    timestamp_str = (
                        filename.replace("visitor_", "")
                        .replace("manual_capture_", "")
                        .replace("stream_capture_", "")
                        .replace(".jpg", "")
                    )
                    if "_" in timestamp_str:
                        date_part = timestamp_str.split("_")[0]  # YYYYMMDD
                        time_part = timestamp_str.split("_")[1]  # HHMMSS

                        formatted_date = (
                            f"{date_part[6:8]}.{date_part[4:6]}.{date_part[0:4]}"
                        )
                        formatted_time = (
                            f"{time_part[0:2]}:{time_part[2:4]}:{time_part[4:6]}"
                        )

                        files.append(
                            {
                                "filename": filename,
                                "url": f"/uploads/{filename}",
                                "date": formatted_date,
                                "time": formatted_time,
                            }
                        )
                    else:
                        # Fallback
                        files.append(
                            {
                                "filename": filename,
                                "url": f"/uploads/{filename}",
                                "date": "Necunoscut",
                                "time": "Necunoscut",
                            }
                        )
                except Exception as e:
                    print(f"Error parsing filename {filename}: {e}")
                    files.append(
                        {
                            "filename": filename,
                            "url": f"/uploads/{filename}",
                            "date": "Necunoscut",
                            "time": "Necunoscut",
                        }
                    )
    except Exception as e:
        print(f"Error reading upload folder: {e}")

    return jsonify(files)


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    if "logged_in" not in session:
        return redirect(url_for("login"))
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_FOLDER, filename)


@app.route("/api/notifications")
def get_notifications():
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    with notifications_lock:
        return jsonify(notifications.copy())


@app.route("/api/notifications/clear", methods=["POST"])
def clear_notifications():
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    with notifications_lock:
        notifications.clear()

    return jsonify({"success": True})


@app.route("/api/access/grant/<filename>", methods=["POST"])
def grant_access(filename):
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    try:
        # Find and update the access record
        record_updated = False
        notification_updated = False

        # Update access history
        with access_history_lock:
            for record in access_history:
                if record["filename"] == filename:
                    record["access_granted"] = True
                    record["status"] = "granted"
                    record["method"] = f"{record['method']}_manual_override"
                    record["recognition_result"] = "Acces permis manual"
                    record["manual_grant_timestamp"] = datetime.now().isoformat()
                    record_updated = True
                    break

        # Update notifications
        with notifications_lock:
            for notification in notifications:
                if notification["filename"] == filename:
                    notification["access_granted"] = True
                    notification["status"] = "granted"
                    notification["recognition_result"] = "Acces permis manual"
                    notification_updated = True
                    break

        # Notify clients about the status change
        if record_updated:
            # Find the updated record to send to clients
            updated_record = None
            with access_history_lock:
                for record in access_history:
                    if record["filename"] == filename:
                        updated_record = record
                        break

            if updated_record:
                notify_clients(
                    json.dumps(
                        {"type": "access_granted_manual", "data": updated_record}
                    )
                )

        return jsonify(
            {
                "success": True,
                "message": f"Acces permis pentru {filename} - se deschide ușa...",
                "record_updated": record_updated,
                "notification_updated": notification_updated,
            }
        )

    except Exception as e:
        print(f"[❌] Eroare la permiterea accesului: {str(e)}")
        return jsonify({"success": False, "message": f"Eroare: {str(e)}"}), 500


@app.route("/api/door/open", methods=["POST"])
def open_door():
    """Endpoint dedicat pentru deschiderea ușii din dashboard"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    door_result = ESP32Controller.open_door()

    if door_result["success"]:
        telegram_bot.send_door_opened_notification("manual")
        return jsonify({"success": True, "message": "Ușa a fost deschisă cu succes"})
    else:
        return jsonify({"success": False, "message": door_result["message"]}), 500


@app.route("/api/camera/status")
def camera_status():
    """Verifică statusul conexiunii cu camera ESP32"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    try:
        response = requests.get(f"http://{ESP32_IP}/capture", timeout=3)
        if response.status_code == 200:
            return jsonify({"status": "online", "ip": ESP32_IP})
        else:
            return jsonify({"status": "offline", "ip": ESP32_IP})
    except requests.exceptions.RequestException:
        return jsonify({"status": "offline", "ip": ESP32_IP})


@app.route("/api/settings/camera-ip", methods=["POST"])
def update_camera_ip():
    """Actualizează IP-ul camerei ESP32"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    global ESP32_IP
    data = request.get_json()
    new_ip = data.get("ip", "").strip()

    if new_ip:
        ESP32_IP = new_ip
        print(f"[⚙️] IP cameră actualizat la: {ESP32_IP}")
        return jsonify(
            {"success": True, "message": f"IP cameră actualizat la {ESP32_IP}"}
        )
    else:
        return jsonify({"success": False, "message": "IP invalid"}), 400


@app.route("/api/access-history")
def get_access_history():
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    with access_history_lock:
        return jsonify(access_history.copy())


@app.route("/upload", methods=["POST"])
def upload():
    timestamp = AccessRecordManager.generate_timestamp()
    filename = f"visitor_{timestamp}.jpg"
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(file_path, "wb") as f:
        f.write(request.data)

    print(f"[📸] Imagine primită și salvată: {file_path}")

    # Process face recognition asynchronously
    future = process_face_recognition_async(file_path, "automatic")

    # Return immediate response - ESP32 expects access_granted field
    return (
        jsonify(
            {
                "status": "processing",
                "message": "Image uploaded, processing face recognition...",
                "filename": filename,
                "access_granted": False,  # Will be updated by background process
            }
        ),
        200,
    )


@app.route("/events")
def sse():
    if "logged_in" not in session:
        return redirect(url_for("login"))

    def event_stream():
        client_queue = []
        with sse_clients_lock:
            sse_clients.append(client_queue)

        try:
            # ping
            yield 'data: {"type": "connected"}\n\n'

            while True:
                if client_queue:
                    msg = client_queue.pop(0)
                    yield f"data: {msg}\n\n"
                else:
                    # Keep-alive
                    yield 'data: {"type": "ping"}\n\n'

                time.sleep(1)
        except GeneratorExit:
            # clean client when disconnecting
            with sse_clients_lock:
                if client_queue in sse_clients:
                    sse_clients.remove(client_queue)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/take_photo", methods=["POST"])
def take_photo():
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    def capture_and_process():
        try:
            # Capture image from ESP32
            image_data = ESP32Controller.capture_image()
            if image_data:
                timestamp = AccessRecordManager.generate_timestamp()
                filename = f"manual_capture_{timestamp}.jpg"
                file_path = os.path.join(UPLOAD_FOLDER, filename)

                with open(file_path, "wb") as f:
                    f.write(image_data)

                print(f"[📷] Captură manuală salvată: {file_path}")

                # Process face recognition
                recognition_result = FaceRecognitionService.recognize_face(file_path)
                record = AccessRecordManager.create_access_record(
                    filename, recognition_result, "manual"
                )
                AccessRecordManager.add_access_record(record)

                # Create and add notification
                notification = AccessRecordManager.create_notification(record)
                AccessRecordManager.add_notification(notification)

                # Handle door opening if access granted
                if recognition_result["access_granted"]:
                    door_result = ESP32Controller.open_door()
                    if door_result["success"]:
                        print(
                            "[🚪] Ușa deschisă automat pentru persoană recunoscută (captură manuală)"
                        )

                # Notify clients
                notify_clients(
                    json.dumps(
                        {
                            "type": "manual_capture_with_recognition",
                            "data": notification,
                        }
                    )
                )

                return record
            else:
                return {"success": False, "message": "ESP32 capture failed"}
        except Exception as e:
            print(f"[❌] Eroare la captura manuală: {str(e)}")
            return {"success": False, "message": str(e)}

    # Run in background thread
    thread = threading.Thread(target=capture_and_process)
    thread.daemon = True
    thread.start()

    return (
        jsonify(
            {"success": True, "message": "Capture started, processing in background..."}
        ),
        202,
    )


@app.route("/api/faces", methods=["GET"])
def get_known_faces():
    """Get list of known faces"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    faces = []
    try:
        for filename in os.listdir(KNOWN_FOLDER):
            if filename.lower().endswith((".jpg", ".jpeg", ".png")):
                faces.append(
                    {
                        "filename": filename,
                        "name": filename.replace(".jpg", "")
                        .replace(".jpeg", "")
                        .replace(".png", ""),
                        "url": f"/known_faces/{filename}",
                    }
                )
    except Exception as e:
        print(f"Error reading known faces: {e}")

    return jsonify(faces)


@app.route("/api/faces/add", methods=["POST"])
def add_known_face():
    """Add a new known face"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    try:
        data = request.get_json()
        name = data.get("name", "").strip()
        image_source = data.get("source")  # 'upload' or 'capture' or 'history'

        if not name:
            return (
                jsonify({"success": False, "message": "Numele este obligatoriu"}),
                400,
            )

        # Sanitize filename
        safe_name = "".join(
            c for c in name if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        safe_name = safe_name.replace(" ", "_")

        if image_source == "capture":
            # Capture new image from ESP32
            image_data = ESP32Controller.capture_image()
            if image_data:
                filename = f"{safe_name}.jpg"
                file_path = os.path.join(KNOWN_FOLDER, filename)

                with open(file_path, "wb") as f:
                    f.write(image_data)

                print(f"[👤] Față nouă adăugată prin captură: {filename}")
                return jsonify(
                    {
                        "success": True,
                        "message": f"Față {name} adăugată cu succes!",
                        "filename": filename,
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "Eroare la captura de pe ESP32",
                        }
                    ),
                    500,
                )

        elif image_source == "history":
            # Copy from history
            history_filename = data.get("filename")
            if not history_filename:
                return (
                    jsonify(
                        {"success": False, "message": "Filename necesar pentru istoric"}
                    ),
                    400,
                )

            history_path = os.path.join(UPLOAD_FOLDER, history_filename)
            if not os.path.exists(history_path):
                return (
                    jsonify(
                        {"success": False, "message": "Imaginea din istoric nu există"}
                    ),
                    404,
                )

            filename = f"{safe_name}.jpg"
            new_path = os.path.join(KNOWN_FOLDER, filename)

            # Copy file
            shutil.copy2(history_path, new_path)

            print(f"[👤] Față nouă adăugată din istoric: {filename}")
            return jsonify(
                {
                    "success": True,
                    "message": f"Față {name} adăugată cu succes din istoric!",
                    "filename": filename,
                }
            )

        else:
            return jsonify({"success": False, "message": "Sursă invalidă"}), 400

    except Exception as e:
        print(f"[❌] Eroare la adăugarea feței: {str(e)}")
        return jsonify({"success": False, "message": f"Eroare: {str(e)}"}), 500


@app.route("/api/faces/upload", methods=["POST"])
def upload_known_face():
    """Upload a new known face from file"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    if "file" not in request.files:
        return (
            jsonify({"success": False, "message": "Nu a fost selectat niciun fișier"}),
            400,
        )

    file = request.files["file"]
    name = request.form.get("name", "").strip()

    if file.filename == "" or not name:
        return (
            jsonify({"success": False, "message": "Fișier și nume sunt obligatorii"}),
            400,
        )

    if file and file.filename.lower().endswith((".jpg", ".jpeg", ".png")):
        # Sanitize filename
        safe_name = "".join(
            c for c in name if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        safe_name = safe_name.replace(" ", "_")
        filename = f"{safe_name}.jpg"

        file_path = os.path.join(KNOWN_FOLDER, filename)
        file.save(file_path)

        print(f"[👤] Față nouă încărcată: {filename}")
        return jsonify(
            {
                "success": True,
                "message": f"Față {name} încărcată cu succes!",
                "filename": filename,
            }
        )

    return jsonify({"success": False, "message": "Format de fișier invalid"}), 400


@app.route("/api/faces/delete/<filename>", methods=["DELETE"])
def delete_known_face(filename):
    """Delete a known face"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    file_path = os.path.join(KNOWN_FOLDER, filename)

    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            print(f"[👤] Față ștearsă: {filename}")
            return jsonify({"success": True, "message": "Față ștearsă cu succes!"})
        except Exception as e:
            print(f"Error deleting face: {e}")
            return jsonify({"success": False, "message": "Eroare la ștergere"}), 500
    else:
        return jsonify({"success": False, "message": "Fișierul nu există"}), 404


@app.route("/known_faces/<filename>")
def serve_known_face(filename):
    """Serve known face images"""
    if "logged_in" not in session:
        return redirect(url_for("login"))
    return send_from_directory(KNOWN_FOLDER, filename)


@app.route("/api/detect-face-stream", methods=["POST"])
def detect_face_stream():
    """Detect faces in stream frames with Telegram notifications"""
    if "logged_in" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    try:
        image_file = request.files.get("image")
        if not image_file:
            return jsonify({"face_detected": False}), 400

        # Create permanent filename first
        timestamp = AccessRecordManager.generate_timestamp()
        stream_filename = f"stream_capture_{timestamp}.jpg"
        stream_path = os.path.join(UPLOAD_FOLDER, stream_filename)

        # Save the image directly to permanent location
        image_file.save(stream_path)

        try:
            # Check if there's actually a face in the image
            faces = DeepFace.extract_faces(
                img_path=stream_path,
                enforce_detection=True,
                detector_backend="opencv",
            )

            # If we get here, at least one face was detected
            print(
                f"[👁️] Face detected in stream - processing with Telegram notification..."
            )

            # Process face recognition asynchronously (this will send Telegram notification)
            future = process_face_recognition_async(stream_path, "stream_detection")

            return jsonify(
                {
                    "face_detected": True,
                    "timestamp": datetime.now().isoformat(),
                    "status": "processing",
                    "filename": stream_filename,
                }
            )

        except ValueError as face_error:
            # No face detected, del file
            try:
                if os.path.exists(stream_path):
                    os.remove(stream_path)
            except Exception as cleanup_error:
                print(f"Error cleaning up file: {cleanup_error}")

            print(f"[👁️] No face detected in stream frame")
            return jsonify(
                {
                    "face_detected": False,
                    "access_granted": False,
                    "timestamp": datetime.now().isoformat(),
                    "status": "no_face",
                }
            )

        except Exception as e:
            # Remove the saved file on error
            try:
                if os.path.exists(stream_path):
                    os.remove(stream_path)
            except Exception as cleanup_error:
                print(f"Error cleaning up file: {cleanup_error}")

            print(f"[❌] Stream detection error: {str(e)}")
            return jsonify({"face_detected": False, "status": "error"}), 500

    except Exception as e:
        print(f"[❌] Stream detection request error: {str(e)}")
        return jsonify({"face_detected": False, "status": "error"}), 500


def door_controller_callback():
    """Simple callback that sends SSE to frontend to handle door opening"""
    print(f"[🚪] Telegram door command - sending SSE to frontend")

    try:
        # Send SSE event to frontend
        telegram_door_event = {
            "type": "telegram_open_door",
            "message": "Telegram door open command received",
            "timestamp": datetime.now().isoformat(),
        }

        notify_clients(json.dumps(telegram_door_event))

        return {"success": True, "message": "Command sent to frontend"}

    except Exception as e:
        print(f"[🚪] Error sending SSE: {e}")
        return {"success": False, "message": f"SSE error: {str(e)}"}


print(f"[📱] Setting door controller callback...")
telegram_bot.set_door_controller(door_controller_callback)

# Start command listener
print(f"[📱] Starting Telegram command listener...")
telegram_bot.start_command_listener()
print(f"[📱] Telegram bot initialization complete")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
