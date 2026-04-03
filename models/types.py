from typing import TypedDict, Dict, List, Any

class CounselorStats(TypedDict):
    name: str
    total_calls: int
    successful_joins: int
    sentiment_counts: Dict[str, int]
    categories: Dict[str, int]
    total_feedback: List[str]
    call_history: List[Dict[str, Any]]
