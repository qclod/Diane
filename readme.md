# Diane AI Assistant

A witty, multi-modal AI companion with a custom personality, powered by Gemini 2.5.
Can function as an everyday assistant, or a stream co-host or something.
Yes, this is my first project. Let's hear it for vibes!

---

## 🚀 Introduction

Diane is a conversational AI assistant that uses Google's powerful Generative AI, Speech-to-Text, and Text-to-Speech services. Diane's personality is fully customizable through a detailed system instruction file, allowing for a unique, character-driven interaction.

This project evolved from a simple console script into a multi-threaded, high-performance GUI application designed for real-time, low-latency conversation.

### ✅ Features
- **Graphical User Interface (GUI):** A clean, modern interface built with `tkinter`.
- **Multi-Modal Input:** Interact with Diane using your voice or keyboard.
- **Selectable Models:** Instantly call `gemini-2.5-flash-lite`, `gemini-2.5-flash`, or `gemini-2.5-pro` at will, with dedicated hotkeys for each.
- **Real-Time Voice Transcription:** See your spoken words appear live in the GUI as you speak.
- **Expressive, Custom Voice:** Diane's responses are spoken aloud using SSML (Speech Synthesis Markup Language) for a more natural and performative delivery.
- **Deeply Customizable Personality:** The `diane_system_instruction.md` file acts as Diane's "soul," allowing you to define her character, worldview, and even her sense of humor, if you really want to. I suggest giving default Diane a try!
- **High-Performance Audio:** The application elevates its process priority to ensure the most accurate, low-latency voice capture possible, even while running other demanding programs.
- **Robust and Secure:** Uses a standard `.env` file for secure credential management, and provides detailed logging for each session.

---

## 🔧 Setup and Installation Guide

This guide covers the one-time setup required to get Diane running.

### Step 1: Google Cloud Project Setup

Diane requires several Google Cloud services.

1.  **Create a New Google Cloud Project:**
    - Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project.

2.  **Enable the Necessary APIs:**
    - In your new project, search for and enable the following four APIs:
      - `Generative Language API`
      - `Cloud Text-to-Speech API`
      - `Cloud Speech-to-Text API`
      - `Cloud Storage` (maybe not needed anymore; do it just in case)

3.  **Create a Service Account:**
    - In "IAM & Admin" -> "Service Accounts," create a new service account (e.g., `diane-ai-service-account`).
    - Grant it the following four roles:
      - **Vertex AI User**
      - **Cloud Speech-to-Text User**
      - **Cloud Text-to-Speech User**
      - **Storage Object Admin** (unsure if this is still needed, do it just in case)

4.  **Download the Service Account Key:**
    - In the "Keys" tab for your new service account, create a new **JSON** key. A file will be downloaded.
    - **Move this JSON file into your Diane project folder** and rename it to exactly `diane-ai-service-account.json`.

5.  **Create a Gemini API Key:**
    - Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and create a new API key within the same Google Cloud project.
    - Copy the long API key string.

### Step 2: Local File Configuration

1.  **Create Your Personal `.env` File:**
    - In the project folder, you will find a template file named `.env.example`.
    - **Make a copy of this file and rename the copy to `.env`**.
    - Open your new `.env` file and replace the placeholder text with your actual keys and names from Step 1.

    ```ini
    # .env - Your private credentials file
    
    GEMINI_API_KEY="paste-your-long-gemini-api-key-here"
    GOOGLE_APPLICATION_CREDENTIALS="diane-ai-service-account.json"
    ```
    ⚠️ **IMPORTANT:** The `.env` file contains your secret keys and is specifically ignored by Git (see the `.gitignore` file). This is to prevent you from accidentally sharing your secrets.

### Step 3: Installation & Launch

1.  **Run the Installer:**
    - Double-click the `diane_installers.bat` file.
    - It may request Administrator privileges. This is required to set process priority for high-performance audio and to install Python libraries correctly.
    - The script will create a Python virtual environment (`diane_env`), install all required libraries from `requirements.txt`, and then launch Diane automatically.

2.  **For Daily Use:**
    - After the initial setup, simply run `Launch_Diane.bat` to start the application.

---

## 💡 How to Use

Diane is controlled entirely by global hotkeys, allowing you to handily speak to Diane even when the window is not in focus. The commands are always visible at the bottom of the GUI window.

- **Voice Input:** `Ctrl + Alt + [L/O/P]` -> Speak your message -> Press `Esc` to send.
- **Text Input:** `Ctrl + Shift + [L/O/P]` -> Type in the now-active text box -> Press `Enter` to send.
  - To cancel text input, press `Esc` while the text box is active.

The `L`, `O`, and `P` keys correspond to the model you wish to use:
- **L:** `gemini-2.5-flash-lite` (Fastest, near-instant responses. Best for conversational flow.)
- **O:** `gemini-2.5-flash` (Slower, with moderate thinking ability. Cheaper to use than Pro, at least.)
- **P:** `gemini-2.5-pro` (Slowest, but might be the most capable depending on your use case.)

All three models share context, so you can easily switch between them in the same conversation.

Edit the config.json file to tweak some parameters of Diane, if needed. You can set any other WaveNet or Neural2 voice.

---

## 📂 Project Components

-   `diane_script.py`: The main application backend. Handles hotkeys, API calls, and all core logic.
-   `diane_gui.py`: Defines the layout and behavior of the graphical user interface.
-   `diane_system_instruction.md`: The "soul" of Diane. Edit this file to completely change her personality, knowledge, and behavior.
-   `config.json`: Contains general settings like voice name, pitch, and model names.
-   `requirements.txt`: Lists all the Python libraries the project depends on.
-   `.gitignore`: A configuration file that tells Git which files (like `.env`) to ignore.
-   `.env.example`: A public template showing the structure of the `.env` file.
-   `diane_installers.bat`: A one-time setup script that prepares the environment and installs all dependencies.
-   `Launch_Diane.bat`: The simple script you run every day to launch the app.

---

## ❤️ Acknowledgments

-   **qclod:** Design, QA
-   **Gemini 2.5 Pro:** Code generation, debugging, documentation
-	**Xeno:** Special thanks <3