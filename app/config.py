from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    public_base_url: str = "http://localhost:8000"
    tool_secret: str = "change-me-tools"
    webhook_secret: str = "change-me-webhook"
    intake_secret: str = "change-me-intake"
    delivery_mode: str = "mock"  # mock | live

    # Ringg: one agent per job (see ringg/agents/)
    ringg_api_key: str = ""
    ringg_base_url: str = "https://prod-api.ringg.ai/ca/api/v0"
    ringg_from_number: str = ""
    ringg_agent_speed_to_lead: str = ""
    ringg_agent_concierge: str = ""
    ringg_agent_feedback: str = ""
    ringg_agent_reactivation: str = ""

    # Calling rules (IST)
    call_window_start: int = 10
    call_window_end: int = 19
    max_calls_per_day: int = 3
    max_calls_per_week: int = 6

    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 5

    # WhatsApp Cloud API
    wa_phone_number_id: str = ""
    wa_access_token: str = ""
    wa_verify_token: str = "change-me-verify"
    wa_api_version: str = "v21.0"
    wa_template_lang: str = "en"
    wa_template_brochure: str = "brochure_share"
    wa_template_details: str = "project_details"
    wa_template_visit: str = "site_visit_confirmation"
    wa_template_visit_reminder: str = "visit_reminder"
    wa_template_visit_location: str = "visit_location"
    wa_template_stop_check: str = "stop_check"
    wa_template_cost_sheet: str = "cost_sheet"
    wa_template_manager_alert: str = "manager_lead_alert"
    wa_template_nurture: str = "nurture_update"

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "Skyline Homes <sales@example.com>"
    sales_team_email: str = "sales-team@example.com"

    # Evals (Claude API)
    anthropic_api_key: str = ""
    eval_model: str = "claude-opus-5-5"

    db_path: str = "data/app.db"

    @property
    def live(self) -> bool:
        return self.delivery_mode == "live"

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p

    def agent_id(self, flow: str) -> str:
        return {
            "speed_to_lead": self.ringg_agent_speed_to_lead,
            "callback": self.ringg_agent_speed_to_lead,
            "visit_reconfirm": self.ringg_agent_concierge,
            "visit_late_check": self.ringg_agent_concierge,
            "visit_recovery": self.ringg_agent_concierge,
            "visit_reschedule": self.ringg_agent_concierge,
            "post_visit_feedback": self.ringg_agent_feedback,
            "reactivation": self.ringg_agent_reactivation,
            "nurture_callback": self.ringg_agent_reactivation,
        }.get(flow, self.ringg_agent_speed_to_lead)


@lru_cache
def get_settings() -> Settings:
    return Settings()
