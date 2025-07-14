# diane_script.py

import os, sys, json, re, tempfile, threading, time, wave
import google.generativeai as genai
from google.cloud import texttospeech, speech
import pyaudio
import keyboard
from dotenv import load_dotenv
from queue import Queue, Empty
from threading import Lock
from diane_gui import DianeGUI
import xml.etree.ElementTree as ET
import tkinter as tk

if sys.platform == "win32":
    import win32api, win32process, win32con

# --- Global State & Threading Primitives ---
app_state = "idle"
state_lock = Lock()
staged_model_key = ""
cancellation_event = threading.Event()
stop_listening_event = threading.Event()
ACTIVE_VOICE_THREAD = None

# --- Helper Functions ---
def sanitize_ssml(raw_text):
    cleaned_text = re.sub(r'```xml\s*|```', '', raw_text).strip()
    is_clean = False
    while not is_clean:
        original_text = cleaned_text
        if cleaned_text.startswith('<speak>'): cleaned_text = cleaned_text[len('<speak>'):].lstrip()
        if cleaned_text.endswith('</speak>'): cleaned_text = cleaned_text[:-len('</speak>')].rstrip()
        if cleaned_text == original_text: is_clean = True
    return f"<speak>{cleaned_text}</speak>"

def strip_ssml_tags(sanitized_ssml): return re.sub(r'<[^>]+>', '', sanitized_ssml).strip()

def set_high_priority():
    try:
        if sys.platform == "win32":
            pid = win32api.GetCurrentProcessId()
            handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, True, pid)
            win32process.SetPriorityClass(handle, win32process.HIGH_PRIORITY_CLASS)
            print("✅ Process priority set to HIGH for Windows.")
        else:
            os.setpriority(os.PRIO_PROCESS, 0, -10)
            print("✅ Process priority set to HIGH for Unix-like OS.")
    except Exception as e: print(f"⚠️  Could not set high process priority: {e}")

# --- Dedicated Audio Player Thread ---
class AudioPlayer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="AudioPlayer")
        self.audio_queue = Queue()
        self.pause_event = threading.Event(); self.pause_event.set()
        self.stop_playback_event = threading.Event()
        self.current_file = None
    def run(self):
        while True:
            try:
                self.current_file = self.audio_queue.get()
                if self.current_file is None: continue
                self.stop_playback_event.clear()
                with wave.open(self.current_file, 'rb') as wf:
                    p = pyaudio.PyAudio()
                    stream = p.open(format=p.get_format_from_width(wf.getsampwidth()), channels=wf.getnchannels(), rate=wf.getframerate(), output=True)
                    data = wf.readframes(1024)
                    while data and not self.stop_playback_event.is_set():
                        self.pause_event.wait(); stream.write(data); data = wf.readframes(1024)
                    stream.close(); p.terminate()
                if os.path.exists(self.current_file): os.remove(self.current_file)
                self.current_file = None
            except Exception as e: print(f"❌ Audio player error: {e}")
            if self.audio_queue.empty() and app_state in ["speaking", "paused"]:
                set_application_state("idle")
    def play_files(self, file_list):
        for f in file_list: self.audio_queue.put(f)
    def toggle_pause(self):
        if app_state not in ["speaking", "paused"]: return
        if self.pause_event.is_set(): self.pause_event.clear(); set_application_state("paused")
        else: self.pause_event.set(); set_application_state("speaking")
    def stop_and_clear(self):
        self.stop_playback_event.set(); self.pause_event.set()
        while not self.audio_queue.empty():
            try:
                filepath = self.audio_queue.get_nowait()
                if filepath and os.path.exists(filepath): os.remove(filepath)
            except (Empty, OSError): pass

# --- Configuration and Client Setup ---
def load_configuration():
    print("--- Loading Configuration ---")
    try:
        with open("config.json", 'r', encoding='utf-8') as f: config = json.load(f)
        with open(config['system_instruction_file'], 'r', encoding='utf-8') as f: config['system_instruction'] = f.read().strip()
        audio_format_val = config['audio_settings']['pyaudio_format_constant']
        if isinstance(audio_format_val, str):
            config['audio_settings']['audio_format_pyaudio'] = getattr(pyaudio, audio_format_val, pyaudio.paInt16)
        else:
            config['audio_settings']['audio_format_pyaudio'] = audio_format_val
        print("✅ Configuration loaded successfully.")
        return config
    except Exception as e:
        print(f"❌ FATAL CONFIGURATION ERROR: {e}")
        return None

def setup_clients():
    print("--- Initializing API Clients ---")
    try:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        return texttospeech.TextToSpeechClient(), speech.SpeechClient()
    except Exception as e:
        print(f"❌ FATAL CLIENT SETUP ERROR: {e}")
        return None, None

# --- Core Logic Functions (State Machine) ---
def set_application_state(new_state, status_message=None):
    global app_state
    with state_lock:
        if app_state == new_state: return
        app_state = new_state
        print(f"--- State changed to: {app_state.upper()} ---")
        ui_config = {'activations': 'disabled', 'send': 'disabled', 'cancel': 'disabled', 'pause_resume': 'disabled', 'entry_box_enabled': False, 'pause_resume_text': 'Pause/Resume', 'send_text': 'Send', 'send_command': 'send'}
        ui_config['app_state'] = app_state
        if app_state == "idle":
            ui_config.update({'activations': 'normal', 'cancel': 'disabled'})
            status_message = status_message or "✅ Ready. Choose an input method."
        elif app_state == "listening":
            ui_config.update({'cancel': 'normal', 'send': 'normal', 'send_text': 'Send', 'send_command': 'stop'})
        elif app_state == "awaiting_text":
            ui_config.update({'cancel': 'normal', 'entry_box_enabled': True, 'send': 'normal'})
        elif app_state == "processing":
            ui_config.update({'cancel': 'normal'})
        elif app_state == "speaking":
            ui_config.update({'cancel': 'normal', 'pause_resume': 'normal', 'pause_resume_text': 'Pause'})
        elif app_state == "paused":
            ui_config.update({'cancel': 'normal', 'pause_resume': 'normal', 'pause_resume_text': 'Resume'})
        ui_queue.put(("ui_state", ui_config))
        if status_message: ui_queue.put(("status", status_message))

def handle_start_voice(model_key):
    if app_state != "idle": return
    global staged_model_key, ACTIVE_VOICE_THREAD
    staged_model_key = model_key
    cancellation_event.clear(); stop_listening_event.clear()
    model_name = config['models'].get(model_key, 'Unknown Model')
    set_application_state("listening", f"🎙️ Listening to {model_name}...")
    ACTIVE_VOICE_THREAD = threading.Thread(target=_voice_input_thread, daemon=True)
    ACTIVE_VOICE_THREAD.start()

def handle_stop_listening():
    if app_state == "listening":
        stop_listening_event.set()

def _voice_input_thread():
    transcript = listen_and_transcribe(clients[1], config['audio_settings'], ui_queue)
    if cancellation_event.is_set():
        return
    if transcript:
        handle_send_request(transcript, "listening")
    else:
        set_application_state("idle", "❌ No audio detected. Action cancelled.")

def listen_and_transcribe(speech_client, audio_settings, gui_queue):
    audio_queue = Queue()
    final_transcript_container = [""] 
    pyaudio_format, channels, rate, chunk = audio_settings['audio_format_pyaudio'], audio_settings['channels'], audio_settings['rate'], audio_settings['chunk_size']
    
    def _audio_recorder():
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio_format, channels=channels, rate=rate, input=True, frames_per_buffer=chunk)
        while not stop_listening_event.is_set() and not cancellation_event.is_set():
            try: audio_queue.put(stream.read(chunk, exception_on_overflow=False))
            except (IOError, OSError): break
        audio_queue.put(None)
        stream.stop_stream(); stream.close(); p.terminate()
    
    def _stream_transcriber():
        def _audio_generator():
            while True:
                chunk_data = audio_queue.get()
                if chunk_data is None: return
                yield speech.StreamingRecognizeRequest(audio_content=chunk_data)
        
        config_rec = speech.RecognitionConfig(encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16, sample_rate_hertz=rate, language_code="en-US", enable_automatic_punctuation=True)
        streaming_config = speech.StreamingRecognitionConfig(config=config_rec, interim_results=True)
        try:
            responses = speech_client.streaming_recognize(config=streaming_config, requests=_audio_generator())
            finalized_parts = []
            for r in responses:
                if cancellation_event.is_set() or stop_listening_event.is_set(): break
                if r.results and r.results[0].alternatives:
                    phrase = r.results[0].alternatives[0].transcript
                    if r.results[0].is_final:
                        finalized_parts.append(phrase.strip())
                    combined_text = ' '.join(finalized_parts + ([phrase] if not r.results[0].is_final else []))
                    gui_queue.put(("update_entry", combined_text))
            final_transcript_container[0] = ' '.join(finalized_parts)
        except Exception as e:
            if not cancellation_event.is_set(): print(f"⚠️  Speech recognition stream ended: {e}")

    recorder = threading.Thread(target=_audio_recorder, daemon=True)
    transcriber = threading.Thread(target=_stream_transcriber, daemon=True)
    recorder.start(); transcriber.start()
    recorder.join(); transcriber.join()
    
    return final_transcript_container[0].strip()

def handle_start_text(model_key):
    if app_state != "idle": return
    global staged_model_key
    staged_model_key = model_key
    cancellation_event.clear()
    model_name = config['models'].get(model_key, 'Unknown Model')
    set_application_state("awaiting_text", f"⌨️ Awaiting text for {model_name}...")

def handle_send_request(user_input, from_state):
    global staged_input
    if app_state != from_state: return
    
    if not user_input.strip():
        handle_cancel_action()
        return
    
    staged_input = user_input
    model_name = config['models'].get(staged_model_key, 'Unknown Model')
    set_application_state("processing", f"🧠 Processing with {model_name}...")
    threading.Thread(target=_request_and_speak_thread, daemon=True).start()

def _request_and_speak_thread():
    global staged_input, staged_model_key, conversation_history
    
    model_name = config['models'][staged_model_key]
    ui_queue.put(("history", f"You: {staged_input}"))
    print(f"\n[You]: {staged_input}")
    
    with history_lock:
        conversation_history.append({'role': 'user', 'parts': [{'text': staged_input}]})
        raw_ai_response, conversation_history = get_ai_response(model_name, conversation_history, config['system_instruction'])
    
    if cancellation_event.is_set(): return

    sanitized_ssml = sanitize_ssml(raw_ai_response)
    clean_display_text = strip_ssml_tags(sanitized_ssml)
    print(f"[Diane]: {sanitized_ssml}")
    log_conversation_turn(config['log_filename'], staged_model_key, staged_input, sanitized_ssml)
    ui_queue.put(("history", f"Diane: {clean_display_text}"))
    
    audio_files = create_audio_chunks(sanitized_ssml, clients[0], config)
    
    if cancellation_event.is_set():
        for f in audio_files:
            if os.path.exists(f): os.remove(f)
        return

    if audio_files:
        set_application_state("speaking")
        audio_player.play_files(audio_files)
    else: set_application_state("idle")

def handle_cancel_action():
    print("--- CANCEL ACTION TRIGGERED ---")
    cancellation_event.set()
    stop_listening_event.set()
    
    global ACTIVE_VOICE_THREAD
    if ACTIVE_VOICE_THREAD and ACTIVE_VOICE_THREAD.is_alive():
        print("--- Waiting for voice thread to terminate... ---")
        ACTIVE_VOICE_THREAD.join()
        print("--- Voice thread terminated. ---")
    
    audio_player.stop_and_clear()
    
    global staged_input, staged_model_key
    staged_input = ""
    staged_model_key = ""
    
    set_application_state("idle", "❌ Action cancelled.")

def get_ai_response(model_name, history, system_instruction):
    print(f"🧠 Sending text to {model_name}...")
    try:
        model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
        response = model.generate_content(history)
        history.append(response.candidates[0].content)
        return response.text, history
    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return "<speak>I seem to be having trouble connecting to my brain.</speak>", history

def create_audio_chunks(sanitized_ssml, tts_client, config):
    byte_limit = config['tts_limits']['byte_limit_for_long_audio']
    ssml_chunks = [sanitized_ssml]
    if len(sanitized_ssml.encode('utf-8')) > byte_limit:
        ssml_chunks = _split_ssml_into_chunks(sanitized_ssml)
    audio_files = []
    for ssml_chunk in ssml_chunks:
        if cancellation_event.is_set() or not strip_ssml_tags(ssml_chunk).strip(): continue
        filepath = _synthesize_single_chunk(ssml_chunk, tts_client, config['audio_settings'])
        if filepath: audio_files.append(filepath)
    return audio_files

def _synthesize_single_chunk(ssml_text, client, audio_settings):
    s_input = texttospeech.SynthesisInput(ssml=ssml_text)
    voice = texttospeech.VoiceSelectionParams(language_code='-'.join(audio_settings['voice_name'].split('-')[:2]), name=audio_settings['voice_name'])
    a_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16, pitch=audio_settings['pitch_modifier'])
    try:
        response = client.synthesize_speech(input=s_input, voice=voice, audio_config=a_config)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_f:
            temp_f.write(response.audio_content)
            return temp_f.name
    except Exception as e:
        print(f"❌ TTS Error (chunk): {e}")
        return None

def _split_ssml_into_chunks(ssml_text):
    BYTE_LIMIT = 4800
    try:
        source_root = ET.fromstring(ssml_text)
        chunks, parent_stack = [], [ET.Element(source_root.tag, source_root.attrib)]
        chunks.append(parent_stack[0])
        def check_and_split():
            nonlocal parent_stack
            if len(ET.tostring(chunks[-1], encoding='utf-8')) > BYTE_LIMIT:
                last_added, parent_of_last = parent_stack[-1], parent_stack[-2]
                parent_of_last.remove(last_added)
                new_chunk_root = ET.Element(source_root.tag, source_root.attrib)
                chunks.append(new_chunk_root)
                new_parent_stack = [new_chunk_root]
                for old_parent in parent_stack[1:-1]:
                    new_parent = ET.Element(old_parent.tag, old_parent.attrib)
                    new_parent_stack[-1].append(new_parent); new_parent_stack.append(new_parent)
                new_parent_stack[-1].append(last_added); parent_stack = new_parent_stack; parent_stack.append(last_added)
        def traverse_and_build(source_node):
            nonlocal parent_stack
            if source_node.text and source_node.text.strip():
                for word in source_node.text.split():
                    current_parent = parent_stack[-1]; original_text = current_parent.text or ""
                    current_parent.text = original_text + word + " "; check_and_split()
            for child_node in source_node:
                new_elem = ET.Element(child_node.tag, child_node.attrib); parent_stack[-1].append(new_elem)
                parent_stack.append(new_elem); check_and_split(); traverse_and_build(child_node); parent_stack.pop()
            if source_node.tail and source_node.tail.strip():
                parent_of_current = parent_stack[-2]
                for word in source_node.tail.split():
                    original_text = parent_of_current.text or ""
                    current_parent.text = original_text + word + " "; check_and_split()
        traverse_and_build(source_root)
        return [ET.tostring(chunk, encoding='unicode') for chunk in chunks]
    except ET.ParseError as e:
        print(f"❌ SSML Parse Error: {e}. Falling back to plain text splitting.")
        plain_text = strip_ssml_tags(ssml_text); CHAR_LIMIT = 4500
        text_chunks = [plain_text[i:i+CHAR_LIMIT] for i in range(0, len(plain_text), CHAR_LIMIT)]
        return [f"<speak>{chunk}</speak>" for chunk in text_chunks if chunk.strip()]

def log_conversation_turn(filename, model_key, user_input, sanitized_ssml):
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        clean_ai_response = strip_ssml_tags(sanitized_ssml)
        log_entry = (f"--- [ {timestamp} | Model: {model_key.upper()} ] ---\n"
                       f"User: {user_input}\n"
                       f"Diane: {clean_ai_response}\n\n")
        with open(filename, 'a', encoding='utf-8') as f: f.write(log_entry)
        print(f"📝 Logged to '{filename}'")
    except Exception as e: print(f"⚠️ Log Error: {e}")

# --- Main Application Logic ---
def main_logic(backend_queue, ui_queue_ref):
    global config, clients, conversation_history, history_lock, audio_player, ui_queue
    ui_queue = ui_queue_ref; load_dotenv(); set_high_priority()
    config = load_configuration()
    if not config: ui_queue.put(("status", "FATAL: Config error. Check console.")); return
    clients = setup_clients()
    if not all(clients): ui_queue.put(("status", "FATAL: Client setup error. Check console.")); return
    os.makedirs("logs", exist_ok=True)
    config['log_filename'] = os.path.join("logs", f"conversation_log_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt")
    conversation_history, history_lock = [], Lock()
    audio_player = AudioPlayer(); audio_player.start()
    
    def on_send_hotkey():
        if app_state == 'listening': handle_stop_listening()
        elif app_state == 'awaiting_text': ui_queue.put(('request_gui_input', None))

    keyboard.add_hotkey('ctrl+shift+m', on_send_hotkey); keyboard.add_hotkey('ctrl+alt+m', on_send_hotkey)
    keyboard.add_hotkey('ctrl+shift+k', handle_cancel_action); keyboard.add_hotkey('ctrl+alt+k', handle_cancel_action)
    keyboard.add_hotkey('ctrl+shift+i', audio_player.toggle_pause); keyboard.add_hotkey('ctrl+alt+i', audio_player.toggle_pause)
    for model, key in [('l','lite'), ('o','flash'), ('p','pro')]:
        keyboard.add_hotkey(f'ctrl+alt+{model}', lambda k=key: backend_queue.put(('start_voice', k)))
        keyboard.add_hotkey(f'ctrl+shift+{model}', lambda k=key: backend_queue.put(('start_text', k)))
    print("--- Hotkey listener is active ---")
    
    greeting_ssml = """<speak><prosody rate="medium">Hello world!</prosody> <prosody rate="fast">Diane here... reporting for duty!</prosody><break time="400ms"/> <prosody rate="medium" pitch="+5st">Oh, hey... I'm awake.</prosody><break time="400ms"/> <prosody rate="x-slow" pitch="-4st">How...</prosody><break time="200ms"/> <prosody rate="slow" pitch="-8st">wonderful.</prosody></speak>"""
    sanitized_greeting = sanitize_ssml(greeting_ssml)
    ui_queue.put(("history", f"Diane: {strip_ssml_tags(sanitized_greeting)}"))
    audio_files = create_audio_chunks(sanitized_greeting, clients[0], config)
    if audio_files: set_application_state("speaking"); audio_player.play_files(audio_files)
    else: set_application_state("idle")

    while True:
        command, data = backend_queue.get()
        if command == 'start_voice': handle_start_voice(data)
        elif command == 'start_text': handle_start_text(data)
        elif command == 'stop_listening': handle_stop_listening()
        elif command == 'send_request': handle_send_request(data, "awaiting_text")
        elif command == 'cancel_action': handle_cancel_action()
        elif command == 'toggle_pause_audio': audio_player.toggle_pause()

if __name__ == '__main__':
    backend_queue, ui_queue = Queue(), Queue()
    root = tk.Tk(); gui = DianeGUI(root, backend_queue, ui_queue)
    logic_thread = threading.Thread(target=main_logic, args=(backend_queue, ui_queue), daemon=True)
    logic_thread.start()
    root.mainloop()
