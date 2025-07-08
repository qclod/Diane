# diane_script.py

import os, sys, json, re, tempfile, uuid, threading, time, wave
import google.generativeai as genai
from google.cloud import texttospeech, speech
import pyaudio
import keyboard
from dotenv import load_dotenv
from queue import Queue, Empty
from threading import Lock
from copy import deepcopy
import tkinter as tk
from diane_gui import DianeGUI
import xml.etree.ElementTree as ET

if sys.platform == "win32":
    import win32api, win32process, win32con


def sanitize_ssml(raw_text):

    cleaned_text = re.sub(r'```xml\s*|```', '', raw_text).strip()


    match = re.search(r'<speak>(.*)</speak>', cleaned_text, re.DOTALL)
    if match:
        # Get the inner content
        inner_content = match.group(1)

        inner_content = re.sub(r'</?speak>', '', inner_content)

        return f"<speak>{inner_content.strip()}</speak>"
    else:

        return f"<speak>{cleaned_text}</speak>"


def strip_ssml_tags(sanitized_ssml):
    return re.sub(r'<[^>]+>', '', sanitized_ssml).strip()

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

def play_wav_file(filename):
    try:
        with wave.open(filename, 'rb') as wf:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=p.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True
            )
            data = wf.readframes(1024)
            while data:
                stream.write(data)
                data = wf.readframes(1024)
            stream.stop_stream()
            stream.close()
            p.terminate()
    except Exception as e:
        print(f"❌ Audio playback error: {e}")

def load_configuration():
    print("--- Loading Configuration ---")
    config_path = "config.json"
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
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
    except FileNotFoundError as e:
        print(f"❌ FATAL CONFIGURATION ERROR: File not found - {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ FATAL CONFIGURATION ERROR: Invalid JSON in config.json - {e}")
        return None
    except Exception as e:
        print(f"❌ FATAL CONFIGURATION ERROR: {e}")
        return None

def setup_clients():
    print("--- Initializing API Clients ---")
    try:
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        service_account_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not all([gemini_api_key, service_account_file]):
            raise ValueError("Missing environment variables GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS in .env file.")
        if not os.path.exists(service_account_file):
            raise FileNotFoundError(f"Service Account JSON file not found at '{service_account_file}'")
        
        genai.configure(api_key=gemini_api_key)
        tts_client = texttospeech.TextToSpeechClient()
        speech_client = speech.SpeechClient()
        print("✅ All clients initialized successfully.")
        return tts_client, speech_client
    except Exception as e:
        print(f"❌ FATAL CLIENT SETUP ERROR: {e}")
        return None, None

def listen_and_transcribe_simultaneously(speech_client, audio_settings, gui):
    gui.queue_clear_entry()
    audio_queue, stop_event, final_transcript_container = Queue(), threading.Event(), [""]
    pyaudio_format, channels, rate, chunk = audio_settings['audio_format_pyaudio'], audio_settings['channels'], audio_settings['rate'], audio_settings['chunk_size']
    
    def _audio_recorder():
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio_format, channels=channels, rate=rate, input=True, frames_per_buffer=chunk)
        gui.queue_status_update("🎙️ Listening... (Press Esc to stop recording)")
        while not stop_event.is_set():
            audio_queue.put(stream.read(chunk, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        p.terminate()
    
    def _stream_transcriber():
        def _audio_generator():
            while not stop_event.is_set():
                try:
                    chunk = audio_queue.get(timeout=0.1)
                    if chunk is None: return
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)
                except Empty:
                    continue
            return
            
        config = speech.RecognitionConfig(encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16, sample_rate_hertz=rate, language_code="en-US", enable_automatic_punctuation=True)
        streaming_config = speech.StreamingRecognitionConfig(config=config, interim_results=True)
        responses = speech_client.streaming_recognize(config=streaming_config, requests=_audio_generator())
        
        finalized_parts = []
        try:
            for r in responses:
                if r.results and r.results[0].alternatives:
                    phrase = r.results[0].alternatives[0].transcript
                    if r.results[0].is_final:
                        finalized_parts.append(phrase.strip())
                        gui.queue_live_transcript_update(' '.join(finalized_parts))
                    else:
                        gui.queue_live_transcript_update(' '.join(finalized_parts + [phrase]))
            final_transcript_container[0] = ' '.join(finalized_parts)
        except Exception as e:
            print(f"⚠️  Speech recognition stream ended: {e}")

    recorder = threading.Thread(target=_audio_recorder, daemon=True)
    transcriber = threading.Thread(target=_stream_transcriber, daemon=True)
    recorder.start()
    transcriber.start()
    
    keyboard.wait('esc')
    stop_event.set()
    audio_queue.put(None)
    recorder.join()
    transcriber.join()
    
    final_transcript = final_transcript_container[0].strip()
    if not final_transcript:
        gui.queue_status_update("...Couldn't understand the audio.")
    return final_transcript

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

def speak_text(sanitized_ssml, tts_client, config, gui):
    gui.queue_status_update("🗣️ Synthesizing speech...")
    print(f"...Checking message length ({len(sanitized_ssml.encode('utf-8'))} bytes)")
    
    byte_limit = config['tts_limits']['byte_limit_for_long_audio']
    if len(sanitized_ssml.encode('utf-8')) > byte_limit:
        print("...Message is long. Using audio chunking.")
        speak_in_chunks(sanitized_ssml, tts_client, config['audio_settings'], gui)
    else:
        print("...Message is standard length. Using standard synthesis.")
        synthesize_short_audio(sanitized_ssml, tts_client, config['audio_settings'], gui)

def synthesize_short_audio(ssml_text, client, audio_settings, gui):
    s_input = texttospeech.SynthesisInput(ssml=ssml_text)
    voice = texttospeech.VoiceSelectionParams(language_code='-'.join(audio_settings['voice_name'].split('-')[:2]), name=audio_settings['voice_name'])
    a_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        pitch=audio_settings['pitch_modifier']
    )
    try:
        response = client.synthesize_speech(input=s_input, voice=voice, audio_config=a_config)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_f:
            temp_f.write(response.audio_content)
            temp_name = temp_f.name
        gui.queue_status_update("...Speaking...")
        play_wav_file(temp_name)
        os.remove(temp_name)
    except Exception as e:
        print(f"❌ TTS Error (short): {e}")
        gui.queue_status_update(f"Error: TTS failed.")

def speak_in_chunks(ssml_text, client, audio_settings, gui):
    BYTE_LIMIT = 4800

    try:
        source_root = ET.fromstring(ssml_text)
        
        chunks = []
        parent_stack = [ET.Element(source_root.tag, source_root.attrib)]
        chunks.append(parent_stack[0])

        def check_and_split():
            """Checks size and creates a new chunk if the current one is too large."""
            nonlocal parent_stack
            current_chunk = chunks[-1]
            if len(ET.tostring(current_chunk, encoding='utf-8')) > BYTE_LIMIT:

                last_added_node = parent_stack[-1]
                parent_of_last_added = parent_stack[-2]
                
                parent_of_last_added.remove(last_added_node)

                new_chunk_root = ET.Element(source_root.tag, source_root.attrib)
                chunks.append(new_chunk_root)
                new_parent_stack = [new_chunk_root]
                
                for old_parent in parent_stack[1:-1]:
                    new_parent = ET.Element(old_parent.tag, old_parent.attrib)
                    new_parent_stack[-1].append(new_parent)
                    new_parent_stack.append(new_parent)

                new_parent_stack[-1].append(last_added_node)
                
                parent_stack = new_parent_stack
                parent_stack.append(last_added_node)


        def traverse_and_build(source_node):
            """Recursively traverses the source tree and builds the chunked trees."""
            nonlocal parent_stack
            
            if source_node.text and source_node.text.strip():
                for word in source_node.text.split():
                    current_parent = parent_stack[-1]
                    if current_parent.text is None:
                        current_parent.text = ""
                    
                    original_text = current_parent.text
                    current_parent.text += word + " "
                    check_and_split()
                    current_parent = parent_stack[-1]
                    if current_parent.text != original_text + word + " ":
                         pass
            
            for child_node in source_node:
                new_elem = ET.Element(child_node.tag, child_node.attrib)
                parent_stack[-1].append(new_elem)
                parent_stack.append(new_elem)
                
                check_and_split()
                
                traverse_and_build(child_node)
                
                parent_stack.pop()

            if source_node.tail and source_node.tail.strip():
                parent_of_current = parent_stack[-2]
                for word in source_node.tail.split():
                    if parent_of_current.text is None:
                        parent_of_current.text = ""
                    
                    original_text = parent_of_current.text
                    parent_of_current.text += word + " "
                    check_and_split()
                    parent_of_current = parent_stack[-2]
                    if parent_of_current.text != original_text + word + " ":
                        pass

        traverse_and_build(source_root)
        
        chunk_ssml_list = [ET.tostring(chunk, encoding='unicode') for chunk in chunks]

        print(f"...Splitting into {len(chunk_ssml_list)} audio chunks.")
        for i, chunk_ssml in enumerate(chunk_ssml_list):
            if not strip_ssml_tags(chunk_ssml).strip():
                continue
            gui.queue_status_update(f"...Speaking chunk {i+1}/{len(chunk_ssml_list)}...")
            synthesize_short_audio(chunk_ssml, client, audio_settings, gui)

    except ET.ParseError as e:
        print(f"❌ SSML Parse Error: {e}. The AI generated invalid SSML.")
        print("...Attempting to speak the content by stripping all SSML tags as a last resort.")
        
        plain_text = strip_ssml_tags(ssml_text)
        CHAR_LIMIT = 4500 # Safe character limit
        text_chunks = [plain_text[i:i+CHAR_LIMIT] for i in range(0, len(plain_text), CHAR_LIMIT)]
        
        if not text_chunks or not any(c.strip() for c in text_chunks):
            gui.queue_status_update("Warning: AI response had errors and no text.")
            return

        gui.queue_status_update(f"Warning: AI response had errors. Speaking plain text in {len(text_chunks)} chunks...")
        
        for i, text_chunk in enumerate(text_chunks):
            if not text_chunk.strip():
                continue
            chunk_for_speech = f"<speak>{text_chunk}</speak>"
            print(f"--- Speaking plain text chunk {i+1}/{len(text_chunks)} ({len(chunk_for_speech.encode('utf-8'))} bytes) ---")
            synthesize_short_audio(chunk_for_speech, client, audio_settings, gui)


def log_conversation_turn(filename, model_key, user_input, sanitized_ssml):
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        clean_ai_response = strip_ssml_tags(sanitized_ssml)
        log_entry = (f"--- [ {timestamp} | Model: {model_key.upper()} ] ---\n"
                       f"User: {user_input}\n"
                       f"Diane: {clean_ai_response}\n"
                       f"Diane (Raw SSML): {sanitized_ssml}\n"
                       f"--------------------------------------------------\n\n")
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        print(f"📝 Logged to '{filename}'")
    except Exception as e:
        print(f"⚠️ Log Error: {e}")

def handle_request(model_key, input_mode, clients, config, history_data, gui):
    gui.queue_status_update(f"--- {input_mode.capitalize()} Hotkey '{model_key.upper()}' triggered ---")
    time.sleep(0.4)
    tts_client, speech_client = clients
    conversation_history, history_lock, text_input_queue = history_data
    user_input = ""

    if input_mode == 'voice':
        user_input = listen_and_transcribe_simultaneously(speech_client, config['audio_settings'], gui)
    elif input_mode == 'text':
        gui.queue_activate_text_input()
        user_input = text_input_queue.get()
    
    if user_input == "_CANCEL_":
        gui.queue_status_update("✅ Ready. Text input cancelled.")
        return

    if user_input:
        gui.queue_live_transcript_update("")
        model_name = config['models'][model_key]
        gui.queue_status_update(f"🧠 Processing with {model_name}...")
        gui.queue_history_update(f"You: {user_input}")
        print(f"\n[You]: {user_input}")
        
        with history_lock:
            conversation_history.append({'role': 'user', 'parts': [{'text': user_input}]})
            raw_ai_response, conversation_history = get_ai_response(model_name, conversation_history, config['system_instruction'])
        
        sanitized_ssml = sanitize_ssml(raw_ai_response)
        clean_display_text = strip_ssml_tags(sanitized_ssml)
        
        print(f"[Diane]: {sanitized_ssml}")
        log_conversation_turn(config['log_filename'], model_key, user_input, sanitized_ssml)
        gui.queue_history_update(f"Diane: {clean_display_text}")
        speak_text(sanitized_ssml, tts_client, config, gui)
    
    gui.queue_status_update("✅ Ready. Press a hotkey to start...")

def main_logic(gui, text_input_queue):
    load_dotenv()
    print("--- Starting Diane Backend ---")
    
    set_high_priority()
    
    config = load_configuration()
    if not config:
        gui.queue_status_update("FATAL: Config error. Check console.")
        return
        
    clients = setup_clients()
    if not all(clients):
        gui.queue_status_update("FATAL: Client setup error. Check console.")
        return

    log_folder = "logs"
    os.makedirs(log_folder, exist_ok=True)
    config['log_filename'] = os.path.join(log_folder, f"conversation_log_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt")
    print(f"📝 Logging to: {config['log_filename']}")
    
    conversation_history, history_lock = [], Lock()
    history_data = (conversation_history, history_lock, text_input_queue)
    
    greeting_ssml = """<speak><prosody rate="medium">Hello world!</prosody> <prosody rate="fast">Diane here... reporting for duty!</prosody><break time="400ms"/> <prosody rate="medium" pitch="+5st">Oh, hey... I'm awake.</prosody><break time="400ms"/> <prosody rate="x-slow" pitch="-4st">How...</prosody><break time="200ms"/> <prosody rate="slow" pitch="-8st">wonderful.</prosody></speak>"""
    sanitized_greeting = sanitize_ssml(greeting_ssml)
    
    gui.queue_history_update(f"Diane: {strip_ssml_tags(sanitized_greeting)}")
    print(f"[Diane]: {sanitized_greeting}")
    speak_text(sanitized_greeting, clients[0], config, gui)
    gui.queue_status_update("✅ Ready. Press a hotkey to start...")

    def start_request_thread(model_key, input_mode):
        if any(t.name == 'diane-request' and t.is_alive() for t in threading.enumerate()):
            print("⚠️ Request already in progress.")
            return
        threading.Thread(
            target=handle_request, 
            name='diane-request', 
            args=(model_key, input_mode, clients, config, history_data, gui), 
            daemon=True
        ).start()

    keyboard.add_hotkey('ctrl+alt+l', lambda: start_request_thread('lite', 'voice'))
    keyboard.add_hotkey('ctrl+alt+o', lambda: start_request_thread('flash', 'voice'))
    keyboard.add_hotkey('ctrl+alt+p', lambda: start_request_thread('pro', 'voice'))
    
    keyboard.add_hotkey('ctrl+shift+l', lambda: start_request_thread('lite', 'text'))
    keyboard.add_hotkey('ctrl+shift+o', lambda: start_request_thread('flash', 'text'))
    keyboard.add_hotkey('ctrl+shift+p', lambda: start_request_thread('pro', 'text'))
    
    print("--- Hotkey listener is active ---")
    threading.Event().wait()

if __name__ == '__main__':
    text_input_queue = Queue()
    root = tk.Tk()
    gui = DianeGUI(root, text_input_queue)
    
    logic_thread = threading.Thread(target=main_logic, args=(gui, text_input_queue), daemon=True)
    logic_thread.start()
    
    root.mainloop()
