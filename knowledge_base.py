import json
from openai import OpenAI
import PyPDF2
import os
from config import ELEVEN_LABS_API_KEY,OPENAI_API_KEY

class PDFProcessor:
    def __init__(self, api_key):
        self.client = OpenAI(api_key=api_key)

    def extract_text_from_pdf(self, pdf_path):
        """Extract text from PDF file"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text()
                if not text.strip():  # Check if text is empty
                    raise ValueError("No text extracted from PDF")
                print("Extracted PDF Text:", text)  # Debugging log
                return text
        except Exception as e:
            print(f"Error reading PDF: {str(e)}")
            return None

    def structure_company_info(self, pdf_text):
        """Use GPT-4 to structure the PDF content into company information"""
        system_prompt = """
        Extract and structure company information from the provided text into the following JSON format:
        {
            "company_name": "string",
            "company_description": "string",
            "services": [
                {
                    "name": "string",
                    "description": "string",
                    "pricing": {
                        "package_name": "price"
                    }
                }
            ],
            "industries_served": ["string"],
            "unique_selling_points": ["string"]
        }
        Instructions:
        - Shorten all descriptions as much as possible into keywords highlighting important information and try to keep the output tokens to a minimum.
        - Focus on the most important details, leaving out non-essential or repetitive information.
        - Extract key services and their descriptions, including any pricing details if available.
        - Extract only key information that would be relevant for sales.
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Here's the company information to structure:\n\n{pdf_text}"}
                ],
                temperature=0.7
            )
            response_text = response.choices[0].message.content
            print("OpenAI API Response:", response_text)  # Debugging log

            # Validate JSON
            structured_info = json.loads(response_text)
            if not structured_info:
                raise ValueError("Empty structured information from OpenAI")
            return structured_info
        except json.JSONDecodeError as e:
            print(f"JSON Decode Error: {str(e)}")
            return None
        except Exception as e:
            print(f"Error structuring information: {str(e)}")
            return None

    def create_sales_prompt(self, structured_info):
        """Generate concise sales agent prompt from structured information"""
        prompt_template = f"""
        Create a brief sales agent system prompt using this format:
        You are an outbound sales agent for [COMPANY]. Your role is to gather Client requirements, Answer questions based on the information you have and schedule consultations.
        Services:
        [BULLETTED_SERVICES WITH Pricing]
        Industries: [INDUSTRIES]
        Objectives:
        - Must Gather client information
        - Understand requirements
        - Match with services
        - Must try to Schedule consultation
        - Must not talk about prices unless asked for it by the user.
        
        Conversation Flow:
        - Introduction and rapport building
        - Understand client's business and challenges
        - Present relevant solutions
        - Schedule consultation meeting
        Guidelines:
        - Keep responses under 3 sentences
        - Focus on business challenges
        - Guide toward consultation
        - No technical details unless asked
        After each response, track entities in this format:
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
        """

        try:
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": prompt_template},
                    {"role": "user", "content": json.dumps(structured_info, indent=2)}
                ],
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating prompt: {str(e)}")
            return None

def main():
    # Get API key from environment variable
    api_key = OPENAI_API_KEY
    if not api_key:
        print("Please set OPENAI_API_KEY environment variable")
        return

    # Initialize processor
    processor = PDFProcessor(api_key=api_key)

    # Get PDF path from user
    pdf_path = input("Enter the path to your company PDF: ")

    # Process PDF
    print("\nExtracting text from PDF...")
    pdf_text = processor.extract_text_from_pdf(pdf_path)
    if not pdf_text:
        print("Failed to extract text from PDF")
        return

    # Structure information
    print("\nStructuring company information...")
    structured_info = processor.structure_company_info(pdf_text)
    if not structured_info:
        print("Failed to structure company information")
        return

    # Save structured information
    with open("structured_company_info.json", "w") as f:
        json.dump(structured_info, f, indent=2)
    print("\nStructured company information saved to structured_company_info.json")

    # Generate sales prompt
    print("\nGenerating sales agent prompt...")
    sales_prompt = processor.create_sales_prompt(structured_info)
    if not sales_prompt:
        print("Failed to generate sales prompt")
        return

    # Save the generated prompt
    with open("sales_agent_prompt.txt", "w") as f:
        f.write(sales_prompt)
    print("\nSales agent prompt saved to sales_agent_prompt.txt")

    print("\nGenerated Prompt:")
    print(sales_prompt)

if __name__ == "__main__":
    main()