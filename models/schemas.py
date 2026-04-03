from typing import List, Optional
from pydantic import BaseModel, Field

class SubtitleResult(BaseModel):
    english_srt: str = Field(description="The complete SRT subtitle string translated into English")
    telugu_srt: str = Field(description="The complete SRT subtitle string translated into Telugu")
    odia_srt: str = Field(description="The complete SRT subtitle string translated into Odia")

class MinimalAnalysisResult(BaseModel):
    call_category: str = Field(description="One of: 'Fee Follow-up', 'Lead Inquiry', 'Technical Issue', 'General Support', 'Complaint', 'Sponsorship'")
    sentiment: str = Field(description="User's mood: 'Positive', 'Neutral', 'Frustrated', 'Angry'")
    counselor_feedback: str = Field(description="Detailed feedback on counselor's performance (2-3 sentences)")
    key_points: List[str] = Field(description="3-5 bullet points of key topics discussed")
    action_items: List[str] = Field(description="Specific tasks the counselor needs to do next")
    summary: str = Field(description="One-sentence executive summary of the call")
    willing_to_join: str = Field(description="Is the student willing to join? 'Ready to Enroll', 'Not Interested', 'Undecided', or 'Undecided (High Risk)'")
    extracted_counselor_name: Optional[str] = Field(None, description="The name of the counselor/staff member handling the call, if mentioned. If not, use 'Unknown Counselor'.")
    extracted_customer_name: Optional[str] = Field(None, description="The name of the customer/student/caller, if mentioned. If not, use 'Unknown Caller'.")

class TranscribeRequest(BaseModel):
    job_id: str
    api_key: str
    file_path: str
    original_filename: str
    source_language: str
    date_str: str
    counselor_name: str
    customer_name: str
    total_chunks: Optional[int] = None
