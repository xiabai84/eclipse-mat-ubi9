"""Pydantic request/response models for the MAT Analysis Service."""

from typing import Optional

from pydantic import BaseModel, Field

from config import get_settings


class AnalyzeRequest(BaseModel):
    report_path: str = Field(
        ...,
        description="Absolute path to the MAT report ZIP file inside the container",
        json_schema_extra={"example": "/reports/MyApp_Leak_Suspects.zip"},
    )
    output_dir: Optional[str] = Field(
        None,
        description="Directory for extracted files and output (defaults to temp dir)",
    )
    include_text: bool = Field(
        True, description="Include the human-readable text report in the response"
    )


class AllAnalyzeRequest(BaseModel):
    reports_dir: str = Field(
        default_factory=lambda: get_settings().reports_dir,
        description="Directory containing the MAT report ZIP files",
    )
    output_dir: Optional[str] = Field(None, description="Override output directory")
    include_text: bool = Field(True, description="Include text reports in the response")
