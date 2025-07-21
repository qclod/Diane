import os, sys, json, re, tempfile, threading, time, wave, html
from html.parser import HTMLParser
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

app_state = "idle"
state_lock = Lock()
staged_model_key = ""
staged_input = ""
cancellation_event = threading.Event()
stop_listening_event = threading.Event()
ACTIVE_VOICE_THREAD = None

master_history = []
history_lock = Lock()
model_caches = {}
cache_source_lens = {}
MINIMUM_CACHE_TOKENS = 2048
CACHE_COMPACTION_THRESHOLD = 30
DIFF_TOKEN_REBUILD_THRESHOLD = 4096

class SSMLFixer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.is_in_prosody = False

    def handle_starttag(self, tag, attrs):
        attr_str = ''.join([f' {k}="{v}"' for k, v in attrs])
        if tag == 'prosody':
            if self.is_in_prosody:
                self.result.append('</prosody>')
            self.result.append(f'<prosody{attr_str}>')
            self.is_in_prosody = True
        elif tag == 'break':
            self.result.append(f'<break{attr_str}/>')
        else:
            self.result.append(f'<{tag}{attr_str}>')

    def handle_endtag(self, tag):
        if tag == 'prosody':
            if self.is_in_prosody:
                self.result.append('</prosody>')
                self.is_in_prosody = False
        elif tag == 'break':
            pass
        else:
            self.result.append(f'</{tag}>')

    def handle_data(self, data):
        self.result.append(data)

    def get_fixed_ssml(self):
        if self.is_in_prosody:
            self.result.append('</prosody>')
        return "".join(self.result)

def sanitize_ssml(raw_text):
    cleaned_text = re.sub(r'```xml\s*|```', '', raw_text).strip()
    cleaned_text = re.sub(r'(")([a-zA-Z]+=)', r'\1 \2', cleaned_text)
    cleaned_text = cleaned_text.replace('</mods>', '</prosody>')
    cleaned_text = cleaned_text.replace('</songs>', '</prosody>')
    cleaned_text = re.sub(r'</?emphasis.*?>', '', cleaned_text)
    cleaned_text = re.sub(r'&(?![a-zA-Z#0-9]+;)', '&', cleaned_text)
    content = re.sub(r'</?speak>', '', cleaned_text).strip()
    fixer = SSMLFixer()
    fixer.feed(content)
    fixed_content = fixer.get_fixed_ssml()
    return f"<speak>{fixed_content}</speak>"

def strip_ssml_tags(sanitized_ssml):
    text_without_tags = re.sub(r'<[^>]+>', '', sanitized_ssml)
    return html.unescape(text_without_tags).strip()

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
    except Exception as e:
        print(f"⚠️  Could not set high process priority: {e}")

def estimate_token_count(history):
    count = 0
    for turn in history:
        for part in turn.get('parts', []):
            count += len(str(part.get('text', '')))
    return count / 4

class AudioPlayer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="AudioPlayer")
        self.audio_queue = Queue()
        self.pause_event = threading.Event()
        self.pause_event.set()
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
                        self.pause_event.wait()
                        stream.write(data)
                        data = wf.readframes(1024)
                    stream.close()
                    p.terminate()
                if os.path.exists(self.current_file):
                    os.remove(self.current_file)
                self.current_file = None
            except Exception as e:
                print(f"❌ Audio player error: {e}")
            if self.audio_queue.empty() and app_state in ["speaking", "paused"]:
                set_application_state("idle")
    def play_files(self, file_list):
        for f in file_list:
            self.audio_queue.put(f)
    def toggle_pause(self):
        if app_state not in ["speaking", "paused"]:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            set_application_state("paused")
        else:
            self.pause_event.set()
            set_application_state("speaking")
    def stop_and_clear(self):
        self.stop_playback_event.set()
        self.pause_event.set()
        while not self.audio_queue.empty():
            try:
                filepath = self.audio_queue.get_nowait()
                if filepath and os.path.exists(filepath):
                    os.remove(filepath)
            except (Empty, OSError):
                pass

def load_configuration():
    print("--- Loading Configuration ---")
    try:
        with open("config.json", 'r', encoding='utf-8') as f:
            config = json.load(f)
        with open(config['system_instruction_file'], 'r', encoding='utf-8') as f:
            config['system_instruction'] = f.read().strip()
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

def set_application_state(new_state, status_message=None):
    global app_state
    with state_lock:
        if app_state == new_state:
            return
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
        if status_message:
            ui_queue.put(("status", status_message))

def handle_start_voice(model_key):
    if app_state != "idle":
        return
    global staged_model_key, ACTIVE_VOICE_THREAD
    staged_model_key = model_key
    cancellation_event.clear()
    stop_listening_event.clear()
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
            try:
                audio_queue.put(stream.read(chunk, exception_on_overflow=False))
            except (IOError, OSError):
                break
        audio_queue.put(None)
        stream.stop_stream()
        stream.close()
        p.terminate()

    def _stream_transcriber():
        def _audio_generator():
            while True:
                chunk_data = audio_queue.get()
                if chunk_data is None:
                    return
                yield speech.StreamingRecognizeRequest(audio_content=chunk_data)

        config_rec = speech.RecognitionConfig(encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16, sample_rate_hertz=rate, language_code="en-US", enable_automatic_punctuation=True)
        streaming_config = speech.StreamingRecognitionConfig(config=config_rec, interim_results=True)
        try:
            responses = speech_client.streaming_recognize(config=streaming_config, requests=_audio_generator())
            finalized_parts = []
            for r in responses:
                if cancellation_event.is_set() or stop_listening_event.is_set():
                    break
                if r.results and r.results[0].alternatives:
                    phrase = r.results[0].alternatives[0].transcript
                    if r.results[0].is_final:
                        finalized_parts.append(phrase.strip())
                    combined_text = ' '.join(finalized_parts + ([phrase] if not r.results[0].is_final else []))
                    gui_queue.put(("update_entry", combined_text))
            final_transcript_container[0] = ' '.join(finalized_parts)
        except Exception as e:
            if not cancellation_event.is_set():
                print(f"⚠️  Speech recognition stream ended: {e}")

    recorder = threading.Thread(target=_audio_recorder, daemon=True)
    transcriber = threading.Thread(target=_stream_transcriber, daemon=True)
    recorder.start()
    transcriber.start()
    recorder.join()
    transcriber.join()

    return final_transcript_container[0].strip()

def handle_start_text(model_key):
    if app_state != "idle":
        return
    global staged_model_key
    staged_model_key = model_key
    cancellation_event.clear()
    model_name = config['models'].get(model_key, 'Unknown Model')
    set_application_state("awaiting_text", f"⌨️ Awaiting text for {model_name}...")

def handle_send_request(user_input, from_state):
    global staged_input
    if app_state != from_state:
        return

    if not user_input.strip():
        handle_cancel_action()
        return

    staged_input = user_input
    model_name = config['models'].get(staged_model_key, 'Unknown Model')
    set_application_state("processing", f"🧠 Processing with {model_name}...")
    threading.Thread(target=_request_and_speak_thread, daemon=True).start()

def _request_and_speak_thread():
    local_model_key = staged_model_key
    local_input = staged_input

    global master_history, model_caches, cache_source_lens

    ui_queue.put(("history", f"You: {local_input}"))
    print(f"\n[You]: {local_input}")

    raw_ai_response = ""
    is_request_successful = False

    with history_lock:
        master_history.append({'role': 'user', 'parts': [{'text': local_input}]})

    while not is_request_successful:
        if cancellation_event.is_set():
            return

        try:
            model_name = config['models'][local_model_key]
            
            with history_lock:
                total_tokens = estimate_token_count(master_history)

                if total_tokens < MINIMUM_CACHE_TOKENS:
                    print(f"--- BOOTSTRAP MODE (History < {MINIMUM_CACHE_TOKENS} tokens). Sending full history... ---")
                    model = genai.GenerativeModel(model_name=model_name, system_instruction=config['system_instruction'])
                    final_content = master_history
                    print(f"    -> [{local_model_key.upper()}] Sending {len(final_content)} turns as context.")
                    response = model.generate_content(final_content)
                    raw_ai_response = response.text
                else:
                    current_cache = model_caches.get(local_model_key)
                    last_known_len = cache_source_lens.get(local_model_key, 0)
                    history_diff = master_history[last_known_len:]
                    
                    rebuild_reason = None
                    if current_cache is None:
                        rebuild_reason = f"NEW CACHE for '{local_model_key.upper()}'"
                    elif len(history_diff) >= CACHE_COMPACTION_THRESHOLD:
                        rebuild_reason = f"CACHE COMPACTION: Diff of {len(history_diff)} turns exceeds threshold ({CACHE_COMPACTION_THRESHOLD})"
                    else:
                        diff_tokens = estimate_token_count(history_diff)
                        if diff_tokens >= DIFF_TOKEN_REBUILD_THRESHOLD:
                            rebuild_reason = f"CACHE COMPACTION: Diff tokens ({int(diff_tokens)}) exceed threshold ({DIFF_TOKEN_REBUILD_THRESHOLD})"
                    
                    if rebuild_reason:
                        print(f"--- BOOTSTRAP/REBUILD MODE: {rebuild_reason}. Rebuilding... ---")
                        if current_cache:
                            try:
                                current_cache.delete()
                            except Exception as e:
                                print(f"⚠️  Could not delete old cache (it may have already expired): {e}")
                        
                        history_for_caching = master_history[:-1]
                        current_cache = genai.caching.CachedContent.create(
                            model=f"models/{model_name}",
                            system_instruction=config['system_instruction'],
                            contents=history_for_caching
                        )
                        model_caches[local_model_key] = current_cache
                        cache_source_lens[local_model_key] = len(history_for_caching)
                    else:
                        print(f"--- CATCH-UP MODE: Using existing cache and sending {len(history_diff)} diff turns. ---")
                    
                    model = genai.GenerativeModel.from_cached_content(cached_content=current_cache)
                    final_content = master_history[cache_source_lens.get(local_model_key, 0):]
                    print(f"    -> [{local_model_key.upper()}] Sending {len(final_content)} turns as context.")
                    response = model.generate_content(final_content)
                    raw_ai_response = response.text
                
                is_request_successful = True

        except Exception as e:
            if "CachedContent not found" in str(e):
                print(f"⚠️  Cache for '{local_model_key}' has expired. Deleting local reference and retrying silently.")
                if local_model_key in model_caches:
                    del model_caches[local_model_key]
                if local_model_key in cache_source_lens:
                    del cache_source_lens[local_model_key]
                continue
            else:
                print(f"❌ Gemini Error: {e}")
                raw_ai_response = "<speak>I seem to be having trouble connecting to my brain.</speak>"
                is_request_successful = True

    with history_lock:
        if raw_ai_response:
            master_history.append({'role': 'model', 'parts': [{'text': raw_ai_response}]})
        else:
            if master_history and master_history[-1]['role'] == 'user':
                master_history.pop()
            return

    if cancellation_event.is_set():
        return

    sanitized_ssml = sanitize_ssml(raw_ai_response)
    print(f"[Diane]: {sanitized_ssml}")
    log_conversation_turn(config['log_filename'], local_model_key, local_input, sanitized_ssml)
    ui_queue.put(("history", f"Diane: {strip_ssml_tags(sanitized_ssml)}"))

    audio_files = create_audio_chunks(sanitized_ssml, clients[0], config)

    if cancellation_event.is_set():
        for f in audio_files:
            if os.path.exists(f):
                os.remove(f)
        return

    if audio_files:
        set_application_state("speaking")
        audio_player.play_files(audio_files)
    else:
        set_application_state("idle")

def handle_cancel_action():
    print("--- CANCEL ACTION TRIGGERED ---")
    cancellation_event.set()
    stop_listening_event.set()

    global ACTIVE_VOICE_THREAD
    if ACTIVE_VOICE_THREAD and ACTIVE_VOICE_THREAD.is_alive():
        ACTIVE_VOICE_THREAD.join()

    audio_player.stop_and_clear()

    global staged_input, staged_model_key
    staged_input = ""
    staged_model_key = ""

    set_application_state("idle", "❌ Action cancelled.")

def create_audio_chunks(sanitized_ssml, tts_client, config):
    byte_limit = config['tts_limits']['byte_limit_for_long_audio']
    ssml_chunks = _split_ssml_into_chunks(sanitized_ssml, byte_limit)
    print(f"--- Split into {len(ssml_chunks)} audio chunks. ---")
    
    audio_files = []
    for i, ssml_chunk in enumerate(ssml_chunks):
        if cancellation_event.is_set() or not strip_ssml_tags(ssml_chunk).strip():
            continue
        print(f"    -> Synthesizing chunk {i+1}/{len(ssml_chunks)} ({len(ssml_chunk.encode('utf-8'))} bytes)...")
        filepath = _synthesize_single_chunk(ssml_chunk, tts_client, config['audio_settings'])
        if filepath:
            audio_files.append(filepath)
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

def _split_ssml_into_chunks(ssml_text, byte_limit):
    try:
        source_root = ET.fromstring(ssml_text)
        if len(ssml_text.encode('utf-8')) > byte_limit:
            print(f"--- SSML is valid and exceeds {byte_limit} bytes. Splitting by structure... ---")
            return _split_by_structure(source_root, byte_limit)
        else:
            print(f"--- SSML is valid and within byte limit. No splitting needed. ---")
            return [ssml_text]
    except ET.ParseError as e:
        print(f"❌ SSML Parse Error: {e}. Salvaging text and splitting by sentence. ---")
        plain_text = strip_ssml_tags(ssml_text)
        return _split_by_sentence(plain_text, byte_limit)

def _split_by_structure(source_root, byte_limit):
    final_chunks = []
    current_chunk_root = ET.Element(source_root.tag, source_root.attrib)

    for elem in list(source_root):
        elem_str = ET.tostring(elem, encoding='unicode')
        current_chunk_str = ET.tostring(current_chunk_root, encoding='unicode')
        
        if len(current_chunk_str.encode('utf-8')) + len(elem_str.encode('utf-8')) > byte_limit and len(list(current_chunk_root)) > 0:
            final_chunks.append(ET.tostring(current_chunk_root, encoding='unicode'))
            current_chunk_root = ET.Element(source_root.tag, source_root.attrib)
        
        current_chunk_root.append(elem)

    if len(list(current_chunk_root)) > 0:
        final_chunks.append(ET.tostring(current_chunk_root, encoding='unicode'))

    return final_chunks

def _split_by_sentence(plain_text, byte_limit):
    chunks = []
    current_chunk_text = ""
    sentences = re.split(r'(?<=[.!?])\s+', plain_text)
    
    for sentence in sentences:
        if not sentence.strip():
            continue
        
        if len(current_chunk_text.encode('utf-8')) + len(sentence.encode('utf-8')) > byte_limit and current_chunk_text:
            chunks.append(f"<speak>{current_chunk_text.strip()}</speak>")
            current_chunk_text = ""
            
        current_chunk_text += sentence + " "
        
    if current_chunk_text.strip():
        chunks.append(f"<speak>{current_chunk_text.strip()}</speak>")
        
    return chunks

def log_conversation_turn(filename, model_key, user_input, raw_ai_response):
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        clean_ai_response = strip_ssml_tags(raw_ai_response)
        log_entry = (
            f"--- [ {timestamp} | Model: {model_key.upper()} ] ---\n"
            f"User: {user_input}\n"
            f"Diane (Clean): {clean_ai_response}\n"
            f"Diane (SSML): {raw_ai_response}\n\n"
        )
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        print(f"📝 Logged to '{filename}'")
    except Exception as e:
        print(f"⚠️ Log Error: {e}")

def main_logic(backend_queue, ui_queue_ref):
    global config, clients, audio_player, ui_queue
    ui_queue = ui_queue_ref
    load_dotenv()
    set_high_priority()
    config = load_configuration()
    if not config:
        ui_queue.put(("status", "FATAL: Config error. Check console."))
        return
    clients = setup_clients()
    if not all(clients):
        ui_queue.put(("status", "FATAL: Client setup error. Check console."))
        return

    os.makedirs("logs", exist_ok=True)
    config['log_filename'] = os.path.join("logs", f"conversation_log_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt")
    audio_player = AudioPlayer()
    audio_player.start()

    def on_send_hotkey():
        if app_state == 'listening':
            backend_queue.put(('stop_listening', None))
        elif app_state == 'awaiting_text':
            ui_queue.put(('request_gui_input', None))

    keyboard.add_hotkey('ctrl+shift+m', on_send_hotkey)
    keyboard.add_hotkey('ctrl+alt+m', on_send_hotkey)
    keyboard.add_hotkey('ctrl+shift+k', lambda: backend_queue.put(('cancel_action', None)))
    keyboard.add_hotkey('ctrl+alt+k', lambda: backend_queue.put(('cancel_action', None)))
    keyboard.add_hotkey('ctrl+shift+i', lambda: backend_queue.put(('toggle_pause_audio', None)))
    keyboard.add_hotkey('ctrl+alt+i', lambda: backend_queue.put(('toggle_pause_audio', None)))
    for model, key in [('l', 'lite'), ('o', 'flash'), ('p', 'pro')]:
        keyboard.add_hotkey(f'ctrl+alt+{model}', lambda k=key: backend_queue.put(('start_voice', k)))
        keyboard.add_hotkey(f'ctrl+shift+{model}', lambda k=key: backend_queue.put(('start_text', k)))
    print("--- Hotkey listener is active ---")

    greeting_ssml = """<speak>Hello world. <prosody rate="fast">Diane here!</prosody><break time="400ms"/> <prosody pitch="+5st">Oh, hey... I'm awake.</prosody><break time="400ms"/> <prosody rate="x-slow" pitch="-4st">How...</prosody><break time="200ms"/> <prosody rate="slow" pitch="-9st">wonderful.</prosody></speak>"""
    sanitized_greeting = sanitize_ssml(greeting_ssml)
    clean_greeting_text = strip_ssml_tags(sanitized_greeting)

    with history_lock:
        master_history.append({'role': 'model', 'parts': [{'text': sanitized_greeting}]})

    print(f"\n[Diane]: {sanitized_greeting}")
    log_conversation_turn(config['log_filename'], "SYSTEM", "[STARTUP]", sanitized_greeting)
    ui_queue.put(("history", f"Diane: {clean_greeting_text}"))
    audio_files = create_audio_chunks(sanitized_greeting, clients[0], config)
    if audio_files:
        set_application_state("speaking")
        audio_player.play_files(audio_files)
    else:
        set_application_state("idle")

    while True:
        try:
            command, data = backend_queue.get()
            if command == 'start_voice':
                handle_start_voice(data)
            elif command == 'start_text':
                handle_start_text(data)
            elif command == 'stop_listening':
                handle_stop_listening()
            elif command == 'send_request':
                handle_send_request(data, "awaiting_text")
            elif command == 'cancel_action':
                handle_cancel_action()
            elif command == 'toggle_pause_audio':
                audio_player.toggle_pause()
        except KeyboardInterrupt:
            print("--- Exiting due to Ctrl+C ---")
            break

if __name__ == '__main__':
    backend_queue, ui_queue = Queue(), Queue()
    root = tk.Tk()
    gui = DianeGUI(root, backend_queue, ui_queue)
    logic_thread = threading.Thread(target=main_logic, args=(backend_queue, ui_queue), daemon=True)
    logic_thread.start()

    try:
        root.mainloop()
    finally:
        print("--- Main window closed. Application shutting down. ---")
