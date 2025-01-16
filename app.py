import speech_recognition as sr
from elevenlabs import generate
import vlc
import time
import os
import tempfile
import queue
import threading
from openai import OpenAI
from pynput import keyboard
import re
import json
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from config import ELEVEN_LABS_API_KEY, OPENAI_API_KEY
from knowledge_base import PDFProcessor 
import requests

from threading import Lock

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AI_SalesAgent:
    def __init__(self, system_prompt=None):
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.elevenlabs_api_key = ELEVEN_LABS_API_KEY
        self.instance = vlc.Instance()
        self.lock = Lock()
        self.player = self.instance.media_player_new()
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.client_entities = {"name": None, "email": None, "company_name": None, "requirements": [], "meeting_date": None, "meeting_time": None, "industry": None}
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=2)
        self.is_listening = False
        self.is_speaking = False
        self.audio_queue = queue.Queue()
        self.system_prompt = system_prompt
        self.full_transcript = [{"role": "system", "content": self.system_prompt}]

    def generate_ai_response(self, text):
        self.full_transcript.append({"role": "user", "content": text})
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4",
                messages=self.full_transcript,
                temperature=0.7,
                max_tokens=150
            )
            response_text = response.choices[0].message.content
            spoken_response, entities = self.extract_response_and_entities(response_text)
            if entities:
                self.update_entities(entities)
            self.full_transcript.append({"role": "assistant", "content": spoken_response})
            self.generate_audio(spoken_response)
        except Exception as e:
            error_response = "I apologize, but I'm having trouble processing that. Could you please repeat?"
            self.generate_audio(error_response)

    def generate_audio(self, text):
        self.is_speaking = True
        try:
            # Generate audio using ElevenLabs API
            audio_stream = generate(api_key=self.elevenlabs_api_key, text=text, voice="Aria", stream=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as temp_file:
                for chunk in audio_stream:
                    temp_file.write(chunk)
            temp_file_path = temp_file.name

            # Play the generated audio
            media = self.instance.media_new(temp_file_path)
            self.player.set_media(media)
            self.player.play()

            # Wait until audio playback finishes
            time.sleep(0.5)
            while self.player.is_playing():
                time.sleep(0.1)

        except Exception as e:
            print(f"Audio generation error: {str(e)}")
        finally:
            self.is_speaking = False
            # Clean up temporary audio file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def start_listening(self):
        self.is_listening = True
        threading.Thread(target=self._listen_continuous).start()

    def _process_audio(self):
        try:
            if not self.audio_queue.empty():
                # Get the next audio from the queue
                audio = self.audio_queue.get()
                # Recognize speech using speech recognition
                recognized_text = self.recognizer.recognize_google(audio)

                # Process the recognized text with AI response
                print(f"Recognized text: {recognized_text}")
                self.generate_ai_response(recognized_text)
        except sr.UnknownValueError:
            print("Speech recognition could not understand audio.")
        except sr.RequestError as e:
            print(f"Speech recognition error: {str(e)}")

    def _listen_continuous(self):
        while self.is_listening:
            if not self.is_speaking:
                print("Listening for input...")
                try:
                    with self.microphone as source:
                        audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
                    self.audio_queue.put(audio)
                    print("Audio captured and queued.")
                    self._process_audio()
                except sr.WaitTimeoutError:
                    print("Listening timeout, no input detected.")
                    continue
                except Exception as e:
                    print(f"Error during listening: {e}")
            else:
                print("Waiting for speaking to finish...")
                time.sleep(0.1)
                
    def set_is_speaking(self, value):
        with self.lock:
            self.is_speaking = value
    
    def get_is_speaking(self):
        with self.lock:
            return self.is_speaking            
    
    def stop_listening(self):
        self.is_listening = False

    def save_entities_to_file(self):
        """
        Save the extracted client entities to a file.
        """
        directory = './client_conversation/'
        if not os.path.exists(directory):
            os.makedirs(directory)
        
        filename = os.path.join(directory, f'client_data_{int(time.time())}.json')
    
        try:
            with open(filename, 'w') as f:
                json.dump(self.client_entities, f, indent=4)
            print(f"Client entities saved to {filename}")
        except Exception as e:
            print(f"Error saving client entities: {str(e)}")

    def extract_response_and_entities(self, response_text):
        parts = response_text.split("[[ENTITIES]]")
        spoken_response = parts[0].strip()
        entities = None
        if len(parts) > 1:
            try:
                json_match = re.search(r'\{.*\}', parts[1], re.DOTALL)
                if json_match:
                    entities = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return spoken_response, entities

ai_sales_agent = AI_SalesAgent()

@app.post("/knowledge_docs")
async def upload_knowledge_docs(file: UploadFile = File(...)):
    os.makedirs("knowledge_base", exist_ok=True)
    
    temp_pdf_path = f"knowledge_base/{file.filename}"
    try:
        with open(temp_pdf_path, "wb") as buffer:
            buffer.write(file.file.read())
    except Exception as e:
        return JSONResponse({"error": f"Failed to save file: {str(e)}"}, status_code=500)

    if not os.path.exists(temp_pdf_path):
        return JSONResponse({"error": "File not found after saving"}, status_code=500)

    pdf_processor = PDFProcessor(api_key=OPENAI_API_KEY)
    pdf_text = pdf_processor.extract_text_from_pdf(temp_pdf_path)
    if not pdf_text:
        return JSONResponse({"error": "Failed to extract text from PDF"}, status_code=400)

    print("Extracted PDF Text:", pdf_text)

    structured_info = pdf_processor.structure_company_info(pdf_text)
    if not structured_info:
        return JSONResponse({"error": "Failed to structure company information"}, status_code=400)

    print("Structured Info:", structured_info)

    sales_prompt = pdf_processor.create_sales_prompt(structured_info)
    if not sales_prompt:
        return JSONResponse({"error": "Failed to generate sales prompt"}, status_code=400)

    print("Generated Sales Prompt:", sales_prompt)

    global ai_sales_agent
    ai_sales_agent = AI_SalesAgent(system_prompt=sales_prompt)

    return JSONResponse({"message": "Knowledge base updated successfully", "prompt": sales_prompt})

@app.get("/voice_options")
async def get_voice_options():
    url = "https://api.elevenlabs.io/v1/voices"
    headers = {
        "xi-api-key": ELEVEN_LABS_API_KEY,
        "Content-Type": "application/json"
    }

    response = requests.get(url, headers=headers)
    data = response.json()

    voices = []
    for voice in data['voices']:
        voices.append({
            'voice_id': voice['voice_id'],
            'name': voice['name']
        })

    return JSONResponse(voices)

@app.post("/start_conversation")
async def start_conversation():
    greeting = "Hello! This is Sarah calling from Toshal Infotech. Could you please tell me your name?"
    ai_sales_agent.generate_audio(greeting)
    ai_sales_agent.start_listening()
    return {"message": "Conversation started."}

@app.post("/stop_conversation")
async def stop_conversation():
    try:
        ai_sales_agent.stop_listening()
        ai_sales_agent.save_entities_to_file()
        return {"message": "Conversation stopped and client data saved."}
    except Exception as e:
        return JSONResponse({"error": f"Failed to stop conversation: {str(e)}"}, status_code=500)

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)