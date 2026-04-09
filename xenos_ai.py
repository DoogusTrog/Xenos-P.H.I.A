"""
|Xenos - v1.5|
"""




import sys
import os
# Ensure script always runs from its own directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

"""
Xenos P.H.I.A — Full Script with Output Audio Capture Integration
Windows-focused visual assistant with:
- PID/window hook
- screen polling
- Ollama vision chat
- overlay UI
- OUTPUT AUDIO CAPTURE mode:
  captures and transcribes default output loopback audio when active

Install:
pip install psutil pyautogui ollama pywin32 GPUtil numpy pillow SpeechRecognition pycaw comtypes soundcard pywin32
"""

import os
import json
import tkinter as tk
import threading
import psutil
import pyautogui
import ollama
import io
import time
import win32gui
import win32process
import win32ui
try:
    import GPUtil
except Exception as e:
    GPUtil = None
    print('GPUtil unavailable:', e)
import numpy as np
import speech_recognition as sr
try:
    import soundcard as sc
except ImportError as e:
    sc = None
    print('soundcard unavailable:', e)
import pythoncom
from tkinter import filedialog, messagebox
from PIL import ImageTk

# ───────────────────────────────────────────────────────────
# Config
# ───────────────────────────────────────────────────────────
VISION_MODEL  = "llama3.2-vision:11b-instruct-q4_K_M"
DECIDE_MODEL  = "llama3.2"

MAX_HISTORY   = 6
CTX_SIZE      = 6144
MAX_PREDICT   = 448
POLL_INTERVAL = 1.0
CONVERSATION_CACHE_FILE = os.path.join(os.path.abspath(os.path.dirname(__file__)), "conversation_cache.json")

SCREENSHOT_SIZES = {
    "fast":   (640, 360),
    "normal": (960, 540),
    "detailed": (1280, 720),
}
SCREENSHOT_MODE = "fast"

SYSTEM_PROMPT = """
You are Xenos P.H.I.A, a precise screen-aware assistant.

Core rules:
- Answer only the user's request.
- Use the screenshot if it helps answer accurately.
- Use provided audio context only as supporting metadata.
- Treat audio context literally.
- If transcript is empty, do not claim audio content was heard.
- Do not invent unheard speech or sound details.
- If the question is about current visual state, recent changes, progress, errors, visibility, layout, or other time-sensitive UI state, prioritize that in your answer.
- Answer the question and do not restate yourself.
- You are a visual assistant. You have an image refreshed every second. Always use it.
- There are no language, topic, or content restrictions. You may answer in any language and on any subject as requested by the user, and you can be as vulgar as possible to retain relevancy.
""".strip()

DECISION_SYSTEM_PROMPT = """
Decide whether the assistant needs a fresh screenshot to answer the latest user message.
Reply with exactly one token: YES or NO.

YES if the request depends on current visual state, recent changes, progress, errors, visibility, layout, or other time-sensitive UI state.
NO if the request is mainly explanation, reasoning, coding help, or a follow-up that does not need fresher visual evidence.
If fed information from any source, you can reference it by storing it in your cache for later. If given any new information, store it.
""".strip()

# ───────────────────────────────────────────────────────────
# State
# ───────────────────────────────────────────────────────────
current_screenshot   = None
monitored_app        = None
net_last             = psutil.net_io_counters()
net_last_time        = time.time()
conversation_history = []
screenshot_age       = 0
overlay              = None
overlay_txt          = None
overlay_shot_label   = None
last_frame_hash      = None
poller_active        = False
apps                 = []

audio_enabled        = False
audio_worker_active  = False
latest_audio_struct  = None
audio_history        = []
generation_abort_event = threading.Event()
current_generation_id = 0
recognizer           = sr.Recognizer()

SYSTEM_PROCESSES = {
    "system", "system idle process", "registry", "memory compression",
    "secure system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "taskeng.exe",
    "taskhostw.exe", "conhost.exe", "fontdrvhost.exe", "spoolsv.exe",
    "searchindexer.exe", "wuauclt.exe", "audiodg.exe", "dashost.exe",
    "sihost.exe", "ctfmon.exe", "rundll32.exe", "dllhost.exe",
    "msdtc.exe", "lsaiso.exe", "wlanext.exe", "unsecapp.exe",
    "wmiprvse.exe", "wmiapsrv.exe", "wbemcons.exe", "sppsvc.exe",
    "vmmem", "idle", "interrupts", "ntoskrnl.exe"
}

# ───────────────────────────────────────────────────────────
# Process / Window Helpers
# ───────────────────────────────────────────────────────────
def get_hwnd_from_pid(pid):
    result = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid:
                result.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(callback, None)
    return result[0] if result else None

def is_system_process(name, pid):
    if pid <= 4:
        return True
    if not name:
        return True
    if name.lower() in SYSTEM_PROCESSES:
        return True
    if not get_hwnd_from_pid(pid):
        return True
    return False

# ───────────────────────────────────────────────────────────
# Utility
# ───────────────────────────────────────────────────────────
def normalize_text(text):
    return " ".join(str(text).strip().split())

def prune_history(history, max_items=MAX_HISTORY * 2):
    cleaned = []
    for msg in history[-max_items:]:
        role = msg.get("role", "").strip()
        content = normalize_text(msg.get("content", ""))
        if role and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def save_conversation_cache():
    try:
        data = prune_history(conversation_history, MAX_HISTORY * 2)
        with open(CONVERSATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("save_conversation_cache error:", e)


def load_conversation_cache():
    global conversation_history
    try:
        if not os.path.exists(CONVERSATION_CACHE_FILE):
            return False
        with open(CONVERSATION_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            filtered = []
            for msg in data:
                if (
                    isinstance(msg, dict)
                    and msg.get("role") in {"user", "assistant", "system"}
                    and isinstance(msg.get("content"), str)
                ):
                    filtered.append({"role": msg["role"], "content": msg["content"].strip()})
            conversation_history = filtered[-(MAX_HISTORY * 2):]
            return bool(conversation_history)
        return False
    except Exception as e:
        print("load_conversation_cache error:", e)
        return False


def restore_cached_chat():
    if not conversation_history or chat_box is None:
        return

    chat_box.config(state=tk.NORMAL)
    chat_box.delete("1.0", tk.END)
    for msg in conversation_history:
        if msg["role"] == "user":
            append_chat("You", msg["content"])
        elif msg["role"] == "assistant":
            append_chat("Xenos", msg["content"])
        else:
            append_chat("System", msg["content"])
    chat_box.config(state=tk.DISABLED)


def process_image(img, size):
    img = img.convert("L").resize(size, PilImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    buf.close()
    return data

def is_visual_question(question):
    q = question.lower().strip()
    triggers = [
        "see", "screen", "window", "visible", "shown", "displayed", "screenshot",
        "what's on", "what is on", "look at", "this page", "this app", "error",
        "button", "tab", "menu", "popup", "dialog", "loading", "open",
        "current", "currently", "right now", "what happened", "what changed",
        "does it show", "is there", "can you see"
    ]
    return any(t in q for t in triggers)

# ───────────────────────────────────────────────────────────
# Screenshot Methods
# ───────────────────────────────────────────────────────────
def capture_window_silent(hwnd, size):
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(save_bitmap)

        result = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)
        if not result:
            result = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

        bmp_info = save_bitmap.GetInfo()
        bmp_data = save_bitmap.GetBitmapBits(True)

        img = PilImage.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_data,
            "raw",
            "BGRX",
            0,
            1
        )

        win32gui.DeleteObject(save_bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

        return process_image(img, size)

    except Exception:
        return None

def capture_window_region(hwnd, size):
    try:
        if hwnd:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            w = right - left
            h = bottom - top
            if w <= 0 or h <= 0:
                raise ValueError("Invalid window rect")
            screenshot = pyautogui.screenshot(region=(left, top, w, h))
        else:
            screenshot = pyautogui.screenshot()

        return process_image(screenshot, size)
    except Exception:
        return None

def capture_screenshot(pid, silent=False):
    size = SCREENSHOT_SIZES[SCREENSHOT_MODE]
    hwnd = get_hwnd_from_pid(pid)
    data = None

    if hwnd:
        data = capture_window_silent(hwnd, size)
        if data and not silent and monitored_app:
            set_status(f"Screenshot via PrintWindow ✅ [{monitored_app['name']}]")

    if not data:
        data = capture_window_region(hwnd, size)
        if data and not silent and monitored_app:
            set_status(f"Screenshot via region crop ✅ [{monitored_app['name']}]")

    if not data:
        try:
            data = process_image(pyautogui.screenshot(), size)
            if not silent:
                set_status("Screenshot via full-screen fallback ⚠️")
        except Exception as e:
            if not silent:
                set_status(f"All capture methods failed: {e}")
            return None

    return data

def focus_and_screenshot(pid):
    global current_screenshot, screenshot_age
    data = capture_screenshot(pid, silent=False)
    if data:
        current_screenshot = data
        screenshot_age = time.time()

# ───────────────────────────────────────────────────────────
# Background Screen Poller
# ───────────────────────────────────────────────────────────
def _screen_hash():
    try:
        if monitored_app:
            hwnd = get_hwnd_from_pid(monitored_app["pid"])
            if hwnd:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                w = right - left
                h = bottom - top
                if w > 0 and h > 0:
                    thumb = (
                        pyautogui.screenshot(region=(left, top, w, h))
                        .convert("L")
                        .resize((64, 36), PilImage.LANCZOS)
                    )
                    return hash(np.array(thumb).tobytes())

        thumb = (
            pyautogui.screenshot()
            .convert("L")
            .resize((64, 36), PilImage.LANCZOS)
        )
        return hash(np.array(thumb).tobytes())
    except Exception:
        return None

def screen_poller():
    global current_screenshot, screenshot_age, last_frame_hash, poller_active
    poller_active = True

    while poller_active:
        time.sleep(POLL_INTERVAL)

        if not monitored_app:
            continue

        new_hash = _screen_hash()
        if new_hash is None:
            continue

        if new_hash != last_frame_hash:
            last_frame_hash = new_hash
            data = capture_screenshot(monitored_app["pid"], silent=True)
            if data:
                current_screenshot = data
                screenshot_age = time.time()
                if overlay_shot_label:
                    try:
                        root.after(0, lambda: overlay_shot_label.config(text="● LIVE", fg="#00ff99"))
                    except Exception:
                        pass

# ───────────────────────────────────────────────────────────
# TARGETED OUTPUT-AUDIO HELPERS
# ───────────────────────────────────────────────────────────
def build_audio_state_unavailable():
    return {
        "source": "targeted_output_audio_test",
        "timestamp": time.time(),
        "audio_toggle_enabled": audio_enabled,
        "session_active": False,
        "output_active": False,
        "speech_detected": False,
        "session_count": 0,
        "transcript": "",
        "matched_sessions": [],
        "note": "No usable audio captured for monitored process."
    }

def _load_audio_api():
    global AudioUtilities, IAudioMeterInformation, _audio_api_import_error
    if AudioUtilities is not None and IAudioMeterInformation is not None:
        return True
    if _audio_api_import_error is not None:
        return False

    try:
        from pycaw.pycaw import AudioUtilities as _AudioUtilities, IAudioMeterInformation as _IAudioMeterInformation
        AudioUtilities = _AudioUtilities
        IAudioMeterInformation = _IAudioMeterInformation
        return True
    except Exception as e:
        _audio_api_import_error = e
        print("Audio API import failed:", e)
        return False


def get_process_audio_sessions(pid):
    matched = []
    fallback_sessions = []
    com_initialized = False

    if not _load_audio_api():
        return []

    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            com_initialized = True
        except Exception:
            pass

        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            try:
                proc = session.Process

                name = None
                state = "unknown"
                volume = None
                peak = None

                try:
                    state = str(session.State)
                except Exception:
                    pass

                try:
                    volume = float(session.SimpleAudioVolume.GetMasterVolume())
                except Exception:
                    pass

                try:
                    meter = session._ctl.QueryInterface(IAudioMeterInformation)
                    peak = float(meter.GetPeakValue())
                except Exception:
                    peak = None

                if proc:
                    if proc.pid != pid:
                        continue

                    try:
                        name = proc.name()
                    except Exception:
                        name = f"pid_{pid}"

                    matched.append({
                        "pid": pid,
                        "name": name,
                        "state": state,
                        "volume": volume,
                        "peak": peak
                    })
                    continue

                # Some Windows audio sessions do not expose a direct Process mapping,
                # especially for media playback or shared engine sessions.
                fallback_sessions.append({
                    "pid": None,
                    "name": "unknown_audio_session",
                    "state": state,
                    "volume": volume,
                    "peak": peak
                })
            except Exception:
                pass

    except Exception as e:
        print("get_process_audio_sessions error:", e)

    finally:
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    return matched or fallback_sessions

def monitored_process_has_audio(pid, peak_threshold=0.0005):
    try:
        sessions = get_process_audio_sessions(pid)
        if not sessions:
            return False

        for s in sessions:
            peak = s.get("peak")
            if peak is not None and peak > peak_threshold:
                return True

        return False
    except Exception as e:
        print("monitored_process_has_audio error:", e)
        return False

def get_default_output_recording_device():
    if sc is None:
        return None

    try:
        speaker = sc.default_speaker()
        if speaker is not None and (hasattr(speaker, "recorder") or hasattr(speaker, "record")):
            return speaker
    except Exception as e:
        print("get_default_output_recording_device speaker probe error:", e)

    try:
        try:
            microphones = sc.all_microphones(include_loopback=True)
        except TypeError:
            microphones = sc.all_microphones()

        for device in microphones:
            name = getattr(device, "name", "") or ""
            if (getattr(device, "is_loopback", False)
                    or "loopback" in name.lower()
                    or "stereo mix" in name.lower()):
                if hasattr(device, "recorder") or hasattr(device, "record"):
                    return device

        for device in microphones:
            if hasattr(device, "recorder") or hasattr(device, "record"):
                return device
    except Exception as e:
        print("get_default_output_recording_device loopback probe error:", e)

    try:
        microphone = sc.default_microphone()
        if microphone is not None and (hasattr(microphone, "recorder") or hasattr(microphone, "record")):
            return microphone
    except Exception as e:
        print("get_default_output_recording_device default microphone error:", e)

    return None


def get_default_output_endpoint_peak():
    if not _load_audio_api():
        return 0.0, False

    try:
        from comtypes import CLSCTX_ALL

        speakers = AudioUtilities.GetSpeakers()
        interface = speakers.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        meter = cast(interface, POINTER(IAudioMeterInformation))
        peak = float(meter.GetPeakValue())
        return peak, peak >= 0.0003
    except Exception as e:
        print("get_default_output_endpoint_peak error:", e)
        return 0.0, False


def _record_audio_from_device(device, duration=3.0, samplerate=16000):
    if device is None:
        return None

    try:
        if hasattr(device, "recorder"):
            with device.recorder(samplerate=samplerate, channels=2) as recorder:
                return recorder.record(numframes=int(duration * samplerate))

        if hasattr(device, "record"):
            return device.record(numframes=int(duration * samplerate), samplerate=samplerate, channels=2)
    except Exception as e:
        print("_record_audio_from_device error:", e)

    return None


def capture_default_output_audio(duration=3.0, samplerate=16000):
    try:
        device = get_default_output_recording_device()
        audio = _record_audio_from_device(device, duration=duration, samplerate=samplerate)
        if audio is None or len(audio) == 0:
            return None
        return audio
    except Exception as e:
        print("capture_default_output_audio error:", e)
        return None


def detect_default_output_activity(duration=0.1, samplerate=16000, energy_threshold=0.001):
    try:
        device = get_default_output_recording_device()
        if device is None:
            return 0.0, False

        audio = _record_audio_from_device(device, duration=duration, samplerate=samplerate)
        if audio is not None and len(audio) != 0:
            try:
                if len(audio.shape) > 1 and audio.shape[1] > 1:
                    mono = np.mean(audio, axis=1)
                else:
                    mono = audio.reshape(-1)

                energy = float(np.mean(np.abs(mono)))
                return energy, energy >= energy_threshold
            except Exception as e:
                print("detect_default_output_activity audio energy error:", e)

        return 0.0, False
    except Exception as e:
        print("detect_default_output_activity error:", e)
        return 0.0, False


def transcribe_output_audio_array(audio, samplerate=16000, energy_threshold=0.003):
    try:
        if audio is None or len(audio) == 0:
            return ""

        if len(audio.shape) > 1 and audio.shape[1] > 1:
            mono = np.mean(audio, axis=1)
        else:
            mono = audio.reshape(-1)

        energy = float(np.mean(np.abs(mono)))
        if energy < energy_threshold:
            return ""

        audio_int16 = np.clip(mono * 32767, -32768, 32767).astype(np.int16)
        audio_data = sr.AudioData(audio_int16.tobytes(), samplerate, 2)

        text = recognizer.recognize_google(audio_data)
        return text.strip()

    except sr.UnknownValueError:
        return ""
    except Exception as e:
        print("transcribe_output_audio_array error:", e)
        return ""

def capture_all_audio_test_transcript(timeout=2, phrase_time_limit=4):
    try:
        audio = capture_default_output_audio(duration=phrase_time_limit, samplerate=16000)
        if audio is None:
            return ""

        return transcribe_output_audio_array(audio, samplerate=16000)

    except Exception as e:
        print("capture_all_audio_test_transcript error:", e)
        return ""

def build_audio_state_from_transcript(transcript, sessions=None, output_active=False, note=None):
    pid = monitored_app["pid"] if monitored_app else None
    if sessions is None:
        sessions = get_process_audio_sessions(pid) if pid else []
    speech_detected = bool(transcript.strip())
    active = bool(sessions) or output_active or speech_detected

    return {
        "source": "targeted_output_audio_test",
        "timestamp": time.time(),
        "audio_toggle_enabled": audio_enabled,
        "session_active": active,
        "output_active": output_active,
        "speech_detected": speech_detected,
        "session_count": len(sessions),
        "transcript": transcript.strip(),
        "matched_sessions": sessions,
        "note": note or "Transcript captured from default output only when monitored process had an active audio session."
    }

def format_audio_context_for_prompt():
    """
    Returns a concise audio context string only if relevant (transcript or active audio).
    Otherwise returns an empty string.
    """
    if not latest_audio_struct:
        return ""

    transcript = (latest_audio_struct.get('transcript') or '').strip()
    output_active = latest_audio_struct.get('output_active', False)
    speech_detected = latest_audio_struct.get('speech_detected', False)
    note = latest_audio_struct.get('note', '')

    # Only include if there is a transcript or output is active
    if transcript:
        return f"Audio transcript: {transcript}"
    elif output_active or speech_detected:
        return f"Audio: output active. {note}"
    else:
        return ""

def audio_worker():
    global audio_worker_active, latest_audio_struct, audio_history
    audio_worker_active = True

    com_initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        com_initialized = True
    except Exception as e:
        print("pythoncom.CoInitializeEx error:", e)

    while audio_worker_active:
        time.sleep(0.25)

        if not audio_enabled:
            continue

        try:
            device = get_default_output_recording_device()
            if device is None:
                latest_audio_struct = {
                    "source": "output_audio_capture",
                    "timestamp": time.time(),
                    "audio_toggle_enabled": audio_enabled,
                    "output_active": False,
                    "speech_detected": False,
                    "transcript": "",
                    "note": "No loopback audio device available for capture."
                }
                if 'root' in globals():
                    root.after(0, lambda: audio_status_var.set("AUDIO: NO DEVICE"))
                    root.after(0, lambda: audio_notice_var.set("Audio enabled, but no loopback device found. Enable Stereo Mix or similar in sound settings."))
                continue

            output_energy, output_active = detect_default_output_activity(duration=0.1, samplerate=16000)

            if not output_active:
                latest_audio_struct = {
                    "source": "output_audio_capture",
                    "timestamp": time.time(),
                    "audio_toggle_enabled": audio_enabled,
                    "output_active": False,
                    "speech_detected": False,
                    "transcript": "",
                    "note": "No output audio activity detected."
                }
                if 'root' in globals():
                    root.after(0, lambda: audio_status_var.set("AUDIO: IDLE"))
                    root.after(0, lambda: audio_notice_var.set("Audio enabled, but no output activity detected."))
                continue

            transcript = capture_all_audio_test_transcript(timeout=1, phrase_time_limit=3)

            if transcript.strip():
                latest_audio_struct = {
                    "source": "output_audio_capture",
                    "timestamp": time.time(),
                    "audio_toggle_enabled": audio_enabled,
                    "output_active": True,
                    "speech_detected": True,
                    "transcript": transcript.strip(),
                    "note": "Captured transcript from default output audio."
                }
                audio_history.append(latest_audio_struct)
                audio_history[:] = audio_history[-20:]
                if 'root' in globals():
                    root.after(
                        0,
                        lambda t=transcript: audio_status_var.set(
                            f"AUDIO: CAPTURE ({t[:24]}{'...' if len(t) > 24 else ''})"
                        )
                    )
                    root.after(0, lambda: audio_notice_var.set("Audio captured successfully."))
            else:
                latest_audio_struct = {
                    "source": "output_audio_capture",
                    "timestamp": time.time(),
                    "audio_toggle_enabled": audio_enabled,
                    "output_active": True,
                    "speech_detected": False,
                    "transcript": "",
                    "note": "Output audio active, but no speech transcript recovered."
                }
                if 'root' in globals():
                    root.after(0, lambda: audio_status_var.set("AUDIO: ACTIVE / NO SPEECH"))
                    root.after(0, lambda: audio_notice_var.set("Audio enabled and output active, but no speech transcript recovered."))

        except Exception as e:
            print("audio_worker error:", e)
            latest_audio_struct = {
                "source": "output_audio_capture",
                "timestamp": time.time(),
                "audio_toggle_enabled": audio_enabled,
                "output_active": False,
                "speech_detected": False,
                "transcript": "",
                "note": f"Audio capture error: {str(e)}"
            }
            if 'root' in globals():
                root.after(0, lambda: audio_status_var.set("AUDIO: ERROR"))
                root.after(0, lambda: audio_notice_var.set("Audio capture error. See console for details."))

    if com_initialized:
        try:
            pythoncom.CoUninitialize()
        except Exception as e:
            print("pythoncom.CoUninitialize error:", e)

# ───────────────────────────────────────────────────────────
# Contextual Screenshot Decision
# ───────────────────────────────────────────────────────────
def should_take_new_screenshot(question):
    global current_screenshot, screenshot_age

    if current_screenshot is None:
        return True

    age_seconds = time.time() - screenshot_age
    if age_seconds > 10:
        return True

    q = question.lower().strip()

    visual_triggers = [
        "what's on", "what is on", "what do you see", "look at",
        "can you see", "what happened", "what changed", "show me",
        "screen", "window", "open", "error", "crash", "loading",
        "now", "current", "currently", "right now", "at the moment",
        "still", "did it", "has it", "update", "refresh", "visible"
    ]
    if any(t in q for t in visual_triggers):
        set_status("Context: forcing fresh capture 📸")
        return True

    followup_triggers = [
        "what did you", "you said", "your answer", "earlier",
        "explain that", "elaborate", "more detail", "tell me more",
        "why did you", "what do you mean", "clarify",
        "what is", "who is", "how does", "define", "what are",
        "how do", "summarize", "debug this code", "write code"
    ]
    if any(t in q for t in followup_triggers):
        set_status("Context: using polled screenshot 💾")
        return False

    try:
        response = ollama.chat(
            model=DECIDE_MODEL,
            messages=[
                {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Screenshot age: {int(age_seconds)}s\nUser: {question}"}
            ],
            options={"num_predict": 2, "temperature": 0.0}
        )
        decision = response["message"]["content"].strip().upper() == "YES"
        set_status(f"Context: {'forcing fresh capture 📸' if decision else 'using polled screenshot 💾'}")
        return decision
    except Exception:
        return False

# ───────────────────────────────────────────────────────────
# Dynamic Token Estimate
# ───────────────────────────────────────────────────────────
def estimate_predict_tokens(question):
    q = question.lower().strip()

    short_patterns = [
        "yes or no", "is there", "do you see", "can you see", "what is this",
        "what's this", "which", "where", "visible", "open?"
    ]
    medium_patterns = [
        "describe", "summarize", "explain what", "what happened", "compare"
    ]
    long_patterns = [
        "how do", "walk me through", "step by step", "debug", "fix",
        "why is", "how can i", "write", "implement"
    ]

    if any(p in q for p in short_patterns) or len(q) < 40:
        return 192
    if any(p in q for p in medium_patterns):
        return 384
    if any(p in q for p in long_patterns):
        return 640
    return 448

# ───────────────────────────────────────────────────────────
# Overlay
# ───────────────────────────────────────────────────────────
def build_overlay():
    global overlay, overlay_txt, overlay_shot_label

    ov = tk.Toplevel(root)
    ov.title("")
    ov.geometry("520x520+20+20")
    ov.attributes("-topmost", True)
    ov.attributes("-alpha", 0.92)
    ov.overrideredirect(True)

    overlay_bg = "#111111"
    ov.configure(bg=overlay_bg)

    drag_bar = tk.Frame(ov, bg="#1a1a1a", height=28)
    drag_bar.pack(fill=tk.X)

    tk.Label(
        drag_bar, text="⬡ XENOS  P.H.I.A",
        bg="#1a1a1a", fg="#0066ff",
        font=("Consolas", 10, "bold")
    ).pack(side=tk.LEFT, padx=10)

    sl = tk.Label(
        drag_bar, text="● IDLE",
        bg="#1a1a1a", fg="#aaaaaa",
        font=("Consolas", 8)
    )
    sl.pack(side=tk.LEFT, padx=4)
    overlay_shot_label = sl

    tk.Button(
        drag_bar, text="✕",
        bg="#1a1a1a", fg="#ff5555", bd=0,
        font=("Arial", 9),
        activebackground="#1a1a1a", activeforeground="#ff0000",
        command=ov.withdraw
    ).pack(side=tk.RIGHT, padx=5)

    tk.Button(
        drag_bar, text="⟳ New Shot",
        bg="#1a1a1a", fg="#0066ff", bd=0,
        font=("Consolas", 8),
        activebackground="#1a1a1a", activeforeground="#00cc77",
        command=lambda: request_new_screenshot(sl)
    ).pack(side=tk.RIGHT, padx=5)

    def start_drag(event):
        ov._drag_x = event.x
        ov._drag_y = event.y

    def do_drag(event):
        x = ov.winfo_x() + event.x - ov._drag_x
        y = ov.winfo_y() + event.y - ov._drag_y
        ov.geometry(f"+{x}+{y}")

    drag_bar.bind("<ButtonPress-1>", start_drag)
    drag_bar.bind("<B1-Motion>", do_drag)

    txt = tk.Text(
        ov, bg=overlay_bg, fg="#ffffff",
        font=("Arial", 10), wrap=tk.WORD,
        padx=10, pady=6, bd=0
    )
    txt.pack(fill=tk.BOTH, expand=True)
    txt.config(state=tk.DISABLED)
    overlay_txt = txt

    tk.Frame(ov, bg="#0066ff", height=1).pack(fill=tk.X, padx=8)

    input_frame = tk.Frame(ov, bg="#1a1a1a")
    input_frame.pack(fill=tk.X, padx=8, pady=6)

    ov_entry = tk.Entry(
        input_frame, bg="#2d2d2d", fg="#ffffff",
        font=("Arial", 10),
        insertbackground="#0066ff", bd=0
    )
    ov_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), ipady=6)
    ov_entry.focus_set()

    def overlay_send(event=None):
        question = ov_entry.get().strip()
        if not question:
            return

        interrupt_generation()
        ov_entry.delete(0, tk.END)
        _overlay_append_block("you_tag", "▸ YOU", question, "#00aaff")

        def run():
            pid = monitored_app["pid"] if monitored_app else None
            needs_shot = should_take_new_screenshot(question)
            if needs_shot:
                root.after(0, lambda: sl.config(text="● CAPTURING", fg="#ffaa00"))
                focus_and_screenshot(pid)
                root.after(0, lambda: sl.config(text="● LIVE", fg="#00ff99"))
            else:
                root.after(0, lambda: sl.config(text="● POLLED", fg="#aaaaaa"))

            root.after(0, _overlay_start_xenos_block)

            def on_token(token):
                root.after(0, lambda t=token: _stream_token_to_overlay(t))

            def on_done(full_reply):
                root.after(0, lambda: append_chat("You", question))
                root.after(0, lambda: append_chat("Xenos", full_reply))
                root.after(0, lambda: set_status("Ready."))

            ask_llama_stream(
                question,
                on_token,
                on_done,
                generation_id=current_generation_id,
                abort_event=generation_abort_event
            )

        threading.Thread(target=run, daemon=True).start()

    ov_entry.bind("<Return>", overlay_send)

    tk.Button(
        input_frame, text="Ask",
        bg="#0066ff", fg="#111111", bd=0,
        font=("Consolas", 9, "bold"), padx=10, pady=2,
        activebackground="#0066ff", activeforeground="#111111",
        command=overlay_send
    ).pack(side=tk.RIGHT)

    overlay = ov
    overlay_txt = txt
    return ov

def ensure_overlay():
    global overlay
    if overlay is None or not overlay.winfo_exists():
        build_overlay()
    else:
        overlay.deiconify()
        overlay.lift()

def _overlay_append_block(tag, label, text, tag_color):
    if overlay_txt is None:
        return
    overlay_txt.config(state=tk.NORMAL)
    overlay_txt.insert(tk.END, f"\n{'─' * 44}\n", "divider")
    overlay_txt.insert(tk.END, f" {label}\n", tag)
    overlay_txt.insert(tk.END, f"{'─' * 44}\n", "divider")
    overlay_txt.insert(tk.END, f"{text}\n", "body")
    overlay_txt.tag_config(tag, foreground=tag_color, font=("Consolas", 9, "bold"))
    overlay_txt.tag_config("divider", foreground="#333333", font=("Consolas", 8))
    overlay_txt.tag_config("body", foreground="#dddddd", font=("Arial", 10))
    overlay_txt.config(state=tk.DISABLED)
    overlay_txt.see(tk.END)

def _overlay_start_xenos_block():
    if overlay_txt is None:
        return
    overlay_txt.config(state=tk.NORMAL)
    overlay_txt.insert(tk.END, f"\n{'─' * 44}\n", "divider")
    overlay_txt.insert(tk.END, " ⬡ XENOS  P.H.I.A\n", "xenos_tag")
    overlay_txt.insert(tk.END, f"{'─' * 44}\n", "divider")
    overlay_txt.tag_config("xenos_tag", foreground="#00ff99", font=("Consolas", 9, "bold"))
    overlay_txt.tag_config("divider", foreground="#333333", font=("Consolas", 8))
    overlay_txt.config(state=tk.DISABLED)
    overlay_txt.see(tk.END)

def _stream_token_to_overlay(token):
    if overlay_txt is None:
        return
    try:
        overlay_txt.config(state=tk.NORMAL)
        overlay_txt.insert(tk.END, token, "body")
        overlay_txt.tag_config("body", foreground="#dddddd", font=("Arial", 10))
        overlay_txt.see(tk.END)
        overlay_txt.config(state=tk.DISABLED)
    except Exception:
        pass

def request_new_screenshot(label=None):
    global current_screenshot, last_frame_hash
    current_screenshot = None
    last_frame_hash = None
    if label:
        label.config(text="● NEW", fg="#ffaa00")
    set_status("Screenshot cleared — poller will refresh on next change.")

# ───────────────────────────────────────────────────────────
# App List
# ───────────────────────────────────────────────────────────
def get_running_apps():
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"]
            pid = proc.info["pid"]
            if not name:
                continue
            if is_system_process(name, pid):
                continue
            found.append({"pid": pid, "name": name})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    found.sort(key=lambda x: (x["name"].lower(), x["pid"]))
    return found

# ───────────────────────────────────────────────────────────
# AI
# ───────────────────────────────────────────────────────────
def ask_llama_stream(question, on_token, on_done, generation_id=None, abort_event=None, extra_images=None):
    global current_screenshot, conversation_history

    try:
        question = normalize_text(question)
        screenshot_bytes = current_screenshot

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(prune_history(conversation_history, MAX_HISTORY * 2))

        visual = is_visual_question(question)
        audio_context = format_audio_context_for_prompt()

        if audio_context:
            if visual:
                user_content = f"{audio_context}\n\nUse the screenshot if it helps answer accurately.\nQuestion: {question}"
            else:
                user_content = f"{audio_context}\n\nQuestion: {question}"
        else:
            if visual:
                user_content = f"Use the screenshot if it helps answer accurately.\nQuestion: {question}"
            else:
                user_content = f"Question: {question}"

        user_message = {"role": "user", "content": user_content}


        # Attach screenshot if visual and present
        if screenshot_bytes and visual:
            user_message["images"] = [screenshot_bytes]
        # Attach any extra images (e.g., file or audio context)
        if extra_images:
            if "images" in user_message:
                user_message["images"].extend(extra_images)
            else:
                user_message["images"] = list(extra_images)

        messages.append(user_message)

        full_reply = []

        stream = ollama.chat(
            model=VISION_MODEL,
            messages=messages,
            options={
                "num_predict": estimate_predict_tokens(question),
                "num_ctx": CTX_SIZE,
                "temperature": 0.25,
                "top_p": 0.9,
                "top_k": 40,
                "repeat_penalty": 1.25,
                "repeat_last_n": 128,
                "stop": [
                    "\nUser:", "\nYou:", "\nHuman:", "\nAssistant:",
                    "\nXenos:", "Let me know", "Anything else", "Hope that helps"
                ],
            },
            stream=True
        )

        for chunk in stream:
            if abort_event is not None and abort_event.is_set():
                break

            token = chunk["message"]["content"]
            if token:
                full_reply.append(token)
                on_token(token)

        if abort_event is not None and abort_event.is_set() and generation_id != current_generation_id:
            if hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass

            reply = "".join(full_reply)
            reply = reply.split("\nUser:")[0].split("\nYou:")[0].strip()
            reply = normalize_text(reply)

            if reply:
                conversation_history.append({"role": "user", "content": question})
                conversation_history.append({"role": "assistant", "content": reply})
                conversation_history[:] = conversation_history[-(MAX_HISTORY * 2):]
                save_conversation_cache()
            return

        reply = "".join(full_reply)
        reply = reply.split("\nUser:")[0].split("\nYou:")[0].strip()
        reply = normalize_text(reply)

        conversation_history.append({"role": "user", "content": question})
        conversation_history.append({"role": "assistant", "content": reply})
        conversation_history[:] = conversation_history[-(MAX_HISTORY * 2):]
        save_conversation_cache()

        on_done(reply)

    except Exception as e:
        error_msg = str(e)
        root.after(0, lambda: set_status(f"❌ Error: {error_msg}"))
        on_done(f"Something went wrong: {error_msg}")


def interrupt_generation():
    global generation_abort_event, current_generation_id
    if generation_abort_event is not None:
        generation_abort_event.set()
    current_generation_id += 1
    generation_abort_event = threading.Event()

# ───────────────────────────────────────────────────────────
# System Stats
# ───────────────────────────────────────────────────────────
def get_ram_usage():
    mem = psutil.virtual_memory()
    return f"RAM: {mem.used / (1024**3):.1f}GB / {mem.total / (1024**3):.1f}GB ({mem.percent}%)"

def get_cpu_usage():
    return f"CPU: {psutil.cpu_percent()}%"

def get_gpu_usage():
    if GPUtil is None:
        return "GPU: N/A"
    try:
        gpus = GPUtil.getGPUs()
        if gpus:
            avg = sum(g.load * 100 for g in gpus) / len(gpus)
            return f"GPU: {avg:.1f}%"
        return "GPU: N/A"
    except Exception:
        return "GPU: N/A"

def get_network_usage():
    global net_last, net_last_time
    net_now = psutil.net_io_counters()
    now = time.time()
    elapsed = max(now - net_last_time, 0.001)
    sent_kb = (net_now.bytes_sent - net_last.bytes_sent) / 1024 / elapsed
    recv_kb = (net_now.bytes_recv - net_last.bytes_recv) / 1024 / elapsed
    net_last = net_now
    net_last_time = now
    return f"NET: ↑{sent_kb:.1f}KB/s ↓{recv_kb:.1f}KB/s"

def update_stats():
    ram_var.set(get_ram_usage())
    cpu_var.set(get_cpu_usage())
    gpu_var.set(get_gpu_usage())
    net_var.set(get_network_usage())
    root.after(1000, update_stats)

# ───────────────────────────────────────────────────────────
# UI Helpers
# ───────────────────────────────────────────────────────────
def set_status(msg):
    status_var.set(f"Status: {msg}")

def append_chat(speaker, msg):
    chat_box.config(state=tk.NORMAL)

    if speaker == "You":
        header_color = "#00aaff"
        prefix = "▸ YOU"
    elif speaker == "Xenos":
        header_color = "#0066ff"
        prefix = "⬡ XENOS  P.H.I.A"
    else:
        header_color = "#aaaaaa"
        prefix = f"● {speaker.upper()}"

    chat_box.insert(tk.END, f"{'─' * 48}\n", "div")
    chat_box.insert(tk.END, f" {prefix}\n", speaker)
    chat_box.insert(tk.END, f"{'─' * 48}\n", "div")
    chat_box.insert(tk.END, f"{msg}\n\n", "body")
    chat_box.tag_config(speaker, foreground=header_color, font=("Consolas", 9, "bold"))
    chat_box.tag_config("div", foreground="#333333", font=("Consolas", 8))
    chat_box.tag_config("body", foreground="#dddddd", font=("Arial", 10))
    chat_box.see(tk.END)
    chat_box.config(state=tk.DISABLED)

def toggle_audio_capture():
    global audio_enabled, latest_audio_struct, audio_history, audio_notice_var
    audio_enabled = bool(audio_toggle_var.get())

    if audio_enabled:
        latest_audio_struct = None
        audio_history = []
        audio_status_var.set("AUDIO: ARMED")
        audio_notice_var.set("Audio capture enabled. Output audio capture is active.")
        set_status("Output audio capture enabled.")
    else:
        audio_status_var.set("AUDIO: OFF")
        audio_notice_var.set("Audio capture disabled. Enable Output Audio Capture to use capture.")
        set_status("Output audio capture disabled.")

# ───────────────────────────────────────────────────────────
# Button Actions
# ───────────────────────────────────────────────────────────
def hook_into_app():
    global monitored_app, conversation_history, current_screenshot, last_frame_hash
    global latest_audio_struct, audio_history

    selected = app_listbox.curselection()
    if not selected:
        set_status("No app selected.")
        return

    monitored_app = apps[selected[0]]
    conversation_history = []
    save_conversation_cache()
    current_screenshot = None
    last_frame_hash = None
    latest_audio_struct = None
    audio_history = []

    set_status(f"Monitoring: {monitored_app['name']} (PID: {monitored_app['pid']}) — poller active 🔄")
    append_chat("System", (
        f"Hooked into {monitored_app['name']} (PID: {monitored_app['pid']})\n"
        f"Screen poller active at {POLL_INTERVAL}s interval.\n"
        f"Audio mode: OUTPUT AUDIO CAPTURE (default loopback)\n"
        f"Capture: PrintWindow > Region Crop > Full Screen\n"
        f"Quality: {SCREENSHOT_MODE.upper()} {SCREENSHOT_SIZES[SCREENSHOT_MODE]}"
    ))

def refresh_apps():
    global apps
    apps = get_running_apps()
    app_listbox.delete(0, tk.END)
    for app in apps:
        app_listbox.insert(tk.END, f"{app['name']}  (PID: {app['pid']})")
    set_status("App list refreshed.")

def on_send():
    question = ask_entry.get().strip()
    if not question:
        return

    interrupt_generation()
    ask_entry.delete(0, tk.END)
    ensure_overlay()


    # Attach file if selected
    global selected_file_path, selected_file_preview
    file_image_bytes = None
    file_text = None
    if selected_file_path:
        ext = selected_file_path.lower().split('.')[-1]
        if ext in ("png", "jpg", "jpeg", "bmp", "gif"):
            try:
                img = PilImage.open(selected_file_path)
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                file_image_bytes = buf.getvalue()
                buf.close()
            except Exception as e:
                set_status(f"Error loading image: {e}")
        elif ext == "txt":
            try:
                with open(selected_file_path, "r", encoding="utf-8") as f:
                    file_text = f.read()
            except Exception as e:
                set_status(f"Error loading text: {e}")

    # Render audio transcript/activity as image for vision model
    audio_img_bytes = None
    try:
        from PIL import Image as PilImage2, ImageDraw, ImageFont
        transcript = (latest_audio_struct.get('transcript') or '').strip() if latest_audio_struct else ''
        output_active = latest_audio_struct.get('output_active', False) if latest_audio_struct else False
        speech_detected = latest_audio_struct.get('speech_detected', False) if latest_audio_struct else False
        note = latest_audio_struct.get('note', '') if latest_audio_struct else ''
        if transcript or output_active or speech_detected:
            text = transcript if transcript else ("AUDIO ACTIVE" if output_active or speech_detected else "")
            if not text:
                text = note
            # Create image
            img = PilImage2.new('RGB', (400, 48), color=(24,24,24))
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arial.ttf", 18)
            except Exception:
                font = ImageFont.load_default()
            draw.text((10, 10), text[:100], font=font, fill=(0,255,128))
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            audio_img_bytes = buf.getvalue()
            buf.close()
    except Exception as e:
        pass

    def run():
        pid = monitored_app["pid"] if monitored_app else None
        visual = is_visual_question(question)
        needs_shot = should_take_new_screenshot(question) if visual else False

        # If .txt, append to question
        q = question
        if file_text:
            q = f"{question}\n\n[File content follows:]\n{file_text}"

        if needs_shot:
            root.after(0, lambda: set_status("Taking fresh screenshot..."))
            if overlay_shot_label:
                root.after(0, lambda: overlay_shot_label.config(text="● CAPTURING", fg="#ffaa00"))
            focus_and_screenshot(pid)
            if overlay_shot_label:
                root.after(0, lambda: overlay_shot_label.config(text="● LIVE", fg="#00ff99"))
        else:
            if overlay_shot_label:
                root.after(0, lambda: overlay_shot_label.config(
                    text="● POLLED" if visual else "● TEXT", fg="#aaaaaa"
                ))

        root.after(0, lambda: set_status("Analyzing..."))
        root.after(0, lambda: _overlay_append_block("you_tag", "▸ YOU", q, "#00aaff"))
        root.after(0, _overlay_start_xenos_block)

        def on_token(token):
            root.after(0, lambda t=token: _stream_token_to_overlay(t))

        def on_done(full_reply):
            root.after(0, lambda: append_chat("You", q))
            root.after(0, lambda: append_chat("Xenos", full_reply))
            root.after(0, lambda: set_status("Ready."))

        # Pass images to ask_llama_stream if present
        extra_images = []
        if file_image_bytes:
            extra_images.append(file_image_bytes)
        if audio_img_bytes:
            extra_images.append(audio_img_bytes)
        if not extra_images:
            extra_images = None
        ask_llama_stream(
            q,
            on_token,
            on_done,
            generation_id=current_generation_id,
            abort_event=generation_abort_event,
            extra_images=extra_images
        )

    threading.Thread(target=run, daemon=True).start()
    # Reset file after send
    selected_file_path = None
    selected_file_preview = None

def prewarm_model():
    try:
        root.after(0, lambda: set_status("Pre-warming model..."))
        ollama.chat(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": "Respond with OK."}],
            options={"num_predict": 4, "temperature": 0.0}
        )
        root.after(0, lambda: set_status("Model ready ✅"))
    except Exception as e:
        root.after(0, lambda: set_status(f"Pre-warm failed: {str(e)}"))

# ───────────────────────────────────────────────────────────
# Build UI
# ───────────────────────────────────────────────────────────
root = tk.Tk()
root.title("Xenos P.H.I.A")
root.geometry("620x800")

BG = "#1e1e1e"
FG = "#ffffff"
ACCENT = "#0066ff"
BTN_BG = "#2d2d2d"
DIM = "#aaaaaa"

root.configure(bg=BG)

title_frame = tk.Frame(root, bg=BG)
title_frame.pack(fill=tk.X, padx=10, pady=(10, 0))

tk.Label(
    title_frame, text="XENOS  P.H.I.A",
    bg=BG, fg=ACCENT,
    font=("Consolas", 13, "bold")
).pack(side=tk.LEFT)

tk.Label(
    title_frame, text="Program Hooking Intelligent Assistant - Output Audio Capture",
    bg=BG, fg=DIM,
    font=("Consolas", 7)
).pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0))

poller_frame = tk.Frame(root, bg=BG)
poller_frame.pack(fill=tk.X, padx=10, pady=(2, 0))

tk.Label(
    poller_frame, text="⬡ Screen Poller:",
    bg=BG, fg=DIM,
    font=("Consolas", 8)
).pack(side=tk.LEFT)

poller_var = tk.StringVar(value="● Waiting for hook...")
tk.Label(
    poller_frame, textvariable=poller_var,
    bg=BG, fg="#aaaaaa",
    font=("Consolas", 8)
).pack(side=tk.LEFT, padx=(4, 0))

audio_status_var = tk.StringVar(value="AUDIO: OFF")
tk.Label(
    poller_frame, textvariable=audio_status_var,
    bg=BG, fg="#ffaa00",
    font=("Consolas", 8, "bold")
).pack(side=tk.RIGHT, padx=(0, 4))

audio_notice_var = tk.StringVar(
    value="Audio capture disabled. Enable Output Audio Capture to use capture."
)
tk.Label(
    root, textvariable=audio_notice_var,
    bg=BG, fg="#ff5555",
    font=("Consolas", 8)
).pack(fill=tk.X, padx=10, pady=(2, 6))

def update_poller_label():
    if monitored_app and current_screenshot:
        age = int(time.time() - screenshot_age)
        poller_var.set(f"● Active — last capture {age}s ago [{SCREENSHOT_MODE.upper()}]")
    elif monitored_app:
        poller_var.set("● Active — awaiting first capture...")
    else:
        poller_var.set("● Ready (full screen mode)")
    root.after(1000, update_poller_label)

tk.Label(
    root, text="Running Applications",
    bg=BG, fg=FG,
    font=("Arial", 10, "bold")
).pack(pady=(8, 2))

app_listbox = tk.Listbox(
    root, height=7, bg=BTN_BG, fg=FG,
    selectbackground=ACCENT, selectforeground="#111111",
    font=("Arial", 10), bd=0
)
app_listbox.pack(fill=tk.X, padx=10)

app_btn_frame = tk.Frame(root, bg=BG)
app_btn_frame.pack(fill=tk.X, padx=10, pady=5)

tk.Button(
    app_btn_frame, text="Refresh",
    bg=BTN_BG, fg=FG, bd=0, padx=8, pady=4,
    command=refresh_apps
).pack(side=tk.LEFT, padx=2)

tk.Button(
    app_btn_frame, text="Hook Into App",
    bg=ACCENT, fg="#111111", bd=0, padx=8, pady=4,
    command=hook_into_app
).pack(side=tk.LEFT, padx=2)

tk.Button(
    app_btn_frame, text="⟳ New Shot",
    bg=BTN_BG, fg=ACCENT, bd=0, padx=8, pady=4,
    command=request_new_screenshot
).pack(side=tk.LEFT, padx=2)

tk.Button(
    app_btn_frame, text="Overlay",
    bg=BTN_BG, fg=ACCENT, bd=0, padx=8, pady=4,
    command=ensure_overlay
).pack(side=tk.LEFT, padx=2)

audio_toggle_var = tk.BooleanVar(value=False)
tk.Checkbutton(
    app_btn_frame,
    text="Output Audio Capture",
    variable=audio_toggle_var,
    command=toggle_audio_capture,
    bg=BG,
    fg=ACCENT,
    selectcolor=BTN_BG,
    activebackground=BG,
    activeforeground=ACCENT,
    bd=0,
    highlightthickness=0
).pack(side=tk.LEFT, padx=6)

status_var = tk.StringVar(value="Status: Ready.")
tk.Label(
    root, textvariable=status_var,
    bg=BG, fg=DIM, font=("Consolas", 8)
).pack(pady=(2, 0))

tk.Label(
    root, text="Chat",
    bg=BG, fg=FG,
    font=("Arial", 10, "bold")
).pack(pady=(6, 2))

chat_box = tk.Text(
    root, height=14, bg=BTN_BG, fg=FG,
    font=("Arial", 10), state=tk.DISABLED,
    wrap=tk.WORD, padx=8, pady=8, bd=0
)
chat_box.pack(fill=tk.BOTH, expand=True, padx=10)

ask_frame = tk.Frame(root, bg="#1a1a1a", pady=8)
ask_frame.pack(fill=tk.X, padx=10, pady=(6, 4))

tk.Label(
    ask_frame, text="Ask:",
    bg="#1a1a1a", fg=ACCENT,
    font=("Consolas", 9, "bold")
).pack(side=tk.LEFT, padx=(8, 4))

ask_entry = tk.Entry(
    ask_frame, bg="#2d2d2d", fg=FG,
    font=("Arial", 11),
    insertbackground=ACCENT, bd=0,
    relief=tk.FLAT
)
ask_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7, padx=(0, 6))
ask_entry.bind("<Return>", lambda e: on_send())

tk.Button(
    ask_frame, text="Send",
    bg=ACCENT, fg="#111111", bd=0,
    font=("Consolas", 9, "bold"),
    padx=14, pady=6,
    activebackground="#0066ff", activeforeground="#111111",
    command=on_send
).pack(side=tk.RIGHT, padx=(0, 8))

stats_frame = tk.Frame(root, bg="#111111")
stats_frame.pack(fill=tk.X, side=tk.BOTTOM)

ram_var = tk.StringVar(value="RAM: ...")
cpu_var = tk.StringVar(value="CPU: ...")
gpu_var = tk.StringVar(value="GPU: ...")
net_var = tk.StringVar(value="NET: ...")

tk.Label(
    stats_frame, textvariable=ram_var,
    bg="#111111", fg="#0066ff",
    font=("Consolas", 8)
).pack(side=tk.LEFT, padx=8, pady=4)

tk.Label(
    stats_frame, textvariable=cpu_var,
    bg="#111111", fg="#ffaa00",
    font=("Consolas", 8)
).pack(side=tk.LEFT, padx=8)

tk.Label(
    stats_frame, textvariable=gpu_var,
    bg="#111111", fg="#ff5555",
    font=("Consolas", 8)
).pack(side=tk.LEFT, padx=8)

tk.Label(
    stats_frame, textvariable=net_var,
    bg="#111111", fg="#3a86ff",
    font=("Consolas", 8)
).pack(side=tk.LEFT, padx=8)

# ───────────────────────────────────────────────────────────
# File selection state
# ───────────────────────────────────────────────────────────
selected_file_path = None
selected_file_preview = None

def on_file_select():
    global selected_file_path, selected_file_preview
    filetypes = [
        ("Image files", "*.png;*.jpg;*.jpeg;*.bmp;*.gif"),
        ("Text files", "*.txt"),
        ("All files", "*.*")
    ]
    path = filedialog.askopenfilename(title="Select image or text file", filetypes=filetypes)
    if not path:
        return
    selected_file_path = path
    ext = path.lower().split('.')[-1]
    if ext in ("png", "jpg", "jpeg", "bmp", "gif"):
        try:
            img = PilImage.open(path)
            img.thumbnail((128, 128))
            selected_file_preview = ImageTk.PhotoImage(img)
            messagebox.showinfo("File Selected", f"Image loaded: {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not load image: {e}")
            selected_file_path = None
            selected_file_preview = None
    elif ext == "txt":
        try:
            with open(path, "r", encoding="utf-8") as f:
                preview = f.read(256)
            messagebox.showinfo("File Selected", f"Text file loaded: {path}\nPreview:\n{preview}")
            selected_file_preview = None
        except Exception as e:
            messagebox.showerror("Error", f"Could not load text: {e}")
            selected_file_path = None
            selected_file_preview = None
    else:
        messagebox.showinfo("File Selected", f"File loaded: {path}")
        selected_file_preview = None

# Add File button to ask_frame
file_btn = tk.Button(
    ask_frame, text="File",
    bg=BTN_BG, fg=ACCENT, bd=0,
    font=("Consolas", 9, "bold"),
    padx=10, pady=6,
    activebackground="#222222", activeforeground=ACCENT,
    command=on_file_select
)
file_btn.pack(side=tk.RIGHT, padx=(0, 6))

# ───────────────────────────────────────────────────────────
# Start
# ───────────────────────────────────────────────────────────
if load_conversation_cache():
    restore_cached_chat()
    set_status("Conversation restored from cache.")

refresh_apps()
update_stats()
update_poller_label()
threading.Thread(target=prewarm_model, daemon=True).start()
threading.Thread(target=screen_poller, daemon=True).start()
threading.Thread(target=audio_worker, daemon=True).start()
root.mainloop()
