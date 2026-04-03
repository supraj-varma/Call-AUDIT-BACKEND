import pytest
from models.schemas import SubtitleResult, TranscribeRequest, MinimalAnalysisResult
from models.types import CounselorStats

def test_subtitle_result_valid():
    model = SubtitleResult(
        telugu_srt="1\n00:00:01,000 --> 00:00:05,000\nHello",
        english_srt="1\n00:00:01,000 --> 00:00:05,000\nHi",
        odia_srt="1\n00:00:01,000 --> 00:00:05,000\nNamaste",
    )
    assert model.telugu_srt.startswith("1")
    assert model.english_srt == "1\n00:00:01,000 --> 00:00:05,000\nHi"

def test_transcribe_request_defaults():
    req = TranscribeRequest(
        job_id="test1234",
        api_key="key",
        file_path="/tmp/file.mp3",
        original_filename="file.mp3",
        source_language="Telugu",
        date_str="2023-10-10",
        counselor_name="Alice",
        customer_name="Bob"
    )
    assert req.total_chunks is None
    assert req.job_id == "test1234"

def test_minimal_analysis_result():
    res = MinimalAnalysisResult(
        call_category="Lead Inquiry",
        sentiment="Positive",
        counselor_feedback="Good job",
        key_points=["Point 1", "Point 2"],
        action_items=["Follow up"],
        summary="User is happy",
        willing_to_join="Ready to Enroll"
    )
    assert res.call_category == "Lead Inquiry"
    assert len(res.key_points) == 2
    assert res.extracted_counselor_name is None  # Check default
