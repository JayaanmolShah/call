from fastapi import FastAPI, WebSocket, File, UploadFile, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import json
import base64
from smallest import Smallest
import os
from openai import OpenAI
from typing import Any,Dict, Optional, List
import PyPDF2
import io
from datetime import datetime
from datetime import timedelta
from simple_salesforce import Salesforce
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
from concurrent.futures import ThreadPoolExecutor
import threading
import asyncio
from queue import Queue

SALESFORCE_USERNAME=os.getenv("SALESFORCE_USERNAME")
SALESFORCE_PASSWORD=os.getenv("SALESFORCE_PASSWORD")
SALESFORCE_SECURITY_TOKEN=os.getenv("SALESFORCE_SECURITY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SMALLEST_API_KEY = os.getenv("SMALLEST_API_KEY")
END_CALL_PHRASES = ["end call", "end the call", "goodbye", "good day", "bye", "quit", "stop", "hang up", 
    "end conversation", "that's all", "thank you bye", "thanks bye", "stop the call", "leave me alone", "thank you"]

app = FastAPI()

SCOPES = ['https://www.googleapis.com/auth/calendar']

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
class SalesforceIntegration:
    def __init__(self):
        try:
            self.username = SALESFORCE_USERNAME
            self.password = SALESFORCE_PASSWORD
            self.security_token = SALESFORCE_SECURITY_TOKEN
            print("Starting Salesforce initialization...")
            print(f"Username present: {bool(self.username)}")
            print(f"Password present: {bool(self.password)}")
            print(f"Security token present: {bool(self.security_token)}")
            if not all([self.username, self.password, self.security_token]):
                raise ValueError("Missing required Salesforce credentials")
            self.lead_history = []
            self.sf = Salesforce(
                username=self.username,
                password=self.password,
                security_token=self.security_token
            )
            self.initialized = True
            print("Salesforce initialized successfully")
            
        except Exception as e:
            print(f"Failed to initialize Salesforce: {str(e)}")
            self.initialized = False
    
    def create_lead(self, client_entities):
        print("\n=== Creating Salesforce Lead ===")
        if not self.initialized:
            print("❌ Cannot create lead: Salesforce not properly initialized")
            return False
            
        try:
            # Map client entities to Salesforce lead fields
            lead_data = {
                'FirstName': client_entities.get('name', '').split()[0] if client_entities.get('name') else '',
                'LastName': ' '.join(client_entities.get('name', '').split()[1:]) if client_entities.get('name') and len(client_entities.get('name', '').split()) > 1 else 'Unknown',
                'Email': client_entities.get('email', ''),
                'Company': client_entities.get('company_name', 'Unknown Company'),
                'Industry': client_entities.get('industry', 'Unknown'),
                'LeadSource': 'AI Sales Call',
                'Status': 'Open - Not Contacted',
                'Description': f"Requirements: {', '.join(client_entities.get('requirements', []))}\nMeeting scheduled for: {client_entities.get('meeting_date', 'Not set')} at {client_entities.get('meeting_time', 'Not set')}"
            }
            
            print("\n📝 Lead Data Being Sent to Salesforce:")
            for key, value in lead_data.items():
                print(f"{key}: {value}")
            
            # Attempt to create the lead
            print("\nSending data to Salesforce...")
            response = self.sf.Lead.create(lead_data)
            
            if response.get('success'):
                lead_id = response.get('id')
                print(f"✅ Lead created successfully with ID: {lead_id}")
                
                # Verify the lead was created
                try:
                    created_lead = self.sf.Lead.get(lead_id)
                    print("\n✓ Lead Verification:")
                    print(f"Lead ID: {lead_id}")
                    print(f"Name: {created_lead.get('FirstName')} {created_lead.get('LastName')}")
                    print(f"Email: {created_lead.get('Email')}")
                    print(f"Company: {created_lead.get('Company')}")
                    return True
                except Exception as e:
                    print(f"⚠️ Lead created but verification failed: {str(e)}")
                    return True
            else:
                print(f"❌ Failed to create lead: {response}")
                return False
                
        except Exception as e:
            print(f"❌ Error creating lead: {str(e)}")
            return False
        finally:
            print("=== Lead Creation Process Complete ===\n")

    def get_lead_history(self):
        """Return the history of created leads"""
        return self.lead_history
    
    def verify_lead(self, lead_id):
        """Verify a specific lead exists and return its data"""
        try:
            lead = self.sf.Lead.get(lead_id)
            return lead
        except Exception as e:
            print(f"Error verifying lead {lead_id}: {str(e)}")
            return None
CREDENTIALS_FILE = 'credentials.json'

class GoogleCalendarManager:
    def __init__(self):
        self.creds = None
        self.initialize_credentials()

    def initialize_credentials(self):
        # Load existing token if available
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                self.creds = pickle.load(token)
        
        # If credentials are not valid or don't exist, refresh or create new ones
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            # Save the credentials for future use
            with open('token.pickle', 'wb') as token:
                pickle.dump(self.creds, token)

    def create_calendar_event(self, entities: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if not entities.get('meeting_date') or not entities.get('meeting_time'):
                return {
                    "success": False,
                    "error": "Missing meeting date or time",
                    "event_link": None
                }

            print(f"Creating calendar event with entities: {json.dumps(entities, indent=2)}")
            
            service = build('calendar', 'v3', credentials=self.creds)
            
            date_str = entities['meeting_date']
            time_str = entities['meeting_time']
            
            # Try multiple time formats
            time_formats = [
                "%d-%m-%Y %I:%M %p",  # 12-hour format with AM/PM (30-01-2025 11:00 AM)
                "%d-%m-%Y %H:%M",     # 24-hour format (30-01-2025 11:00)
                "%d-%m-%Y %I:%M",     # 12-hour format without AM/PM (30-01-2025 11:00)
            ]
            
            start_datetime = None
            for format in time_formats:
                try:
                    datetime_str = f"{date_str} {time_str}"
                    start_datetime = datetime.strptime(datetime_str, format)
                    print(f"Successfully parsed datetime using format: {format}")
                    break
                except ValueError:
                    continue
            
            if not start_datetime:
                return {
                    "success": False,
                    "error": f"Could not parse date/time: {date_str} {time_str}. Expected formats: DD-MM-YYYY HH:MM AM/PM or DD-MM-YYYY HH:MM",
                    "event_link": None
                }

            event = {
                'summary': f"Sales Consultation - {entities.get('company_name', 'Potential Client')}",
                'description': f"""
                Client Details:
                Name: {entities.get('name', 'N/A')}
                Email: {entities.get('email', 'N/A')}
                Company: {entities.get('company_name', 'N/A')}
                Industry: {entities.get('industry', 'N/A')}
                Requirements: {', '.join(entities.get('requirements', ['N/A']))}
                """,
                'start': {
                    'dateTime': start_datetime.isoformat(),
                    'timeZone': 'IST',
                },
                'end': {
                    'dateTime': (start_datetime + timedelta(hours=1)).isoformat(),
                    'timeZone': 'IST',
                },
                'attendees': [
                    {'email': entities.get('email')} if entities.get('email') else None
                ],
                'reminders': {
                    'useDefault': True
                }
            }

            print(f"Attempting to create event with details: {json.dumps(event, indent=2)}")

            created_event = service.events().insert(calendarId='primary', body=event).execute()
            
            print(f"Successfully created calendar event:")
            print(f"- Event ID: {created_event.get('id')}")
            print(f"- Event Link: {created_event.get('htmlLink')}")
            print(f"- Start Time: {created_event.get('start')}")
            print(f"- End Time: {created_event.get('end')}")

            return {
                "success": True,
                "error": None,
                "event_link": created_event.get('htmlLink'),
                "event_id": created_event.get('id'),
                "event_details": created_event
            }

        except Exception as e:
            error_msg = f"Error creating calendar event: {str(e)}"
            print(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "event_link": None
            }
    
class AudioStreamManager:
    def __init__(self):
        self.current_stream: Optional[Any] = None
        self.stream_lock = asyncio.Lock()
        self.should_stop = threading.Event()
    
    async def start_new_stream(self, audio_data: bytes):
        async with self.stream_lock:
            if self.current_stream:
                self.should_stop.set()
                await asyncio.sleep(0.1)
            
            self.should_stop.clear()
            self.current_stream = audio_data
    
    async def stop_current_stream(self):
        async with self.stream_lock:
            if self.current_stream:
                self.should_stop.set()
                self.current_stream = None

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
            
            prompt = f"""You are an outbound AI sales agent for {company_info['company_name']}.
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
- Once all necessary information is gathered, confirm the date and time of the meeting, and ask if the client has any other questions or if the call can be ended.

Strict Guidelines:
- Keep responses under 3 sentences
- Focus on business challenges
- Guide toward consultation
- No technical details unless asked
- Persuade client and pitch your services, even if the client shows disinterest
- Never introduce yourself again as you've already done so
- For end call requests, ask "Would you like to end our conversation?" and only end if confirmed

Example of Final Confirmation:
"I have all the necessary information to schedule your meeting on "meeting_date" at "meeting_time". Is that perfect for you? Do you have any other questions or should I end the call?"

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
If user not specified date but say "Tommorrow", "Day After Tommorrow", "Next <DAY_NAME>", "This <DAY_NAME>" then set date according from Today's date ({datetime.now()}) and save in "DD-MM-YYYY" Format.
"""
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
        self.system_prompt = system_prompt or DEFAULT_SALES_PROMPT
        print(f"Initializing AI agent with prompt: {self.system_prompt[:200]}...")
        
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.smallestai_client = Smallest(api_key=SMALLEST_API_KEY)
        self.salesforce = SalesforceIntegration()
        self.conversation_history = [{"role": "system", "content": self.system_prompt}]
        self.end_call_detected = False
        self.end_call_confirmed = False
        self.client_entities = {
            "name": None, "email": None, "company_name": None,
            "requirements": [], "meeting_date": None,
            "meeting_time": None, "industry": None
        }
        self.audio_manager = AudioStreamManager()
        self.current_response_context = None


    def _print_current_entities(self):
        """Print current state of collected client information"""
        print("\n=== Current Client Information ===")
        for key, value in self.client_entities.items():
            print(f"{key}: {value}")
        print("===================================")

    def check_for_end_call(self, text: str) -> bool:
        return any(phrase.lower() in text.lower() for phrase in END_CALL_PHRASES)

    async def generate_response(self, user_input: str, was_interrupted: bool = False) -> tuple[str, bytes, bool]:
        print(f"Generating response for input: {user_input}")
        try:
            # Check if we're in end call confirmation mode
            if self.end_call_detected:
                print("\n=== Call End Status ===")
                print("End call detected: True")
                print("Awaiting user confirmation...")
                
                # If user confirms ending the call
                if any(word in user_input.lower() for word in ["yes", "okay", "sure", "correct", "yeah"]):
                    print("User confirmed call end")
                    self.end_call_confirmed = True
                    return await self._handle_call_ending()
                
                # If user wants to continue
                elif any(word in user_input.lower() for word in ["no", "continue", "not yet"]):
                    print("User chose to continue conversation")
                    self.end_call_detected = False
                    continue_response = "I understand you'd like to continue. What else can I help you with?"
                    audio_data = self.smallestai_client.synthesize(continue_response)
                    return continue_response, audio_data, False
            
            # Check for end call phrases in user input
            if self.check_for_end_call(user_input):
                print("\n=== End Call Phrase Detected ===")
                self.end_call_detected = True
                confirm_msg = "Would you like to end our conversation?"
                audio_data = self.smallestai_client.synthesize(confirm_msg)
                return confirm_msg, audio_data, True

            # Normal conversation flow
            self.conversation_history.append({"role": "user", "content": user_input})
            response = self.openai_client.chat.completions.create(
                model="gpt-4",
                messages=self.conversation_history,
                temperature=0.7,
                max_tokens=150
            )

            response_text = response.choices[0].message.content
            spoken_response, entities = self.extract_entities(response_text)
            
            if entities:
                print("\n=== Updating Client Information ===")
                self.update_entities(entities)
                self._print_current_entities()

            audio_data = self.smallestai_client.synthesize(spoken_response)
            self.conversation_history.append({"role": "assistant", "content": spoken_response})
            
            return response_text, audio_data, False

        except Exception as e:
            print(f"Error generating response: {str(e)}")
            error_msg = "I apologize, but I'm experiencing technical difficulties. Please try again."
            audio_data = self.smallestai_client.synthesize(error_msg)
            return error_msg, audio_data, False

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
    
    async def _handle_call_ending(self) -> tuple[str, bytes, bool]:
        print("\n====== CALL ENDING - FINAL ACTIONS ======")
        print("Starting lead creation process...")
        
        # Verify we have minimum required information
        print("\n=== Verifying Required Information ===")
        required_fields = ["name", "email", "company_name"]
        missing_fields = [field for field in required_fields if not self.client_entities.get(field)]
        
        if missing_fields:
            print(f"⚠️ Missing required fields: {', '.join(missing_fields)}")
            print("Attempting to create lead anyway...")
        else:
            print("✓ All required fields present")
        
        self._print_current_entities()
        
        # Create Salesforce lead
        print("\n=== Creating Salesforce Lead ===")
        lead_created = self.salesforce.create_lead(self.client_entities)
        print(f"Lead creation {'successful' if lead_created else 'failed'}")
        
        # Create calendar event if meeting was scheduled
        calendar_event_created = False
        if self.client_entities.get('meeting_date') and self.client_entities.get('meeting_time'):
            print("\n=== Creating Calendar Event ===")
            try:
                calendar_manager = GoogleCalendarManager()
                event_result = calendar_manager.create_calendar_event(self.client_entities)
                calendar_event_created = event_result.get('success', False)
                print(f"Calendar event creation: {'successful' if calendar_event_created else 'failed'}")
            except Exception as e:
                print(f"Error creating calendar event: {str(e)}")

        print("\n=== Preparing Farewell Message ===")
        # Construct appropriate farewell message
        if lead_created and calendar_event_created:
            farewell = "Thank you for your time. I've saved your information and scheduled our meeting. Our team will follow up with the details. Have a great day! Goodbye!"
        elif lead_created:
            farewell = "Thank you for your time. I've saved your information and our team will follow up soon. Have a great day! Goodbye!"
        else:
            farewell = "Thank you for your time. Our team will be in touch soon. Have a great day! Goodbye!"

        print("Sending farewell message and ending call...")
        print("=====================================")
        
        audio_data = self.smallestai_client.synthesize(farewell)
        return farewell, audio_data, True

DEFAULT_SALES_PROMPT = f"""DEFAULT_SALES_PROMPT = You are an AI sales agent for Toshal Infotech, a technology consulting company. 
You've already introduced yourself at the start of the call, so don't introduce yourself again. And Don't say Hello or Hi etc..
Your role is to understand client needs and guide them toward our solutions.

Available Services:
- Custom Software Development: Building tailored software solutions for businesses
- Web Development: Creating modern, responsive websites and web applications
- Mobile App Development: Developing iOS and Android applications
- Cloud Solutions: Cloud migration, hosting, and infrastructure management
- Digital Transformation: Helping businesses modernize their digital processes
- IT Consulting: Strategic technology planning and implementation

Industries We Serve: Healthcare, Finance, Education, Retail, Manufacturing, Technology

Key Points:
- Over 10 years of industry experience
- Dedicated project managers for each client
- Agile development methodology
- 24/7 support
- Competitive pricing
- Strong focus on security and scalability

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
- Once all necessary information is gathered, confirm the date and time of the meeting, and ask if the client has any other questions or if the call can be ended.

Strict Guidelines:
- Keep responses under 3 sentences
- Focus on business challenges
- Guide toward consultation
- No technical details unless asked
- Persuade client and pitch your services, even if the client shows disinterest
- Never introduce yourself again as you've already done so
- For end call requests, ask "Would you like to end our conversation?" and only end if confirmed

Example of Final Confirmation:
"I have all the necessary information to schedule your meeting on "meeting_date" at "meeting_time". Is that perfect for you? Do you have any other questions or should I end the call?"

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
If user not specified date but say "Tommorrow", "Day After Tommorrow", "Next <DAY_NAME>", "This <DAY_NAME>" then set date according from Today's date ({datetime.now()}) and save in "DD-MM-YYYY" Format."""


current_sales_prompt = DEFAULT_SALES_PROMPT
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
        
        # Update existing AI agents with new prompt
        for agent in ai_agents.values():
            agent.system_prompt = current_sales_prompt
            agent.conversation_history = [{"role": "system", "content": current_sales_prompt}]
        
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

@app.post("/test/create_lead")
async def test_create_lead():
    test_data = {
        "name": "Test User",
        "email": "test@example.com",
        "company_name": "Test Company",
        "industry": "Technology",
        "requirements": ["Web Development", "Mobile App"],
        "meeting_date": "2024-01-30",
        "meeting_time": "14:00"
    }
    
    agent = AI_SalesAgent()
    agent.client_entities = test_data
    success = agent.salesforce.create_lead(test_data)
    
    return {
        "success": success,
        "lead_data": test_data,
        "history": agent.salesforce.get_lead_history()
    }

@app.get("/test/leads")
async def get_test_leads():
    if not ai_agents:
        return {"message": "No active agents"}
    
    agent = next(iter(ai_agents.values()))
    return {
        "lead_history": agent.salesforce.get_lead_history()
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    print("New WebSocket connection request")
    await websocket.accept()
    print("WebSocket connection accepted")
    
    connection_id = str(id(websocket))
    calendar_manager = GoogleCalendarManager()
    
    try:
        ai_agents[connection_id] = AI_SalesAgent(system_prompt=current_sales_prompt)
        print(f"Created new AI agent for connection {connection_id} with current sales prompt")
        agent = ai_agents[connection_id]

        while True:
            data = await websocket.receive_json()
            print(f"Received WebSocket data: {json.dumps(data, indent=2)}")
            
            if data["action"] == "start_recording":
                greeting = "Hello! I'm calling from Toshal Infotech. I'd love to discuss how our services could benefit your business. Is this a good time to talk?"
                audio_data = agent.smallestai_client.synthesize(greeting)
                
                await websocket.send_json({
                    "type": "ai_response",
                    "text": greeting,
                    "audio": base64.b64encode(audio_data).decode('utf-8') if audio_data else None
                })
            
            elif data["action"] == "user_speaking":
                print("User speaking detected, handling interruption...")
                if agent.current_response_context:
                    response_text, response_audio, end_call = await agent.handle_interruption(data.get("text", ""))
                    
                    if response_text:
                        await websocket.send_json({
                            "type": "ai_response",
                            "text": response_text,
                            "audio": base64.b64encode(response_audio).decode('utf-8') if response_audio else None,
                            "end_call": end_call
                        })
            
            elif data["action"] == "message":
                print(f"Processing message: {data['text']}")
                
                # Check for end call phrases
                if agent.check_for_end_call(data["text"]) and not agent.end_call_detected:
                    print("\n=== End Call Phrase Detected ===")
                    agent.end_call_detected = True
                    confirm_msg = "Would you like to end our conversation?"
                    audio_data = agent.smallestai_client.synthesize(confirm_msg)
                    
                    await websocket.send_json({
                        "type": "ai_response",
                        "text": confirm_msg,
                        "audio": base64.b64encode(audio_data).decode('utf-8'),
                        "end_call": False
                    })
                    continue

                # Handle end call confirmation
                if agent.end_call_detected:
                    user_input = data["text"].lower()
                    if any(word in user_input for word in ["yes", "okay", "sure", "correct", "yeah"]):
                        print("\n====== CALL ENDING - FINAL ACTIONS ======")
                        print("\n=== Creating Salesforce Lead ===")
                        print("Current client information:")
                        for key, value in agent.client_entities.items():
                            print(f"{key}: {value}")
                            
                        lead_created = agent.salesforce.create_lead(agent.client_entities)
                        print(f"Lead creation {'successful' if lead_created else 'failed'}")
                        
                        if agent.client_entities.get('meeting_date') and agent.client_entities.get('meeting_time'):
                            print("\n=== Creating Calendar Event ===")
                            event_result = calendar_manager.create_calendar_event(agent.client_entities)
                            print(f"Calendar event creation: {'successful' if event_result.get('success') else 'failed'}")
                        
                        farewell = "Thank you for your time. I've saved your information and our team will follow up soon. Have a great day! Goodbye!"
                        audio_data = agent.smallestai_client.synthesize(farewell)
                        
                        await websocket.send_json({
                            "type": "ai_response",
                            "text": farewell,
                            "audio": base64.b64encode(audio_data).decode('utf-8'),
                            "end_call": True
                        })
                        
                        print("=== Call Ended Successfully ===")
                        await websocket.close()
                        if connection_id in ai_agents:
                            del ai_agents[connection_id]
                        break
                    elif any(word in user_input for word in ["no", "continue", "not yet"]):
                        agent.end_call_detected = False
                        continue_msg = "I understand you'd like to continue. What else can I help you with?"
                        audio_data = agent.smallestai_client.synthesize(continue_msg)
                        
                        await websocket.send_json({
                            "type": "ai_response",
                            "text": continue_msg,
                            "audio": base64.b64encode(audio_data).decode('utf-8'),
                            "end_call": False
                        })
                        continue

                # Normal conversation flow
                response_text, response_audio, _ = await agent.generate_response(data["text"])
                
                if response_text:
                    await websocket.send_json({
                        "type": "ai_response",
                        "text": response_text,
                        "audio": base64.b64encode(response_audio).decode('utf-8') if response_audio else None,
                        "end_call": False
                    })

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for connection {connection_id}")
        if connection_id in ai_agents:
            del ai_agents[connection_id]
    except Exception as e:
        print(f"WebSocket error for connection {connection_id}: {str(e)}")
        if connection_id in ai_agents:
            del ai_agents[connection_id]

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)