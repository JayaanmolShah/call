from fastapi import FastAPI, WebSocket, File, UploadFile, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import json
import base64
from elevenlabs import generate
import os
from openai import OpenAI
from typing import Dict, Optional
import PyPDF2
import io
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from dataclasses import dataclass 
from typing import List
from datetime import datetime

app = FastAPI()
ELEVEN_LABS_API_KEY = os.environ.get("ELEVEN_LABS_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
END_CALL_PHRASES = ["end call", "end the call", "goodbye", "good day", "bye", "quit", "stop", "hang up", 
    "end conversation", "that's all", "thank you bye", "thanks bye", "stop the call", "leave me alone", "thank you"]

@dataclass
class RetrievalResult:
    chunks: List[str]
    similarities: List[float]
    sources: List[str]
    page_numbers: List[int]

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse('index.html')

class PDFProcessor:
    def __init__(self, api_key):
        self.client = OpenAI(api_key=api_key)

    def extract_text_from_pdf(self, file_content: bytes) -> Optional[str]:
        """Extract text directly from PDF file content"""
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
            text = ""
            print(f"Processing PDF with {len(pdf_reader.pages)} pages")
            
            for i, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                text += page_text
                print(f"Page {i+1} extracted text length: {len(page_text)} characters")
            
            if not text.strip():
                print("Warning: No text extracted from PDF")
                return None
                
            print(f"Total extracted text length: {len(text)} characters")
            print("First 500 characters of extracted text:", text[:500])
            return text
            
        except Exception as e:
            print(f"Error extracting PDF text: {str(e)}")
            return None

    def create_sales_prompt(self, company_info: dict) -> str:
        try:
            print("Creating sales prompt from structured info:", json.dumps(company_info, indent=2))
            
            prompt = f"""You are an AI sales agent for {company_info['company_name']}. 
You've already introduced yourself at the start of the call, so don't introduce yourself again. And Don't say Hello or Hi etc..
Your role is to understand client needs and guide them toward our solutions.

Available Services:
{self._format_services(company_info['services'])}

Industries We Serve: {', '.join(company_info['industries_served'])}

Key Points:
{self._format_points(company_info['unique_selling_points'])}

Objectives:
- Must Gather client information(E-mail,Name,Company name)
- Understand requirements
- Match with services
- Must try to Schedule consultation
- Must not talk about prices unless asked for it by the user.    

Conversation Flow:
- Focus on understanding client's business and challenges
- Present relevant solutions
- Schedule consultation meeting

Strict Guidelines:
- Keep responses under 3 sentences
- Focus on business challenges
- Guide toward consultation
- No technical details unless asked
- Persuade client and pitch your services, even if the client shows disinterest

After each response, include entity tracking in this format:
[[ENTITIES]]
{{
    "entities": {{
        "name": "identified name or null",
        "email": "identified email or null",
        "company_name": "identified company or null",
        "requirements": ["requirement1", "requirement2"],
        "meeting_date": "identified date or null",
        "meeting_time": "identified time or null",
        "industry": "identified industry or null"
    }}
}}
Consider today's date as {datetime.now().strftime("%d-%m-%Y")} and time as {datetime.now().strftime("%I:%M %p")}.
If user not specified date but say "Tomorrow", "Day After Tomorrow", "Next <DAY_NAME>", "This <DAY_NAME>" then set date according from Today's date ({datetime.now()}) and save in "DD-MM-YYYY" Format."""

            print("Generated prompt:", prompt)
            return prompt
            
        except Exception as e:
            print(f"Error creating sales prompt: {str(e)}")
            return None

    def _format_services(self, services):
        return "\n".join([f"- {service['name']}: {service['description']}" for service in services])

    def _format_points(self, points):
        return "\n".join([f"- {point}" for point in points])

    def structure_company_info(self, pdf_text: str) -> Optional[dict]:
        """Structure PDF content into company information"""
        try:
            print("Structuring company information from PDF text")
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract company information from the given text. "
                            "Respond in the following JSON structure without any additional text or explanation:\n"
                            "{"
                            "\"company_name\": \"\", "
                            "\"company_description\": \"\", "
                            "\"services\": [{\"name\": \"\", \"description\": \"\", \"pricing\": \"\"}], "
                            "\"industries_served\": [\"\"], "
                            "\"unique_selling_points\": [\"\"]"
                            "}"
                        )
                    },
                    {"role": "user", "content": pdf_text},
                ],
                temperature=0.7
            )

            raw_content = response.choices[0].message.content.strip()
            print("Raw API Response:", raw_content)

            try:
                structured_info = json.loads(raw_content)
                print("Structured company info:", json.dumps(structured_info, indent=2))
                return structured_info
            except json.JSONDecodeError:
                print("Failed to parse JSON from API response")
                return None

        except Exception as e:
            print(f"Error structuring company info: {str(e)}")
            return None

class AI_SalesAgent:
    def __init__(self, system_prompt=None):
        self.system_prompt = system_prompt or current_sales_prompt
        print(f"Initializing AI agent with prompt: {self.system_prompt[:200]}...")
        
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.elevenlabs_api_key = ELEVEN_LABS_API_KEY
        self.conversation_history = [{"role": "system", "content": self.system_prompt}]
        self.client_entities = {
            "name": None, "email": None, "company_name": None,
            "requirements": [], "meeting_date": None,
            "meeting_time": None, "industry": None
        }
        self.end_call_detected = False
        self.end_call_confirmed = False
        
        # Initialize RAG components
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')
        self.documents = []
        self.embeddings = []
        self.sources = []
        self.page_numbers = []

    def check_for_end_call(self, text: str) -> bool:
        """Check if the input contains any end call phrases"""
        return any(phrase.lower() in text.lower() for phrase in END_CALL_PHRASES)

    def create_chunks(self, text: str, source: str, page_number: int, chunk_size: int = 300):
        """Create chunks from text"""
        sentences = text.split('. ')
        current_chunk = []
        current_length = 0
        
        for sentence in sentences:
            sentence = sentence.strip() + '. '
            sentence_length = len(sentence)
            
            if current_length + sentence_length > chunk_size and current_chunk:
                chunk_text = ' '.join(current_chunk)
                self.documents.append(chunk_text)
                self.sources.append(source)
                self.page_numbers.append(page_number)
                current_chunk = [sentence]
                current_length = sentence_length
            else:
                current_chunk.append(sentence)
                current_length += sentence_length
        
        if current_chunk:
            chunk_text = ' '.join(current_chunk)
            self.documents.append(chunk_text)
            self.sources.append(source)
            self.page_numbers.append(page_number)
            
        self.embeddings = self.encoder.encode(self.documents)

    def retrieve_relevant_chunks(self, query: str, k: int = 3) -> RetrievalResult:
        """Retrieve relevant chunks"""
        if not self.documents:
            return RetrievalResult([], [], [], [])
            
        query_embedding = self.encoder.encode([query])[0]
        similarities = cosine_similarity([query_embedding], self.embeddings)[0]
        
        top_k_indices = np.argsort(similarities)[-k:][::-1]
        
        return RetrievalResult(
            chunks=[self.documents[i] for i in top_k_indices],
            similarities=[similarities[i] for i in top_k_indices],
            sources=[self.sources[i] for i in top_k_indices],
            page_numbers=[self.page_numbers[i] for i in top_k_indices]
        )

    async def generate_response(self, user_input: str) -> tuple[str, bytes, bool]:
        print(f"Generating response for input: {user_input}")
        try:
            # Handle end call confirmation
            if self.end_call_detected and ("yes" in user_input.lower() or "okay" in user_input.lower() or "sure" in user_input.lower()):
                self.end_call_confirmed = True
                farewell = "Thank you for your time. Have a great day! Goodbye!"
                audio_data = generate(
                    api_key=self.elevenlabs_api_key,
                    text=farewell,
                    voice="Aria",
                    model="eleven_flash_v2_5"
                )
                return farewell, audio_data, True
            
            # Check for end call request
            if self.check_for_end_call(user_input) and not self.end_call_detected:
                self.end_call_detected = True
                confirmation_msg = "Would you like to end our conversation?"
                audio_data = generate(
                    api_key=self.elevenlabs_api_key,
                    text=confirmation_msg,
                    voice="Aria",
                    model="eleven_flash_v2_5"
                )
                return confirmation_msg, audio_data, False
            
            # Reset end_call_detected if user continues conversation
            if self.end_call_detected and ("no" in user_input.lower() or "continue" in user_input.lower()):
                self.end_call_detected = False

            # Retrieve relevant chunks using RAG
            retrieved = self.retrieve_relevant_chunks(user_input)
            context = "\n".join([f"Context {i+1}: {chunk}" 
                               for i, chunk in enumerate(retrieved.chunks)])
            
            # Add context to the conversation
            enhanced_input = f"""User Input: {user_input}

Retrieved Context:
{context}

Current Entities Tracked:
{json.dumps(self.client_entities, indent=2)}"""

            self.conversation_history.append({"role": "user", "content": enhanced_input})

            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=self.conversation_history,
                temperature=0.7,
                max_tokens=150
            )

            response_text = response.choices[0].message.content
            print(f"GPT response: {response_text}")
            
            spoken_response, entities = self.extract_entities(response_text)
            if entities:
                print("Extracted entities:", json.dumps(entities, indent=2))
                self.update_entities(entities)

            print("Generating audio response...")
            audio_data = generate(
                api_key=self.elevenlabs_api_key,
                text=spoken_response,
                voice="Aria",
                model="eleven_flash_v2_5"
            )
            print("Audio response generated successfully")

            self.conversation_history.append({"role": "assistant", "content": spoken_response})
            return spoken_response, audio_data, self.end_call_detected

        except Exception as e:
            print(f"Error generating response: {str(e)}")
            return None, None, False

    def extract_entities(self, response_text: str) -> tuple[str, Optional[dict]]:
        print("Extracting entities from response:", response_text)
        parts = response_text.split("[[ENTITIES]]")
        spoken_response = parts[0].strip()
        entities = None
        if len(parts) > 1:
            try:
                entities_text = parts[1].strip()
                entities = json.loads(entities_text)
            except Exception as e:
                print(f"Error parsing entities: {str(e)}")
        return spoken_response, entities

    def update_entities(self, entities: dict):
        print("Updating entities:", json.dumps(entities, indent=2))
        if "entities" in entities:
            entities = entities["entities"]
        for key, value in entities.items():
            if value is not None:
                self.client_entities[key] = value
        print("Updated client entities:", json.dumps(self.client_entities, indent=2))

current_sales_prompt = "You are an AI sales agent. Your role is to understand client needs and guide them toward our solutions. Please be professional and courteous."
ai_agents: Dict[str, AI_SalesAgent] = {}

@app.post("/upload_knowledge")
async def upload_knowledge(file: UploadFile = File(...)):
    print(f"Received file upload: {file.filename}")
    global current_sales_prompt
    
    try:
        content = await file.read()
        print(f"Read file content: {len(content)} bytes")
        
        pdf_processor = PDFProcessor(OPENAI_API_KEY)
        
        # Extract text from PDF
        pdf_text = pdf_processor.extract_text_from_pdf(content)
        if not pdf_text:
            print("Failed to extract text from PDF")
            return JSONResponse(
                {"status": "error", "message": "Failed to extract text from PDF"},
                status_code=400
            )
        
        # Structure the company information
        structured_info = pdf_processor.structure_company_info(pdf_text)
        if not structured_info:
            print("Failed to structure company information")
            return JSONResponse(
                {"status": "error", "message": "Failed to structure company information"},
                status_code=400
            )
        
        # Create and store sales prompt globally
        sales_prompt = pdf_processor.create_sales_prompt(structured_info)
        if not sales_prompt:
            print("Failed to create sales prompt")
            return JSONResponse(
                {"status": "error", "message": "Failed to create sales prompt"},
                status_code=400
            )
        
        current_sales_prompt = sales_prompt
        print("Successfully processed PDF and created sales prompt")
        
        # Update existing AI agents with new prompt and RAG content
        for agent in ai_agents.values():
            agent.system_prompt = current_sales_prompt
            agent.conversation_history = [{"role": "system", "content": current_sales_prompt}]
            # Process the PDF text for RAG
            agent.create_chunks(pdf_text, file.filename, 1)
        
        return JSONResponse({
            "status": "success",
            "prompt": sales_prompt
        })
        
    except Exception as e:
        print(f"Error processing upload: {str(e)}")
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    print("New WebSocket connection request")
    await websocket.accept()
    print("WebSocket connection accepted")
    
    connection_id = str(id(websocket))
    
    try:
        # Create new AI agent for this connection
        ai_agents[connection_id] = AI_SalesAgent(system_prompt=current_sales_prompt)
        print(f"Created new AI agent for connection {connection_id} with current sales prompt")

        # Send initial greeting and add to conversation history
        greeting = "Hello! I'm calling from Toshal Infotech. I'd love to discuss how our services could benefit your business. Is this a good time to talk?"
        audio_data = generate(
            api_key=ELEVEN_LABS_API_KEY,
            text=greeting,
            voice="Aria",
            model="eleven_flash_v2_5"
        )
        
        # Add greeting to conversation history
        ai_agents[connection_id].conversation_history.append({
            "role": "assistant", 
            "content": greeting
        })
        
        # Send greeting to client
        await websocket.send_json({
            "type": "ai_response",
            "text": greeting,
            "audio": base64.b64encode(audio_data).decode('utf-8') if audio_data else None,
            "end_call": False
        })

        # Main conversation loop
        while True:
            data = await websocket.receive_json()
            print(f"Received WebSocket data: {json.dumps(data, indent=2)}")
            
            ai_agent = ai_agents[connection_id]
            
            if data["action"] == "message":
                print(f"Processing message: {data['text']}")
                response_text, response_audio, end_call = await ai_agent.generate_response(data["text"])
                
                if response_text:
                    print(f"Sending response: {response_text}")
                    await websocket.send_json({
                        "type": "ai_response",
                        "text": response_text,
                        "audio": base64.b64encode(response_audio).decode('utf-8') if response_audio else None,
                        "end_call": end_call
                    })
                    
                    if end_call:
                        await websocket.close()
                        if connection_id in ai_agents:
                            del ai_agents[connection_id]
                        break

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for connection {connection_id}")
        if connection_id in ai_agents:
            del ai_agents[connection_id]
    except Exception as e:
        print(f"WebSocket error for connection {connection_id}: {str(e)}")
        if connection_id in ai_agents:
            del ai_agents[connection_id]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
