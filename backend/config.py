"""Centralized configuration via Pydantic BaseSettings.

All settings are loaded from environment variables with sensible defaults
that match the previous hardcoded values — zero behavior change.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Core service settings."""

    reports_dir: str = "/reports"
    heapdumps_dir: str = "/heapdumps"
    mat_script: str = "/opt/eclipse-mat/ParseHeapDump.sh"
    mat_timeout_seconds: int = 600
    max_upload_size_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GB

    log_level: str = "INFO"
    log_json: bool = False

    service_name: str = "mat-analysis"
    service_version: str = "3.1.0"

    model_config = {"env_prefix": "", "populate_by_name": True}

    def __init__(self, **kwargs):
        # Allow MAT_TIMEOUT env var as alias for mat_timeout_seconds
        if "mat_timeout_seconds" not in kwargs and "MAT_TIMEOUT" not in kwargs:
            val = os.environ.get("MAT_TIMEOUT")
            if val is not None:
                kwargs["mat_timeout_seconds"] = int(val)
        super().__init__(**kwargs)


class SuspectsThresholds(BaseSettings):
    """Thresholds for Leak Suspects analyzer."""

    primary_leak_high_mb: float = 500
    significant_suspect_mb: float = 50
    significant_suspect_ratio: float = 0.2
    secondary_leak_high_mb: float = 200
    heap_leak_critical_pct: float = 70
    heap_leak_warning_pct: float = 40

    model_config = {"env_prefix": "SUSPECTS_"}


class OverviewThresholds(BaseSettings):
    """Thresholds for System Overview analyzer."""

    large_heap_high_mb: float = 2048
    large_heap_medium_mb: float = 1024
    high_object_count: int = 1_000_000
    elevated_object_count: int = 500_000
    high_classloader_count: int = 20
    elevated_classloader_count: int = 10
    high_gc_root_count: int = 5000
    thread_leak_mb: float = 50
    thread_leak_severe_mb: float = 100
    large_array_mb: float = 100
    large_string_mb: float = 100
    large_cache_mb: float = 100

    model_config = {"env_prefix": "OVERVIEW_"}


class TopComponentsThresholds(BaseSettings):
    """Thresholds for Top Components analyzer."""

    dominant_classloader_mb: float = 200
    dominant_classloader_high_pct: float = 50
    dominant_consumer_mb: float = 500
    dominant_consumer_pct: float = 40
    large_consumer_mb: float = 100
    waste_problem_mb: float = 50
    waste_warning_mb: float = 10

    model_config = {"env_prefix": "TOP_COMPONENTS_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_suspects_thresholds() -> SuspectsThresholds:
    return SuspectsThresholds()


@lru_cache
def get_overview_thresholds() -> OverviewThresholds:
    return OverviewThresholds()


@lru_cache
def get_top_components_thresholds() -> TopComponentsThresholds:
    return TopComponentsThresholds()
